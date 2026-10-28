"""AI-SCAN-03A (contract §6b, R6): bounded rotation + hash-chained manifest + verified pagination + the G4 cached
eligibility seam. Storage only - no engine hook, no producer enabled, no model or network call. Limits are asserted
against the frozen #227 JSON twin (rotation_prerequisite.limits). Fakes only - never the real data folder."""
import ast, json, os, socket, sys, tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()            # never the real %LOCALAPPDATA%\ZackBot
import ai_scan as A                                        # noqa: E402
import ai_scan_ledger as L                                 # noqa: E402
from ml_shadow.common import canonical_json, sha256_hex    # noqa: E402
from test_ai_scan import sig, shadow, outcome, outcome1, sim   # noqa: E402

JS = os.path.join(ROOT, 'contracts', 'ai_scan_03', 'ai_scan_03_contract.v1.json')
MIB = 1 << 20


@pytest.fixture(scope='module')
def lim():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)['rotation_prerequisite']['limits']


@pytest.fixture(scope='module')
def sigs():
    return [sig(sleeve=f'S{i}') for i in range(1000)]          # ~1.2 MiB of distinct signal rows


def files(d):
    return {n: open(os.path.join(d, n), 'rb').read() for n in sorted(os.listdir(d))}


def small_segments(d, groups, bound=MIB):
    """One sealed segment per group of rows (seal() = clean-close rotation)."""
    led = L.RotatingLedger(d, bound)
    for g in groups:
        for r in g: led.append(r)
        led.seal()
    return led


# ------------------------------------------------------------------ limits / bound
def test_limits_are_the_frozen_contract_limits(lim):
    assert (L.SEG_MIN, L.SEG_MAX, L.SEGS_PER_READ, L.MAX_SEGMENTS) == \
        (lim['min_segment_bytes'], lim['max_segment_bytes'], lim['max_segments_per_read'], lim['max_manifest_segments'])


@pytest.mark.parametrize('bad', [True, False, 1, MIB - 1, 16 * MIB + 1, '8MiB', float(8 * MIB), None, -MIB, [MIB]])
def test_segment_bound_is_a_plain_int_inside_the_frozen_range(tmp_path, bad):
    assert L.check_bound(MIB) == MIB and L.check_bound(16 * MIB) == 16 * MIB
    with pytest.raises(A.AIScanError): L.check_bound(bad)
    d = tmp_path / 'seg'
    led = L.RotatingLedger(d, bad)
    assert led.disabled == 'config_invalid' and led.counters['producer_disabled'] == 1
    assert led.eligibility.check()[1:] == (False, 'config_invalid')
    with pytest.raises(A.AIScanError, match='disabled'): led.append(sig())
    assert not d.exists()                                                     # refused before any write


# ------------------------------------------------------------------ rotation / hash chain / restart replay
def test_rotation_seals_at_the_bound_with_a_hash_chain_and_restart_replay_is_idempotent(tmp_path, sigs):
    d = str(tmp_path)
    led = L.RotatingLedger(d, MIB)
    assert led.eligibility.check()[1] is True
    out = [led.append(r) for r in sigs]
    assert out == sigs and led.counters['rotations'] == 1
    m = L.load_manifest(d)
    (e,) = m['segments']
    assert e['name'] == L.seg_name(1) and e['prev'] == L.GENESIS and e['bytes'] <= MIB and e['rows'] > 0
    assert e['bytes'] + len(canonical_json(sigs[e['rows']]).encode()) + 2 > MIB          # sealed only when full
    assert os.path.getsize(os.path.join(d, L.seg_name(2))) <= MIB and m['manifest_hash'] == L._manifest_hash(m)
    raw = files(d)
    led2 = L.RotatingLedger(d, MIB)                                            # restart replays everything again
    assert [led2.append(r) for r in sigs] == [None] * len(sigs) and files(d) == raw
    with pytest.raises(A.AIScanError, match='conflicting'):
        led2.append(sig(sleeve='S0', rules_reason='edited'))
    assert files(d) == raw and led2.counters['skipped_lines'] == 0


def test_cross_segment_replay_folds_once_and_joins_across_pages(tmp_path):
    d = str(tmp_path)
    s, s2 = sig(), sig(symbol='ethusdt', side='SHORT')
    o, o1 = outcome(s), outcome1(s)
    led = small_segments(d, [[s, s2], [shadow(s)], [o, sim(s2)], [s, o], [o1]])   # dupes in later groups are no-ops
    assert len(L.load_manifest(d)['segments']) == 4                        # the all-duplicate group never seals
    r = L.read_window(d)
    assert r['complete'] and r['pages'] == 1 and set(r['views']) == {s['signal_id'], s2['signal_id']}
    v = r['views'][s['signal_id']]
    assert v['measured'] == o1 and len(v['revisions']) == 2 and list(v['shadows']) == [shadow(s)['epoch']]
    assert L.RotatingLedger(d, MIB).append(o) is None                         # restart: still a no-op


def test_active_segment_is_excluded_until_sealed(tmp_path):
    d = str(tmp_path)
    led = L.RotatingLedger(d, MIB)
    s = sig()
    led.append(s)
    r = L.read_window(d)
    assert r['complete'] and r['views'] == {} and r['pages'] == 0              # nothing sealed yet: provably empty
    assert L.RotatingLedger(d, MIB).append(s) is None                          # the active segment is still indexed
    led.seal()
    assert set(L.read_window(d)['views']) == {s['signal_id']} and led.seal() is None


def test_a_row_larger_than_the_bound_is_refused():
    led = L.RotatingLedger.__new__(L.RotatingLedger)
    led.bound, led.disabled, led._seen, led._lock = 100, None, {}, __import__('threading').Lock()
    with pytest.raises(A.AIScanError, match='larger than segment_bound'): led.append(sig())


# ------------------------------------------------------------------ manifest full / mismatch
def _chain_entries(n):
    segs, prev = [], L.GENESIS
    for i in range(1, n + 1):
        e = dict(n=i, name=L.seg_name(i), sha256='e' * 64, bytes=10, rows=1, prev=prev)
        e['chain'] = L._chain(e)
        segs.append(e)
        prev = e['chain']
    return segs


def test_manifest_cap_is_4096_segments():
    segs = _chain_entries(L.MAX_SEGMENTS + 1)
    assert L.verify_rotation_manifest(L._new_manifest(MIB, segs[:-1]))
    with pytest.raises(A.AIScanError): L.verify_rotation_manifest(L._new_manifest(MIB, segs))


def test_manifest_full_disables_before_append_and_stays_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(L, 'MAX_SEGMENTS', 3)                                  # behaviour only; the cap value is above
    d = str(tmp_path)
    s = [sig(sleeve=f'F{i}') for i in range(5)]
    led = small_segments(d, [[s[0]], [s[1]]])
    led.append(s[2])
    end = led.seal()                                                           # sealing slot 3 leaves no room: disabled
    m = L.load_manifest(d)
    assert end == m['segments'][-1]['chain'] and led.disabled == 'manifest_full'
    assert len(m['segments']) == 3 and m['disabled'] == 'manifest_full'      # persisted marker, all rows sealed
    assert led.counters['producer_disabled'] == led.counters['manifest_full'] == 1
    assert led.eligibility.check()[1:] == (False, 'manifest_full')
    raw = files(d)
    with pytest.raises(A.AIScanError, match='disabled'): led.append(s[3])
    assert files(d) == raw and not os.path.exists(os.path.join(d, L.seg_name(4)))
    led2 = L.RotatingLedger(d, MIB)
    assert led2.disabled == 'manifest_full' and led2.eligibility.check()[1] is False
    with pytest.raises(A.AIScanError): led2.append(s[4])
    assert files(d) == raw and L.read_window(d)['complete']


def test_rotation_at_the_cap_seals_then_refuses_the_row(tmp_path, monkeypatch, sigs):
    monkeypatch.setattr(L, 'MAX_SEGMENTS', 1)
    d = str(tmp_path)
    led = L.RotatingLedger(d, MIB)
    with pytest.raises(A.AIScanError, match='manifest_full'):
        for r in sigs: led.append(r)
    n = L.load_manifest(d)['segments'][0]['rows']
    assert led.counters['manifest_full'] == 1 and len(L.read_window(d)['views']) == n


def _edit(d, fn):
    p = os.path.join(d, L.MANIFEST_NAME)
    m = json.load(open(p))
    fn(m)
    open(p, 'w').write(json.dumps(m))


@pytest.mark.parametrize('how', ['byte', 'rehash_broken_chain', 'segment', 'bound', 'garbage'])
def test_manifest_mismatch_disables_and_never_appends_on(tmp_path, how):
    d = str(tmp_path)
    s, s2 = sig(), sig(sleeve='S9')
    small_segments(d, [[s], [s2]])
    if how == 'byte': _edit(d, lambda m: m['segments'][0].update(rows=7))
    elif how == 'rehash_broken_chain':
        _edit(d, lambda m: (m['segments'][1].update(prev='f' * 64), m.update(manifest_hash=L._manifest_hash(m))))
    elif how == 'segment': open(os.path.join(d, L.seg_name(1)), 'ab').write(b'\n')
    elif how == 'garbage': open(os.path.join(d, L.MANIFEST_NAME), 'w').write('{nope')
    led = L.RotatingLedger(d, 2 * MIB if how == 'bound' else MIB)
    assert led.disabled == ('config_mismatch' if how == 'bound' else 'manifest_invalid')
    assert led.counters['producer_disabled'] == 1 and led.eligibility.check()[1] is False
    raw = files(d)
    with pytest.raises(A.AIScanError, match='disabled'): led.append(sig(sleeve='S10'))
    assert files(d) == raw
    if how != 'bound': assert L.read_window(d)['complete'] is False and L.read_window(d)['views'] == {}


def test_writer_refresh_detects_a_live_edit_and_publishes_a_new_generation(tmp_path):
    d = str(tmp_path)
    led = small_segments(d, [[sig()]])
    g = led.eligibility.check()
    assert g[1] is True and led.refresh()[0] == g[0] + 1
    open(os.path.join(d, L.seg_name(1)), 'ab').write(b'x')
    gen, ok, why = led.refresh()
    assert (ok, why, led.disabled) == (False, 'manifest_invalid', 'manifest_invalid') and gen > g[0] + 1
    with pytest.raises(A.AIScanError): led.append(sig(sleeve='S2'))


# ------------------------------------------------------------------ verified pagination
@pytest.fixture
def nine(tmp_path):
    d = str(tmp_path)
    groups = []
    for i in range(9):                                           # 9 sealed segments -> pages of 4 / 4 / 1
        groups.append([sig(sleeve=f'P{i}')])
    sigs9 = [g[0] for g in groups]
    groups[8].append(outcome(sigs9[0]))                          # an outcome in the final page joins page 1's signal
    small_segments(d, groups)
    return d, sigs9


def test_pagination_binds_count_cursor_and_end_hash_and_pages_are_continuous(nine):
    d, s9 = nine
    b = L.bind_read(d)
    segs = L.load_manifest(d)['segments']
    assert b['page_count'] == 3 and b['end_hash'] == segs[-1]['chain']
    pages = [L.read_page(d, b, i) for i in range(3)]
    assert [len(p['records']) for p in pages] == [4, 4, 2]       # <= 4 segments per read
    assert pages[0]['start'] == L.GENESIS and pages[-1]['end'] == b['end_hash']
    assert all(pages[i]['start'] == pages[i - 1]['end'] for i in (1, 2))
    r = L.fold_pages(d, b, pages)
    assert r['complete'] and len(r['views']) == 9 and r['views'][s9[0]['signal_id']]['measured'] is not None
    assert L.read_window(d) == dict(r, pages=3)


def test_missing_middle_final_reordered_or_truncated_pages_are_incomplete(nine):
    d, _ = nine
    b = L.bind_read(d)
    p = [L.read_page(d, b, i) for i in range(3)]
    for bad in ([p[0], p[2]], [p[0], p[1]], [p[1], p[0], p[2]], [p[0], None, p[2]], [], p + [p[2]],
                [p[0], dict(p[1], start='f' * 64), p[2]], [p[0], dict(p[1], manifest_hash='0' * 64), p[2]]):
        r = L.fold_pages(d, b, bad)
        assert r['complete'] is False and r['views'] == {} and r['error'], bad
    # a valid, self-consistent truncated prefix manifest written after the bind
    m = L.load_manifest(d)
    L._write_manifest(d, L._new_manifest(m['segment_bound'], m['segments'][:4]))
    with pytest.raises(A.AIScanError, match='changed since bind'): L.read_page(d, b, 2)
    r = L.fold_pages(d, b, (L.read_page(d, b, i) for i in range(3)))
    assert r['complete'] is False and r['views'] == {}
    bp = L.bind_read(d)                                          # pages of the prefix never satisfy the full binding
    assert L.fold_pages(d, b, [L.read_page(d, bp, 0)])['complete'] is False


@pytest.mark.parametrize('how', ['missing_middle', 'missing_final', 'changed', 'truncated'])
def test_a_missing_changed_or_truncated_segment_makes_the_window_incomplete(nine, how):
    d, _ = nine
    name = L.seg_name(9 if how == 'missing_final' else 5)
    p = os.path.join(d, name)
    if how.startswith('missing'): os.remove(p)
    elif how == 'changed': data = open(p, 'rb').read(); open(p, 'wb').write(data.replace(b'P4', b'P5'))
    else: data = open(p, 'rb').read(); open(p, 'wb').write(data[:-10])
    r = L.read_window(d)
    assert r['complete'] is False and r['views'] == {} and name in r['error']


def test_bounded_read_refuses_a_window_beyond_its_page_bound(nine):
    d, _ = nine
    assert L.read_window(d, max_pages=2) == dict(complete=False, views={}, error='window needs 3 pages > bound 2', pages=3)
    assert L.read_window(d, max_pages=3)['complete'] is True
    assert L.read_window(os.path.join(d, 'none'))['complete'] is False          # no manifest: not provably complete


# ------------------------------------------------------------------ Codex PR #229 review: forged binding / corruption
def _forge(b, how):
    f = dict(b, cursors=list(b['cursors']))
    if how == 'partial':                                         # Codex repro: one page, ending at page 0's end
        f.update(page_count=1, cursors=[b['cursors'][0]], end_hash=b['cursors'][0][1])
    elif how == 'page_count': f['page_count'] = 2
    elif how == 'page_count_bool': f['page_count'] = True
    elif how == 'cursor_start': f['cursors'][1] = ('f' * 64, b['cursors'][1][1])
    elif how == 'cursor_end': f['cursors'][0] = (b['cursors'][0][0], 'f' * 64)
    elif how == 'end_hash': f['end_hash'] = b['cursors'][1][1]
    elif how == 'manifest_hash': f['manifest_hash'] = 'f' * 64
    elif how == 'extra_key': f['complete'] = True
    return f


@pytest.mark.parametrize('how', ['partial', 'page_count', 'page_count_bool', 'cursor_start', 'cursor_end', 'end_hash',
                                 'manifest_hash', 'extra_key'])
def test_a_forged_binding_is_refused_by_read_page_and_fold_pages(nine, how):
    d, _ = nine
    b = L.bind_read(d)
    f = _forge(b, how)
    with pytest.raises(A.AIScanError): L.read_page(d, f, 0)
    real = [L.read_page(d, b, i) for i in range(3)]
    for pages in (real, real[:f['page_count'] if _int_ok(f['page_count']) else 1]):
        r = L.fold_pages(d, f, pages)
        assert r['complete'] is False and r['views'] == {} and r['error']
    assert L.fold_pages(d, b, real)['complete'] is True                     # the genuine binding still works


def _int_ok(v):
    return type(v) is int and 0 <= v <= 3


def test_a_same_size_sealed_edit_fails_the_writer_refresh(tmp_path):
    d = str(tmp_path)
    led = small_segments(d, [[sig(sleeve='P0')], [sig(sleeve='P9')]])
    g = led.refresh()
    assert g[1] is True
    p = os.path.join(d, L.seg_name(1))
    data = open(p, 'rb').read()
    open(p, 'wb').write(data.replace(b'P0', b'P1'))                          # same length, different sha256
    assert len(open(p, 'rb').read()) == len(data)
    gen, ok, why = led.refresh()
    assert gen > g[0] and (ok, led.disabled) == (False, 'manifest_invalid') and led.eligibility.check()[1] is False
    with pytest.raises(A.AIScanError, match='disabled'): led.append(sig(sleeve='P5'))


def _corrupt_active(d, n, junk=b'{bad}\n'):
    open(os.path.join(d, L.seg_name(n)), 'ab').write(junk)


@pytest.mark.parametrize('junk', [b'{bad}\n', b'{"kind":"sig'])            # malformed line / torn tail
def test_a_corrupt_active_segment_is_never_sealed(tmp_path, junk):
    d = str(tmp_path)
    s0, s1 = sig(sleeve='C0'), sig(sleeve='C1')
    led = small_segments(d, [[s0]])
    led.append(s1)
    _corrupt_active(d, 2, junk)
    m = L.load_manifest(d)
    with pytest.raises(A.AIScanError, match='active_corrupt'): led.seal()
    assert led.disabled == 'active_corrupt' and led.eligibility.check()[1:] == (False, 'active_corrupt')
    assert L.load_manifest(d) == m                                           # nothing sealed
    r = L.read_window(d)
    assert set(r['views']) == {s0['signal_id']}                               # the corrupt bytes never became readable
    with pytest.raises(A.AIScanError, match='disabled'): led.append(sig(sleeve='C2'))


@pytest.mark.parametrize('junk', [b'{bad}\n', b'{"kind":"sig'])
def test_a_corrupt_active_segment_disables_the_writer_on_restart(tmp_path, junk):
    d = str(tmp_path)
    s0 = sig(sleeve='C0')
    led = small_segments(d, [[s0]])
    led.append(sig(sleeve='C1'))
    _corrupt_active(d, 2, junk)
    m, raw = L.load_manifest(d), files(d)
    led2 = L.RotatingLedger(d, MIB)
    assert led2.disabled == 'active_corrupt' and led2.eligibility.check()[1] is False
    with pytest.raises(A.AIScanError, match='disabled'): led2.seal()
    with pytest.raises(A.AIScanError, match='disabled'): led2.append(sig(sleeve='C2'))
    assert L.load_manifest(d) == m and files(d) == raw
    assert set(L.read_window(d)['views']) == {s0['signal_id']}
    _corrupt_active(d, 2, b'\n')                                             # a live writer's refresh catches it too
    led.refresh()
    assert led.disabled == 'active_corrupt'


def test_a_bad_line_inside_a_hash_valid_sealed_segment_is_incomplete(tmp_path):
    d = str(tmp_path)
    s = sig()
    small_segments(d, [[s]])
    p = os.path.join(d, L.seg_name(1))
    open(p, 'ab').write(b'{bad}\n')                                           # re-sealed as if the bytes were genuine
    data = open(p, 'rb').read()
    e = dict(L.load_manifest(d)['segments'][0], sha256=__import__('hashlib').sha256(data).hexdigest(), bytes=len(data))
    e['chain'] = L._chain(e)
    L._write_manifest(d, L._new_manifest(MIB, [e]))
    r = L.read_window(d)
    assert r['complete'] is False and r['views'] == {} and 'malformed' in r['error']
    assert L.RotatingLedger(d, MIB).disabled == 'manifest_invalid'


# ------------------------------------------------------------------ G4 eligibility seam
def test_hot_path_check_is_one_cached_read_without_io_or_hashing(tmp_path, monkeypatch):
    led = small_segments(str(tmp_path), [[sig()]])
    def boom(*a, **k): raise AssertionError('I/O or hashing on the hot path')
    for mod, name in ((__import__('builtins'), 'open'), (os, 'stat'), (os.path, 'getsize'), (os.path, 'exists'),
                      (L.hashlib, 'sha256'), (L, 'load_manifest'), (L, 'sha256_hex')):
        monkeypatch.setattr(mod, name, boom)
    g = led.eligibility.check()
    assert g is led.eligibility.check() and g[1] is True and isinstance(g[0], int)
    monkeypatch.undo()
    assert led.eligibility.publish(False, 'x') == (g[0] + 1, False, 'x')
    assert L.Eligibility().check() == (0, False, 'not_verified')                # unverified = not eligible


# ------------------------------------------------------------------ isolation
def test_storage_slice_imports_no_engine_order_settings_model_or_network():
    tree = ast.parse(open(os.path.join(ROOT, 'ai_scan_ledger.py'), encoding='utf-8').read())
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
        {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert mods == {'hashlib', 'json', 'logging', 'os', 'threading', 'ai_scan', 'ml_shadow.common'}


def test_write_rotate_and_read_make_no_network_call(tmp_path, monkeypatch):
    def no_net(*a, **k): raise AssertionError('network call')
    monkeypatch.setattr(socket, 'socket', no_net)
    monkeypatch.setattr(socket, 'create_connection', no_net)
    d = str(tmp_path)
    s = sig()
    small_segments(d, [[s], [outcome(s)]])
    assert L.read_window(d)['complete']


def test_legacy_view_and_ledger_are_untouched_by_segments(tmp_path):
    s = sig()
    small_segments(L.segments_dir(tmp_path), [[s]])
    assert L.scan_view({A.FLAG: True}, tmp_path) == dict(on=True, rows=[], skipped=0, error=None)
    assert L.scan_view({}, tmp_path) == A.api_off()
    assert not os.path.exists(L.ledger_path(tmp_path))


# ------------------------------------------------------------------ L1 measurement (sizes asserted; latency printed)
def test_representative_row_sizes_fit_the_conservative_caps(capsys):
    s = sig()
    up = dict(kind='identity_upgrade', schema='ai-scan-schema-v2', signal_id=s['signal_id'], orig_record_hash='a' * 64,
              orig_record_key=list(A.record_key(outcome(s))), content_hash='b' * 64,
              code=dict(git_sha='c' * 40, code_hash='d' * 64), event_at='2026-10-20T14:00:00.000000Z',
              writer_id='w' * 32, seq=123456)
    up['record_hash'] = sha256_hex(canonical_json(up))
    rows = dict(signal=s, shadow=shadow(s), outcome=outcome(s), restatement=outcome1(s), simulated=sim(s), upgrade=up)
    size = {k: len(canonical_json(v).encode('utf-8')) + 1 for k, v in rows.items()}
    with capsys.disabled(): print('\nAI-SCAN-03A row bytes:', size)
    assert max(size.values()) < 4096 and L.SEG_MAX // max(size.values()) >= 4000
    assert L.SEGS_PER_READ * L.SEG_MAX == 64 * MIB                              # the hard per-read byte bound
