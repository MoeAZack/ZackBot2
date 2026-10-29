"""AI-SCAN-04A: the default-OFF local producer data plane (contracts/ai_scan_04, rulings C1-C4 / E1-E4 / R1-R2).
Every test named in the contract test plan is implemented here; crash boundaries are simulated by injecting a
BaseException at named fault points (or failing a durable primitive) between protocol steps, then restarting a fresh
ledger on the same folder. tmp_path only, injectable clocks / sleep - never the real data folder, no network."""
import ast, errno, hashlib, json, math, os, socket, sys, tempfile, threading, tracemalloc, types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()            # never the real %LOCALAPPDATA%\ZackBot
import ai_scan as A                                        # noqa: E402
import ai_scan_ledger as L                                 # noqa: E402
import ai_scan_dataplane as D                              # noqa: E402
from ml_shadow.common import canonical_json                # noqa: E402
from test_ai_scan import sig                               # noqa: E402

JS = os.path.join(ROOT, 'contracts', 'ai_scan_04', 'ai_scan_04_contract.v1.json')
MIB = 1 << 20


class Crash(BaseException):
    """A simulated process death: never caught by the ledger's OSError / AIScanError handlers."""


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def crash_at(point):
    def f(p):
        if p == point: raise Crash(p)
    return f


def mk(d, bound=MIB, start=True, **kw):
    led = D.ProducerLedger(str(d), bound, **kw)
    if start: led.start()
    return led


def line(r):
    return (canonical_json(r) + '\n').encode('utf-8')


def seg(d, n):
    return os.path.join(str(d), L.seg_name(n))


def read(p):
    with open(p, 'rb') as fh:
        return fh.read()


def write(p, b):
    with open(p, 'wb') as fh:
        fh.write(b)


def cp(d):
    return json.loads(read(os.path.join(str(d), D.CHECKPOINT_NAME)))


def state(d):
    return D.load_state(str(d))


def quarantined(d):
    return sorted(n for n in os.listdir(str(d)) if '.quarantine-' in n)


def build_sealed(d, groups, bound=MIB):
    """Fast fixture: write sealed segments + manifest + a committed-empty checkpoint directly (no fsync per row)."""
    os.makedirs(str(d), exist_ok=True)
    segs, prev = [], L.GENESIS
    for i, g in enumerate(groups, 1):
        data = b''.join(line(r) for r in g)
        write(seg(d, i), data)
        e = dict(n=i, name=L.seg_name(i), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), rows=len(g), prev=prev)
        e['chain'] = L._chain(e)
        segs.append(e)
        prev = e['chain']
    L._write_manifest(str(d), L._new_manifest(bound, segs))
    D.atomic_write(os.path.join(str(d), D.CHECKPOINT_NAME),
                   D.checkpoint_doc((len(groups) + 1, 0, D.EMPTY_SHA), None, 0.0, None))
    return segs


def same_size_twin(a, b):
    """Two parse-valid records with recomputed record_hash and identical canonical length."""
    assert len(line(a)) == len(line(b)) and line(a) != line(b)
    return a, b


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


# ------------------------------------------------------------------ frozen numbers + plan coverage
def test_constants_equal_the_frozen_contract(c):
    rc, a = c['refresh_cadence'], c['active_segment']
    assert (D.TICK_PERIOD_S, D.SEALED_PER_TICK, D.ELIGIBILITY_MAX_AGE_S) == \
        (rc['tick_period_s'], rc['sealed_segments_per_tick'], rc['eligibility_max_age_s'])
    assert (D.MAX_ACTIVE_ROWS, D.MAX_ACTIVE_AGE_S, D.MAX_AUTO_RECOVERIES, D.RECOVERY_WINDOW_S) == \
        (a['max_active_rows'], a['max_active_age_s'], a['max_auto_recoveries'], a['recovery_window_s'])
    assert D.READ_CAP_BPS == rc['startup_io']['read_cap_bytes_per_s']
    assert D.SEAL_HASH_BOUND == rc['max_bytes_per_seal_refresh'] == a['seal_rotation']['advance_proof']['hash_bound_bytes']
    assert D.EMPTY_SHA == a['seal_rotation']['empty_sha256']
    assert set(c['counters_added']) <= set(D.COUNTERS) and set(c['counters_added']) <= set(mk_counters())


def mk_counters():
    return D.ProducerLedger(tempfile.mkdtemp(), MIB).counters


def test_every_contract_test_plan_name_is_implemented_here(c):
    here = {n for n, v in globals().items() if n.startswith('test_') and callable(v)}
    missing = [t['name'] for t in c['test_plan'] if t['name'] not in here]
    assert missing == []


# ------------------------------------------------------------------ (a) refresh tick
def test_refresh_tick_verifies_at_most_sixteen_sealed_segments(tmp_path):
    build_sealed(tmp_path, [[sig(sleeve=f'T{i}')] for i in range(20)])
    led = mk(tmp_path)
    seen, orig = [], led._verify_entry
    led._verify_entry = lambda e, index=False: (seen.append(e['n']), orig(e, index))[1]
    reads, rl = [], D._read_limited
    D._read_limited, restore = (lambda p, lim: (reads.append((os.path.basename(p), lim)), rl(p, lim))[1]), rl
    try:
        assert led.tick()[1] is True
        assert seen == list(range(1, 17))                                   # 16 sealed, sequential from the cursor
        active = [r for r in reads if r[0] == L.seg_name(21)]
        assert len(active) == 1 and active[0][1] <= L.SEG_MAX                # plus the active segment (<= 16 MiB)
        seen.clear()
        led.tick()
        assert seen == [17, 18, 19, 20] + list(range(1, 13))                # the cursor wraps to segment 1
        assert led.counters['sweeps_completed'] == 1 and led.counters['refresh_ticks'] == 2
    finally:
        D._read_limited = restore
    assert sum(e['bytes'] for e in L.load_manifest(str(tmp_path))['segments'][:16]) <= 16 * L.SEG_MAX


def test_refresh_tick_holds_one_segment_buffer_at_a_time(tmp_path):
    rows = 40
    build_sealed(tmp_path, [[sig(sleeve=f'M{i}-{j}') for j in range(rows)] for i in range(16)])
    led = mk(tmp_path)
    depth, peak_depth, orig = [0], [0], led._verify_entry

    def wrapped(e, index=False):
        depth[0] += 1
        peak_depth[0] = max(peak_depth[0], depth[0])
        try:
            out = orig(e, index)
        finally:
            depth[0] -= 1
        assert isinstance(out, int)                                          # nothing (no buffer) is handed back
        return out
    led._verify_entry = wrapped
    e1 = L.load_manifest(str(tmp_path))['segments'][0]
    tracemalloc.start()
    try:
        orig(e1)
        one = tracemalloc.get_traced_memory()[1]
        tracemalloc.reset_peak()
        assert led.tick()[1] is True
        tick_peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak_depth[0] == 1
    assert tick_peak < 3 * one                                               # ~one segment, never 16 held at once


@pytest.mark.parametrize('k', [1, 17, 40])
def test_full_sweep_covers_every_sealed_segment_within_bound(tmp_path, k):
    n = 40
    build_sealed(tmp_path, [[sig(sleeve=f'W{i:02d}')] for i in range(n)])
    led = mk(tmp_path)
    p = seg(tmp_path, k)
    data = read(p)
    write(p, data.replace(f'W{k - 1:02d}'.encode(), f'Z{k - 1:02d}'.encode()))                 # same size, different sha256
    assert len(read(p)) == len(data)
    ticks = 0
    while not led.disabled and ticks < 10:
        led.tick()
        ticks += 1
    assert led.disabled == 'manifest_invalid' and ticks <= math.ceil(n / D.SEALED_PER_TICK)
    with pytest.raises(A.AIScanError, match='disabled'): led.append(sig(sleeve='after'))


def test_repeated_overrun_stays_fail_closed(tmp_path):
    mono = Clock(100.0)
    led = mk(tmp_path, mono=mono)
    assert led.tick()[1] is True
    led._tick_lock.acquire()                                                 # a tick still running
    try:
        for _ in range(4):
            mono.t += D.TICK_PERIOD_S
            led.tick()
        assert led.counters['refresh_overrun'] == 4
        g = led.eligibility.check()
        assert g[1:] == (False, 'eligibility_stale') and led.counters['eligibility_stale'] >= 1
    finally:
        led._tick_lock.release()
    assert led.tick()[1] is True and led.eligibility.check()[1] is True     # a completed tick re-publishes


def test_startup_verification_never_blocks_engine_or_paper(tmp_path, monkeypatch):
    build_sealed(tmp_path, [[sig(sleeve='B0')], [sig(sleeve='B1')]])

    def no_io(*a, **k): raise AssertionError('segment I/O during construction')
    monkeypatch.setattr('builtins.open', no_io)
    gate, entered, threads = threading.Event(), threading.Event(), []
    led = D.ProducerLedger(str(tmp_path), MIB, sleep=lambda s: (threads.append(threading.get_ident()), entered.set(),
                                                                 gate.wait(5)))
    monkeypatch.undo()
    assert led.eligibility.check()[1:] == (False, 'startup_verifying')
    with pytest.raises(A.AIScanError, match='startup_verifying'): led.append(sig(sleeve='early'))
    t = threading.Thread(target=led.start, daemon=True)
    t.start()
    assert entered.wait(5)                                                   # mid-verification, off this thread
    assert t.is_alive() and led.eligibility.check()[1] is False and not led.ready
    gate.set()
    t.join(5)
    assert not t.is_alive() and led.eligibility.check()[1] is True
    assert threads and threading.get_ident() not in threads


def test_seal_refresh_verifies_only_the_new_entry(tmp_path):
    build_sealed(tmp_path, [[sig(sleeve=f'R{i}')] for i in range(5)])
    led = mk(tmp_path)
    seen, orig = [], led._verify_entry
    led._verify_entry = lambda e, index=False: (seen.append(e['n']), orig(e, index))[1]
    led.append(sig(sleeve='R9'))
    led.seal()
    assert seen == [6] and led.eligibility.check()[1] is True


def test_stale_eligibility_is_refused_on_the_hot_path(tmp_path, monkeypatch):
    mono = Clock(10.0)
    led = mk(tmp_path, mono=mono)
    g = led.eligibility.check()
    assert g[1] is True

    def boom(*a, **k): raise AssertionError('I/O or hashing on the hot path')
    for mod, name in ((__import__('builtins'), 'open'), (os, 'stat'), (os.path, 'getsize'), (os.path, 'exists'),
                      (hashlib, 'sha256'), (L, 'load_manifest'), (D, '_load_json'), (D, '_read_limited')):
        monkeypatch.setattr(mod, name, boom)
    mono.t += D.ELIGIBILITY_MAX_AGE_S
    assert led.eligibility.check() == g                                       # exactly 180 s: still fresh
    mono.t += 0.001
    assert led.eligibility.check() == (g[0], False, 'eligibility_stale')
    assert led.counters['eligibility_stale'] == 1


# ------------------------------------------------------------------ (b) active segment digest + forced seals
def test_active_same_size_edit_detected_within_one_tick(tmp_path):
    led = mk(tmp_path)
    a, b = same_size_twin(sig(sleeve='E1'), sig(sleeve='E2'))
    led.append(a)
    led.append(sig(sleeve='E3'))
    assert led.tick()[1] is True
    p = seg(tmp_path, 1)
    write(p, read(p).replace(line(a), line(b)))                              # parse-valid, record_hash recomputed
    gen, ok, why = led.tick()
    assert (ok, why, led.disabled) == (False, 'active_corrupt', 'active_corrupt')
    assert led.counters['active_digest_mismatch'] == 1
    assert L.load_manifest(str(tmp_path))['disabled'] == 'active_corrupt' and len(state(tmp_path)['gaps']) == 1


def test_pre_seal_digest_mismatch_refuses_the_seal(tmp_path):
    led = mk(tmp_path)
    a, b = same_size_twin(sig(sleeve='P1'), sig(sleeve='P2'))
    led.append(a)
    write(seg(tmp_path, 1), line(b))
    with pytest.raises(A.AIScanError, match='active_corrupt'): led.seal()
    m = L.load_manifest(str(tmp_path))
    assert m['segments'] == [] and m['disabled'] == 'active_corrupt'          # the edit was never bound
    assert led.counters['active_digest_mismatch'] == 1 and led.eligibility.check()[1] is False


@pytest.mark.parametrize('cap', ['bytes', 'rows', 'age'])
def test_forced_seal_at_bytes_rows_and_age(tmp_path, monkeypatch, cap):
    clock = Clock()
    rows = [sig(sleeve=f'F{i}') for i in range(4)]
    if cap == 'bytes':
        monkeypatch.setattr(L, 'SEG_MIN', 1000)
        led = mk(tmp_path, bound=2 * len(line(rows[0])) + 10, clock=clock)
    elif cap == 'rows':
        led = mk(tmp_path, clock=clock, max_active_rows=3)
    else:
        led = mk(tmp_path, clock=clock)
    for r in rows[:3]: led.append(r)
    if cap == 'age':
        clock.t += D.MAX_ACTIVE_AGE_S
        led.tick()
        assert led.counters['forced_seals'] == 0                             # not yet older than 6 h
        clock.t += 1
        led.tick()
    else:
        led.append(rows[3])
    segs = L.load_manifest(str(tmp_path))['segments']
    assert led.counters['forced_seals'] >= 1 and segs and led.disabled is None
    assert cp(tmp_path)['committed']['segment'] == len(segs) + 1
    if cap == 'rows': assert segs[0]['rows'] == 3 and read(seg(tmp_path, 2)) == line(rows[3])


# ------------------------------------------------------------------ C1: two-phase checkpoint transaction
def _record_primitives(monkeypatch, calls):
    for name in ('_write_tmp', '_replace_retry', '_fsync_dir', '_append_bytes', '_fsync_file'):
        orig = getattr(D, name)

        def w(*a, _o=orig, _n=name):
            arg = a[0].name if _n == '_fsync_file' else a[0]
            calls.append((_n, os.path.basename(str(arg)), a[1] if _n == '_write_tmp' else None))
            return _o(*a)
        monkeypatch.setattr(D, name, w)


def test_checkpoint_atomically_replaced_after_every_fsynced_append(tmp_path, monkeypatch):
    led = mk(tmp_path)
    calls = []
    _record_primitives(monkeypatch, calls)
    r = sig(sleeve='A1')
    led.append(r)
    seq = [(n, f) for n, f, _ in calls]
    tmp = [f for n, f in seq if n == '_write_tmp']
    assert all(f.startswith(D.CHECKPOINT_NAME + '.') and f.endswith('.tmp') for f in tmp) and len(tmp) == 2
    assert [n for n, _ in seq] == ['_write_tmp', '_fsync_file', '_replace_retry', '_fsync_dir',      # intent
                                   '_append_bytes', '_fsync_file',                                    # append + fsync
                                   '_write_tmp', '_fsync_file', '_replace_retry', '_fsync_dir']       # commit
    intent, commit = (json.loads(b) for n, _, b in calls if n == '_write_tmp')
    assert intent['committed'] == dict(segment=1, byte_length=0, sha256=D.EMPTY_SHA)
    assert intent['pending'] == dict(segment=1, byte_length=len(line(r)), sha256=hashlib.sha256(line(r)).hexdigest())
    assert commit['committed'] == intent['pending'] and commit['pending'] is None
    assert cp(tmp_path) == commit                                             # replaced, never written in place


def test_crash_after_intent_before_append_accepts_committed(tmp_path):
    led = mk(tmp_path)
    a = sig(sleeve='K1')
    led.append(a)
    led._fault = crash_at('after_intent')
    with pytest.raises(Crash): led.append(sig(sleeve='K2'))
    assert cp(tmp_path)['pending'] is not None and read(seg(tmp_path, 1)) == line(a)
    led2 = mk(tmp_path)
    assert led2.counters['checkpoint_pending_discarded'] == 1 and led2.disabled is None
    assert quarantined(tmp_path) == [] and state(tmp_path)['recoveries'] == []   # budget untouched
    assert led2.counters['startup_seals'] == 1                                # committed tail sealed at start
    assert L.load_manifest(str(tmp_path))['segments'][0]['rows'] == 1
    assert led2.append(sig(sleeve='K2')) is not None and led2.eligibility.check()[1] is True


def test_crash_after_append_fsync_before_commit_promotes_pending(tmp_path):
    led = mk(tmp_path)
    a, b = sig(sleeve='Q1'), sig(sleeve='Q2')
    led.append(a)
    led._fault = crash_at('after_append')
    with pytest.raises(Crash): led.append(b)
    assert read(seg(tmp_path, 1)) == line(a) + line(b) and cp(tmp_path)['pending'] is not None
    led2 = mk(tmp_path)
    assert led2.counters['checkpoint_pending_promoted'] == 1 and led2.disabled is None
    assert quarantined(tmp_path) == [] and state(tmp_path)['recoveries'] == [] and state(tmp_path)['gaps'] == []
    e = L.load_manifest(str(tmp_path))['segments'][0]
    assert e['rows'] == 2 and read(seg(tmp_path, 1)) == line(a) + line(b)  # the complete tail is kept and sealed
    assert led2.append(b) is None                                             # indexed: a replay is a no-op


@pytest.mark.parametrize('tail', ['half_row', 'torn_junk', 'extra_byte'])
def test_torn_or_partial_append_is_active_corrupt(tmp_path, tail):
    led = mk(tmp_path)
    a, b = sig(sleeve='T1'), sig(sleeve='T2')
    led.append(a)
    led._fault = crash_at('after_intent')
    with pytest.raises(Crash): led.append(b)
    frag = {'half_row': line(b)[:len(line(b)) // 2], 'torn_junk': b'{"kind":"sig', 'extra_byte': line(b) + b'x'}[tail]
    with open(seg(tmp_path, 1), 'ab') as fh: fh.write(frag)
    bad = read(seg(tmp_path, 1))
    led2 = mk(tmp_path)
    assert led2.counters['checkpoint_mismatch'] == 1 and led2.counters['active_quarantined'] == 1
    (q,) = quarantined(tmp_path)
    assert read(os.path.join(str(tmp_path), q)) == bad                         # preserved, never truncated
    assert L.load_manifest(str(tmp_path))['segments'] == []                    # never sealed
    assert len(state(tmp_path)['gaps']) == 1 and len(state(tmp_path)['recoveries']) == 1


# ------------------------------------------------------------------ E1 / R1 / R2: seal boundary
def _to_seal_boundary(d, how):
    """Leave the folder exactly at 'manifest entry n durable, checkpoint still names n'. -> (n, rows sealed)."""
    rows = [sig(sleeve=f'S{i}') for i in range(3)]
    if how == 'forced_seal':                                  # R2: a live writer seals while appending
        led = mk(d, max_active_rows=2)
        led.append(rows[0])
        led.append(rows[1])
        led._fault = crash_at('after_seal_manifest')
        with pytest.raises(Crash): led.append(rows[2])
    elif how == 'startup_seal':                               # R2: no live writer, the pre-append state
        led = mk(d)
        led.append(rows[0])
        led.append(rows[1])
        led2 = mk(d, start=False)
        led2._fault = crash_at('after_seal_manifest')
        with pytest.raises(Crash): led2.start()
    else:                                                     # clean-close seal
        led = mk(d)
        led.append(rows[0])
        led.append(rows[1])
        led._fault = crash_at('after_seal_manifest')
        with pytest.raises(Crash): led.seal()
    m = L.load_manifest(str(d))
    assert len(m['segments']) == 1 and cp(d)['committed']['segment'] == 1 and cp(d)['pending'] is None
    return 1, rows


@pytest.mark.parametrize('how', ['forced_seal', 'startup_seal', 'close_seal'])
def test_crash_between_seal_and_checkpoint_advance(tmp_path, how):
    n, rows = _to_seal_boundary(tmp_path, how)
    led = mk(tmp_path)
    assert led.counters['checkpoint_seal_advanced'] == 1 and led.disabled is None
    assert cp(tmp_path)['committed'] == dict(segment=n + 1, byte_length=0, sha256=D.EMPTY_SHA)
    assert quarantined(tmp_path) == [] and state(tmp_path)['recoveries'] == [] and state(tmp_path)['gaps'] == []
    led.append(rows[2])
    assert read(seg(tmp_path, n + 1)) == line(rows[2])                        # the next row lands in n+1


@pytest.mark.parametrize('case', ['exact', 'next_empty', 'file_n_same_size_edit', 'next_nonempty', 'pending_non_null',
                                  'file_n_missing', 'file_n_over_hash_bound'])
def test_seal_advance_requires_exact_file_n_and_empty_next(tmp_path, monkeypatch, case):
    n, rows = _to_seal_boundary(tmp_path, 'forced_seal')
    p = seg(tmp_path, n)
    if case == 'next_empty': write(seg(tmp_path, n + 1), b'')
    elif case == 'file_n_same_size_edit': write(p, read(p).replace(b'"S0"', b'"S7"'))
    elif case == 'next_nonempty': write(seg(tmp_path, n + 1), line(rows[2]))
    elif case == 'pending_non_null':
        doc = cp(tmp_path)
        c0 = doc['committed']
        D.atomic_write(os.path.join(str(tmp_path), D.CHECKPOINT_NAME), D.checkpoint_doc(
            (n, c0['byte_length'], c0['sha256']), (n, c0['byte_length'] + 5, 'f' * 64), doc['opened_at'], None))
    elif case == 'file_n_missing': os.remove(p)
    elif case == 'file_n_over_hash_bound': monkeypatch.setattr(D, 'SEAL_HASH_BOUND', 10)
    led = mk(tmp_path)
    if case in ('exact', 'next_empty'):
        assert led.counters['checkpoint_seal_advanced'] == 1 and led.disabled is None
        assert state(tmp_path)['recoveries'] == [] and quarantined(tmp_path) == []
        return
    assert led.counters['checkpoint_seal_advanced'] == 0 and led.counters['checkpoint_mismatch'] == 1
    assert len(state(tmp_path)['gaps']) == 1 and len(state(tmp_path)['recoveries']) == 1   # E4 path + budget
    if case in ('file_n_same_size_edit', 'file_n_missing'):
        assert led.disabled == 'active_corrupt'                              # sealed n broken: full verify fails
        assert L.load_manifest(str(tmp_path))['disabled'] == 'active_corrupt'
    if case == 'next_nonempty':
        (q,) = quarantined(tmp_path)
        assert read(os.path.join(str(tmp_path), q)) == line(rows[2])


def test_forced_seal_checkpoint_advance_persist_failure_recovers_via_e1(tmp_path, monkeypatch):
    led = mk(tmp_path, max_active_rows=2)
    rows = [sig(sleeve=f'X{i}') for i in range(3)]
    led.append(rows[0])
    led.append(rows[1])
    armed, orig = [False], D._write_tmp

    def failing(tmp, data):
        if armed[0]: raise OSError(errno.EIO, 'disk gone')
        return orig(tmp, data)
    monkeypatch.setattr(D, '_write_tmp', failing)
    led._fault = lambda p: armed.__setitem__(0, armed[0] or p == 'after_seal_manifest')
    with pytest.raises(A.AIScanError, match='persist failed'): led.append(rows[2])
    assert led.disabled == 'checkpoint_persist_failed' and led.counters['checkpoint_persist_failed'] == 1
    assert led.eligibility.check()[1] is False and led._active_bytes == len(line(rows[0])) + len(line(rows[1]))
    monkeypatch.undo()
    led2 = mk(tmp_path)
    assert led2.counters['checkpoint_seal_advanced'] == 1 and led2.disabled is None
    assert quarantined(tmp_path) == [] and state(tmp_path)['recoveries'] == []
    led2.append(rows[2])
    assert read(seg(tmp_path, 2)) == line(rows[2])


@pytest.mark.parametrize('case', ['cp_names_n_plus_1_manifest_lacks_n', 'manifest_n_mismatches_cp'])
def test_seal_reverse_order_state_is_refused(tmp_path, case):
    n, rows = _to_seal_boundary(tmp_path, 'forced_seal')
    m = L.load_manifest(str(tmp_path))
    cpp = os.path.join(str(tmp_path), D.CHECKPOINT_NAME)
    if case == 'cp_names_n_plus_1_manifest_lacks_n':
        L._write_manifest(str(tmp_path), L._new_manifest(MIB, m['segments'][:-1]))
        D.atomic_write(cpp, D.checkpoint_doc((n + 1, 0, D.EMPTY_SHA), None, 0.0, None))
    else:
        c0 = cp(tmp_path)['committed']
        D.atomic_write(cpp, D.checkpoint_doc((n, c0['byte_length'], 'a' * 64), None, 0.0, None))
    file_n = read(seg(tmp_path, n))
    led = mk(tmp_path)
    assert led.counters['checkpoint_seal_advanced'] == 0 and led.counters['checkpoint_mismatch'] == 1
    assert led.trail[:3] == ['disable', 'persist_marker', 'open_gap'] and 'quarantine' in led.trail
    assert len(state(tmp_path)['gaps']) == 1 and len(state(tmp_path)['recoveries']) == 1
    if case == 'cp_names_n_plus_1_manifest_lacks_n':
        (q,) = quarantined(tmp_path)                                         # the unsealed file n is preserved
        assert read(os.path.join(str(tmp_path), q)) == file_n and L.load_manifest(str(tmp_path))['segments'] == []


# ------------------------------------------------------------------ E2 / E3 / E4
def test_orphan_checkpoint_temp_is_never_authoritative(tmp_path):
    led = mk(tmp_path)
    a = sig(sleeve='O1')
    led.append(a)
    good = cp(tmp_path)
    orphan = os.path.join(str(tmp_path), D.CHECKPOINT_NAME + '.999-1.tmp')
    D._write_tmp(orphan, json.dumps(D.checkpoint_doc((1, 0, D.EMPTY_SHA), (1, 3, 'b' * 64), 0.0, None)).encode())
    led2 = mk(tmp_path, start=False)
    assert os.path.exists(orphan)                                             # not touched before validation
    led2.start()
    assert led2.disabled is None and led2.counters['checkpoint_mismatch'] == 0 and not os.path.exists(orphan)
    assert L.load_manifest(str(tmp_path))['segments'][0]['sha256'] == good['committed']['sha256']
    # a malformed canonical checkpoint still fails closed; the (valid-looking) temp never stands in for it
    led2.append(sig(sleeve='O2'))
    D._write_tmp(orphan, json.dumps(cp(tmp_path)).encode())
    write(os.path.join(str(tmp_path), D.CHECKPOINT_NAME), b'{"schema": "garbage"')
    led3 = mk(tmp_path)
    assert led3.counters['checkpoint_mismatch'] == 1 and len(state(tmp_path)['recoveries']) == 1
    assert os.path.exists(orphan) and len(quarantined(tmp_path)) == 1        # temp untouched on the corrupt path
    mk(tmp_path)
    assert not os.path.exists(orphan)                                         # removed after a validated start


def test_one_row_per_checkpoint_transaction(tmp_path, monkeypatch):
    led = mk(tmp_path)
    calls = []
    _record_primitives(monkeypatch, calls)
    rows = [sig(sleeve=f'G{i}') for i in range(3)]
    for r in rows: led.append(r)
    docs = [json.loads(b) for n, _, b in calls if n == '_write_tmp']
    appends = [n for n, _, _ in calls if n == '_append_bytes']
    assert len(appends) == 3 and len(docs) == 6                               # intent + commit per row
    for r, intent in zip(rows, docs[0::2]):
        assert intent['pending']['byte_length'] - intent['committed']['byte_length'] == len(line(r))
    assert [n for n in dir(D.ProducerLedger) if 'batch' in n or 'many' in n or 'extend' in n] == []
    import inspect
    assert list(inspect.signature(D.ProducerLedger.append).parameters) == ['self', 'rec']


@pytest.mark.parametrize('how', ['enospc', 'partial'])
def test_partial_append_or_enospc_is_active_corrupt(tmp_path, monkeypatch, how):
    led = mk(tmp_path)
    a, b = sig(sleeve='N1'), sig(sleeve='N2')
    led.append(a)

    def failing(path, data):
        if how == 'partial':
            with open(path, 'ab') as fh: fh.write(data[:len(data) // 3])
        raise OSError(errno.ENOSPC, 'No space left on device')
    monkeypatch.setattr(D, '_append_bytes', failing)
    with pytest.raises(A.AIScanError, match='active_corrupt'): led.append(b)
    assert led.disabled == 'active_corrupt' and led.trail == ['disable', 'persist_marker', 'open_gap']
    assert L.load_manifest(str(tmp_path))['disabled'] == 'active_corrupt' and len(state(tmp_path)['gaps']) == 1
    bad = read(seg(tmp_path, 1))
    monkeypatch.undo()
    led2 = mk(tmp_path)
    (q,) = quarantined(tmp_path)
    assert read(os.path.join(str(tmp_path), q)) == bad                       # no truncation salvage
    assert len(state(tmp_path)['recoveries']) == 1 and L.load_manifest(str(tmp_path))['segments'] == []
    assert led2.disabled is None and L.load_manifest(str(tmp_path))['disabled'] is None
    assert led2.append(a) is not None                                        # quarantined rows are not re-imported


# ------------------------------------------------------------------ C3 startup I/O
def test_startup_verification_reads_at_most_the_cap_with_one_buffer(tmp_path):
    build_sealed(tmp_path, [[sig(sleeve=f'C{i}-{j}') for j in range(5)] for i in range(6)])
    total = sum(e['bytes'] for e in L.load_manifest(str(tmp_path))['segments'])
    mono, sleeps = Clock(0.0), []

    def sleep(s):
        sleeps.append(s)
        mono.t += s
    cap = 4096
    led = D.ProducerLedger(str(tmp_path), MIB, mono=mono, sleep=sleep, read_cap=cap)
    depth, peak, orig = [0], [0], led._verify_entry

    def wrapped(e, index=False):
        depth[0] += 1
        peak[0] = max(peak[0], depth[0])
        try: return orig(e, index)
        finally: depth[0] -= 1
    led._verify_entry = wrapped
    led.start()
    assert led.eligibility.check()[1] is True and peak[0] == 1
    assert total / mono.t <= cap * 1.0001                                     # never faster than the cap
    assert sleeps.count(0) == 6                                               # a yield after every segment
    assert D.ProducerLedger(str(tmp_path), MIB, read_cap=D.READ_CAP_BPS * 2).read_cap == D.READ_CAP_BPS   # refused
    for bad in (True, 1.5e6, 0, -1, '1'):
        assert D.ProducerLedger(str(tmp_path), MIB, read_cap=bad).read_cap == D.READ_CAP_BPS
    assert D.ProducerLedger(str(tmp_path), MIB, read_cap=1 << 20).read_cap == 1 << 20   # lowering is allowed


# ------------------------------------------------------------------ C4
@pytest.mark.parametrize('point', ['temp_write', 'temp_fsync', 'os_replace', 'dir_fsync'])
@pytest.mark.parametrize('phase', ['intent', 'commit'])
def test_checkpoint_persist_failure_disables_the_producer(tmp_path, monkeypatch, point, phase):
    led = mk(tmp_path)
    a = sig(sleeve='Y1')
    led.append(a)
    before = (led._active_bytes, led._digest.hexdigest(), dict(led._cp))
    n_writes, retries = [0], []
    ow, of, orp, od = D._write_tmp, D._fsync_file, os.replace, D._fsync_dir
    target = 1 if phase == 'intent' else 2

    def armed():
        return n_writes[0] == target
    monkeypatch.setattr(D, '_write_tmp', lambda t, b: (n_writes.__setitem__(0, n_writes[0] + 1),
                                                       (_ for _ in ()).throw(OSError('write')) if point == 'temp_write'
                                                       and armed() else ow(t, b))[1])
    monkeypatch.setattr(D, '_fsync_file', lambda fh: (_ for _ in ()).throw(OSError('fsync'))
                        if point == 'temp_fsync' and armed() and fh.name.endswith('.tmp') else of(fh))
    monkeypatch.setattr(D.os, 'replace', lambda s, d: (_ for _ in ()).throw(PermissionError('held'))
                        if point == 'os_replace' and armed() else orp(s, d))
    monkeypatch.setattr(D, '_sleep', lambda s: retries.append(s))
    monkeypatch.setattr(D, '_fsync_dir', lambda d: (_ for _ in ()).throw(OSError('dirfsync'))
                        if point == 'dir_fsync' and armed() else od(d))
    with pytest.raises(A.AIScanError, match='persist failed'): led.append(sig(sleeve='Y2'))
    assert led.disabled == 'checkpoint_persist_failed' and led.counters['checkpoint_persist_failed'] == 1
    assert led.eligibility.check()[1:] == (False, 'checkpoint_persist_failed')
    assert (led._active_bytes, led._digest.hexdigest(), dict(led._cp)) == before   # RAM never ahead of disk
    if point == 'os_replace': assert retries == list(D.SAVE_RETRY_S)          # after the repo's retries
    with pytest.raises(A.AIScanError, match='disabled'): led.append(sig(sleeve='Y3'))
    monkeypatch.undo()
    led2 = mk(tmp_path)                                                       # the normal recovery path
    assert led2.disabled is None and quarantined(tmp_path) == [] and state(tmp_path)['recoveries'] == []


# ------------------------------------------------------------------ startup seal / mismatch / quarantine
def test_startup_seals_a_nonempty_active_only_when_checkpoint_matches(tmp_path):
    led = mk(tmp_path)
    rows = [sig(sleeve=f'U{i}') for i in range(3)]
    led.append(rows[0])
    led.append(rows[1])
    data = read(seg(tmp_path, 1))
    led2 = mk(tmp_path)
    (e,) = L.load_manifest(str(tmp_path))['segments']
    assert led2.counters['startup_seals'] == 1 and e['rows'] == 2 and e['sha256'] == hashlib.sha256(data).hexdigest()
    led2.append(rows[2])
    assert read(seg(tmp_path, 1)) == data and read(seg(tmp_path, 2)) == line(rows[2])   # fresh segment


@pytest.mark.parametrize('how', ['missing', 'malformed', 'mismatched'])
def test_missing_malformed_or_mismatched_checkpoint_is_active_corrupt(tmp_path, how):
    led = mk(tmp_path)
    led.append(sig(sleeve='V1'))
    p = os.path.join(str(tmp_path), D.CHECKPOINT_NAME)
    if how == 'missing': os.remove(p)
    elif how == 'malformed': write(p, b'{nope')
    else: D.atomic_write(p, D.checkpoint_doc((1, 7, 'c' * 64), None, 0.0, None))
    data = read(seg(tmp_path, 1))
    led2 = mk(tmp_path)
    assert led2.counters['checkpoint_mismatch'] == 1 and led2.counters['startup_seals'] == 0
    assert led2.trail[:5] == ['disable', 'persist_marker', 'open_gap', 'quarantine', 'fresh_active']
    assert L.load_manifest(str(tmp_path))['segments'] == []                   # nothing sealed
    (q,) = quarantined(tmp_path)
    assert read(os.path.join(str(tmp_path), q)) == data and len(state(tmp_path)['gaps']) == 1


def test_restart_refuses_same_size_parse_valid_active_edit(tmp_path):
    led = mk(tmp_path)
    a, b = same_size_twin(sig(sleeve='J1'), sig(sleeve='J2'))
    led.append(a)
    led.append(sig(sleeve='J3'))
    p = seg(tmp_path, 1)
    edited = read(p).replace(line(a), line(b))
    write(p, edited)                                                          # while the writer is down
    led2 = mk(tmp_path)
    assert led2.counters['checkpoint_mismatch'] == 1 and led2.counters['startup_seals'] == 0
    assert L.load_manifest(str(tmp_path))['segments'] == []                   # refused, never sealed
    (q,) = quarantined(tmp_path)
    assert read(os.path.join(str(tmp_path), q)) == edited
    assert L.read_window(str(tmp_path))['views'] == {}


def test_corrupt_active_is_quarantined_never_sealed_or_deleted(tmp_path):
    clock = Clock()
    led = mk(tmp_path, clock=clock)
    s0 = sig(sleeve='Q0')
    led.append(s0)
    led.seal()
    opened = cp(tmp_path)['opened_at']
    led.append(sig(sleeve='Q1'))
    with open(seg(tmp_path, 2), 'ab') as fh: fh.write(b'{bad}\n')
    corrupt = read(seg(tmp_path, 2))
    clock.t += 100
    led.tick()                                                                # live detection: steps 1-3
    assert led.trail == ['disable', 'persist_marker', 'open_gap'] and os.path.exists(seg(tmp_path, 2))
    (gap,) = state(tmp_path)['gaps']
    assert gap['from_utc'] == opened and gap['to_utc'] == clock.t and gap['segment'] == 2
    assert gap['after_end'] == L.load_manifest(str(tmp_path))['segments'][0]['chain']
    clock.t += 50
    led2 = mk(tmp_path, clock=clock)                                          # steps 4-5 at the next writer start
    assert led2.trail == ['disable', 'quarantine', 'fresh_active', 'clear_marker']
    (q,) = quarantined(tmp_path)
    assert q.startswith(L.seg_name(2) + '.quarantine-') and q.endswith('Z')
    assert read(os.path.join(str(tmp_path), q)) == corrupt                    # preserved byte for byte
    assert read(seg(tmp_path, 2)) == b'' if os.path.exists(seg(tmp_path, 2)) else True
    assert len(L.load_manifest(str(tmp_path))['segments']) == 1               # never sealed
    assert set(L.read_window(str(tmp_path))['views']) == {s0['signal_id']}    # never read as data
    assert D.window_eligible(str(tmp_path), opened - 10, opened + 1) is False # crosses the durable gap
    assert D.window_eligible(str(tmp_path), opened - 100, opened - 10) is True
    assert led2.disabled is None and led2.eligibility.check()[1] is True


def test_second_corruption_in_window_stays_disabled(tmp_path):
    clock = Clock()
    led = mk(tmp_path, clock=clock)
    led.append(sig(sleeve='Z1'))
    with open(seg(tmp_path, 1), 'ab') as fh: fh.write(b'{bad}\n')
    led2 = mk(tmp_path, clock=clock)                                          # first corruption: auto-recovered
    assert led2.disabled is None and len(state(tmp_path)['recoveries']) == 1
    led2.append(sig(sleeve='Z2'))
    with open(seg(tmp_path, 1), 'ab') as fh: fh.write(b'{bad}\n')
    clock.t += 3600
    led2.tick()
    assert led2.disabled == 'active_corrupt'
    raw = sorted(os.listdir(str(tmp_path)))
    led3 = mk(tmp_path, clock=clock)                                          # second inside 24 h: refused
    assert led3.disabled == 'active_corrupt' and led3.counters['recovery_refused'] == 1
    assert led3.eligibility.check()[1] is False and len(quarantined(tmp_path)) == 1
    with pytest.raises(A.AIScanError, match='disabled'): led3.append(sig(sleeve='Z3'))
    assert sorted(os.listdir(str(tmp_path))) == raw                           # durable: nothing renamed
    assert L.load_manifest(str(tmp_path))['disabled'] == 'active_corrupt'
    clock.t += D.RECOVERY_WINDOW_S
    led4 = mk(tmp_path, clock=clock)                                          # outside the window: budget again
    assert led4.disabled is None and len(state(tmp_path)['recoveries']) == 2


# ------------------------------------------------------------------ (c) pages + default OFF + isolation
def test_pages_never_leave_the_fold(tmp_path, monkeypatch):
    build_sealed(tmp_path, [[sig(sleeve=f'PG{i}')] for i in range(9)])
    got, orig = [], L.fold_pages
    monkeypatch.setattr(L, 'fold_pages', lambda d, b, pages: (got.append(pages), orig(d, b, pages))[1])
    r = L.read_window(str(tmp_path))
    assert r['complete'] and set(r) == {'complete', 'views', 'error', 'pages'} and type(r['pages']) is int
    assert len(got) == 1 and isinstance(got[0], types.GeneratorType)         # lazy, in-process only
    src = open(os.path.join(ROOT, 'ai_scan_dataplane.py'), encoding='utf-8').read()
    assert 'read_page' not in src and 'pickle' not in src and 'queue' not in src


def test_producer_stays_default_off(tmp_path):
    for f in ('engine.py', 'app.py'):
        src = open(os.path.join(ROOT, f), encoding='utf-8').read()
        assert 'ai_scan_dataplane' not in src and 'ProducerLedger' not in src, f
    assert 'AI_SCAN=False' in open(os.path.join(ROOT, 'engine.py'), encoding='utf-8').read()
    src = open(os.path.join(ROOT, 'ai_scan_dataplane.py'), encoding='utf-8').read()
    assert 'A.FLAG' not in src and "'AI_SCAN'" not in src and 'settings' not in src.split('"""', 2)[2]
    led = D.ProducerLedger(str(tmp_path), MIB)                                # constructed only, never started
    assert led.eligibility.check()[1] is False and os.listdir(str(tmp_path)) == []


def test_data_plane_imports_no_engine_order_settings_model_or_network(tmp_path, monkeypatch):
    tree = ast.parse(open(os.path.join(ROOT, 'ai_scan_dataplane.py'), encoding='utf-8').read())
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
        {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods == {'hashlib', 'json', 'logging', 'os', 're', 'threading', 'time', 'datetime', 'ai_scan',
                    'ai_scan_ledger', 'ml_shadow.common'}

    def no_net(*a, **k): raise AssertionError('network call')
    monkeypatch.setattr(socket, 'socket', no_net)
    monkeypatch.setattr(socket, 'create_connection', no_net)
    led = mk(tmp_path)
    led.append(sig())
    led.tick()
    led.seal()
    assert L.read_window(str(tmp_path))['complete']


def test_durable_state_documents_are_hash_checked(tmp_path):
    led = mk(tmp_path)
    led.append(sig(sleeve='H1'))
    p = os.path.join(str(tmp_path), D.STATE_NAME)
    st = json.loads(read(p))
    st['recoveries'] = []
    st['gaps'] = [dict(from_utc=0, to_utc=1, segment=1, after_end=L.GENESIS, reason='x')]
    write(p, json.dumps(st).encode())                                        # hash no longer matches
    led2 = mk(tmp_path)
    assert led2.disabled == 'state_invalid' and led2.eligibility.check()[1] is False
    assert D.window_eligible(str(tmp_path), 0, 10) is False                  # fail closed
