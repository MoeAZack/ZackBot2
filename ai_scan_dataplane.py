"""AI-SCAN-04A: the default-OFF local producer data plane for the AI-SCAN-03A rotating ledger (contract
contracts/ai_scan_04, Integration rulings 6480873345 / 6483289354 (C1-C4) / 6483743382 (E1-E4) / 6484022181 (R1, R2)).
PAPER / shadow only. Storage only: nothing here is wired to an engine hook, reads the AI_SCAN flag, places / sizes /
gates an order, writes settings, calls a model or opens a socket. The producer stays OFF; enabling it needs a separate
Integration ruling.

ProducerLedger extends ai_scan_ledger.RotatingLedger with:
- a durable two-phase active checkpoint {committed, pending} (C1): intent -> append + fsync -> commit, exactly one
  canonical row per transaction (E3); each checkpoint write is temp + fsync + os.replace (PermissionError retries) +
  directory fsync, never in place;
- startup recovery that accepts exactly file == committed (discard pending) or file == pending (promote), the E1 seal
  boundary advance with the R1 file-n proof, and nothing else: any third state (torn / partial append, ENOSPC, a
  same-size edit, a missing / malformed / mismatched checkpoint, a reverse seal order) is active_corrupt (E4) ->
  durable marker + durable coverage gap -> quarantine (rename, never delete) within one automatic recovery per 24 h;
- orphan checkpoint temp files never authoritative, removed only after the canonical checkpoint validated (E2);
- forced seals on bytes / rows / age following seal-then-advance (E1); a persist failure is C4: disable + count;
- a refresh tick: 16 sealed segments sequentially from a rolling cursor (one buffer at a time) + the active segment
  against the running digest and the durable checkpoint; eligibility stamped with a monotonic clock and refused on
  the hot path once older than 180 s (one tuple read + one clock read);
- an off-thread-capable startup verification (start()) under a downward-only read cap (64 MiB/s) with a cooperative
  yield between segments; the producer is ineligible until it completes.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import ai_scan as A
import ai_scan_ledger as L
from ml_shadow.common import canonical_json, sha256_hex

log = logging.getLogger('zackbot.ai_scan')

# ------------------------------------------------------------------ frozen numbers (contracts/ai_scan_04 JSON twin)
TICK_PERIOD_S = 60
SEALED_PER_TICK = 16
ELIGIBILITY_MAX_AGE_S = 180
MAX_ACTIVE_ROWS = 10000
MAX_ACTIVE_AGE_S = 21600
MAX_AUTO_RECOVERIES = 1
RECOVERY_WINDOW_S = 86400
READ_CAP_BPS = 64 << 20                                  # startup read cap, bytes/s (C3); configurable downward only
SEAL_HASH_BOUND = 16 << 20                               # max_bytes_per_seal_refresh (R1)
EMPTY_SHA = hashlib.sha256(b'').hexdigest()
SAVE_RETRY_S = (0.05, 0.1, 0.25, 0.5)                    # same schedule as engine.SAVE_RETRY_S (engine never imported)

CHECKPOINT_NAME, STATE_NAME, TMP = 'active_checkpoint.json', 'producer_state.json', '.tmp'
CP_SCHEMA, STATE_SCHEMA = 'ai-scan-active-checkpoint-v1', 'ai-scan-producer-state-v1'
_CP_KEYS = {'schema', 'committed', 'pending', 'opened_at', 'first_row_at', 'checkpoint_hash'}
_ST_KEYS = {'schema', 'recoveries', 'gaps', 'quarantined', 'state_hash'}
_TRIPLE = ('segment', 'byte_length', 'sha256')
_SEG_RE = re.compile(r'^seg-(\d{6})\.jsonl$')

COUNTERS = ('producer_disabled', 'manifest_full', 'rotations', 'skipped_lines',            # AI-SCAN-03A
            'checkpoint_mismatch', 'refresh_ticks', 'refresh_overrun', 'sweeps_completed', 'eligibility_stale',
            'active_digest_mismatch', 'forced_seals', 'startup_seals', 'active_quarantined', 'recovery_refused',
            'checkpoint_pending_promoted', 'checkpoint_pending_discarded', 'checkpoint_persist_failed',
            'checkpoint_seal_advanced')


# ------------------------------------------------------------------ durable primitives (fault-injection seams)
def _sleep(s):
    time.sleep(s)


def _replace_retry(src, dst):
    """os.replace with the repo's PermissionError retries (engine._replace_retry / SAVE_RETRY_S)."""
    for d in SAVE_RETRY_S + (None,):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if d is None: raise
            _sleep(d)


def _fsync_file(fh):
    fh.flush()
    os.fsync(fh.fileno())


def _fsync_dir(d):
    """Directory fsync. Windows cannot open a directory for fsync (NTFS journals the rename); elsewhere a failure raises."""
    if os.name == 'nt': return
    fd = os.open(d or '.', os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_tmp(tmp, data):
    with open(tmp, 'wb') as fh:
        fh.write(data)
        _fsync_file(fh)


def atomic_write(path, obj):
    """temp write + fsync, os.replace (retried), directory fsync. Never written in place; any OSError propagates and the
    previous file is left as it was (the temp is removed)."""
    tmp = f'{path}.{os.getpid()}-{threading.get_ident()}{TMP}'
    try:
        _write_tmp(tmp, canonical_json(obj).encode('utf-8'))
        _replace_retry(tmp, path)
    except OSError:
        try: os.remove(tmp)
        except OSError: pass
        raise
    _fsync_dir(os.path.dirname(path))


def _append_bytes(path, data):
    """Step 2 of a transaction: append + fsync. A short write raises (partial append = third state, E4)."""
    with open(path, 'ab') as fh:
        if fh.write(data) != len(data): raise OSError('short write')
        _fsync_file(fh)


def _read_limited(path, limit):
    """-> the file's bytes, b'' when absent, or None when larger than `limit` (never reads more than limit + 1 bytes)."""
    try:
        with open(path, 'rb') as fh:
            data = fh.read(limit + 1)
    except FileNotFoundError:
        return b''
    return None if len(data) > limit else data


def is_orphan_temp(name):
    return name.endswith(TMP) and name.startswith(tuple(f'{b}.' for b in (CHECKPOINT_NAME, STATE_NAME, L.MANIFEST_NAME)))


def _triple(n, data):
    return (n, len(data), hashlib.sha256(data).hexdigest())


def _st(t):
    return None if t is None else dict(zip(_TRIPLE, t))


def _tt(d):
    return None if d is None else tuple(d[k] for k in _TRIPLE)


def _utc_stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _num(v):
    return type(v) in (int, float)


# ------------------------------------------------------------------ checkpoint / state documents
def checkpoint_doc(committed, pending=None, opened_at=0.0, first_row_at=None):
    d = dict(schema=CP_SCHEMA, committed=_st(committed), pending=_st(pending), opened_at=opened_at,
             first_row_at=first_row_at)
    d['checkpoint_hash'] = sha256_hex(canonical_json(d))
    return d


def _valid_triple(d):
    return (isinstance(d, dict) and set(d) == set(_TRIPLE) and L._int(d['segment']) and d['segment'] >= 1
            and L._int(d['byte_length']) and d['byte_length'] >= 0 and A._hex64(d['sha256']))


def verify_checkpoint(d):
    """-> d when it is a hash-valid canonical checkpoint; else AIScanError (a malformed canonical fails closed, E2)."""
    if not (isinstance(d, dict) and set(d) == _CP_KEYS and d['schema'] == CP_SCHEMA):
        raise A.AIScanError('not an active checkpoint')
    if d['checkpoint_hash'] != sha256_hex(canonical_json({k: v for k, v in d.items() if k != 'checkpoint_hash'})):
        raise A.AIScanError('checkpoint hash mismatch')
    c, p = d['committed'], d['pending']
    ok = _valid_triple(c) and _num(d['opened_at']) and (d['first_row_at'] is None or _num(d['first_row_at']))
    if p is not None:
        ok = ok and _valid_triple(p) and p['segment'] == c['segment'] and p['byte_length'] > c['byte_length']
    if not ok: raise A.AIScanError('checkpoint fields malformed')
    return d


def state_doc(recoveries=(), gaps=(), quarantined=()):
    d = dict(schema=STATE_SCHEMA, recoveries=list(recoveries), gaps=list(gaps), quarantined=list(quarantined))
    d['state_hash'] = sha256_hex(canonical_json(d))
    return d


def verify_state(d):
    if not (isinstance(d, dict) and set(d) == _ST_KEYS and d['schema'] == STATE_SCHEMA
            and all(isinstance(d[k], list) for k in ('recoveries', 'gaps', 'quarantined'))
            and all(_num(t) for t in d['recoveries'])):
        raise A.AIScanError('not a producer state document')
    if d['state_hash'] != sha256_hex(canonical_json({k: v for k, v in d.items() if k != 'state_hash'})):
        raise A.AIScanError('producer state hash mismatch')
    return d


def _load_json(path, verify):
    """-> verified doc, or None when absent. Unreadable / malformed -> AIScanError."""
    try:
        with open(path, 'rb') as fh:
            d = json.loads(fh.read().decode('utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError) as ex:
        raise A.AIScanError(f'{os.path.basename(path)} unreadable ({type(ex).__name__})') from ex
    return verify(d)


def load_state(seg_dir):
    return _load_json(os.path.join(str(seg_dir), STATE_NAME), verify_state)


def coverage_gaps(seg_dir):
    """The durable coverage gaps [{from_utc, to_utc, segment, after_end, reason}] (empty when no state)."""
    st = load_state(seg_dir)
    return list(st['gaps']) if st else []


def window_eligible(seg_dir, start_utc, end_utc):
    """G2: a window [start_utc, end_utc] (epoch seconds) crossing any durable coverage gap is ineligible. Fail closed: an
    unreadable state document makes every window ineligible."""
    try:
        gaps = coverage_gaps(seg_dir)
    except A.AIScanError:
        return False
    return not any(g['from_utc'] <= end_utc and g['to_utc'] >= start_utc for g in gaps)


# ------------------------------------------------------------------ eligibility with a monotonic age
class AgedEligibility(L.Eligibility):
    """G4 seam plus the 180 s age rule: publish() stamps each generation with the monotonic clock; check() is one cached
    tuple read and one clock read - no lock, disk I/O, hashing or manifest traversal. An eligible generation older than
    max_age_s is reported ineligible ('eligibility_stale', counted)."""

    def __init__(self, mono=time.monotonic, max_age_s=ELIGIBILITY_MAX_AGE_S, counters=None):
        super().__init__()
        self._mono, self.max_age_s = mono, max_age_s
        self.counters = counters if counters is not None else {'eligibility_stale': 0}
        self._stamped = (self._gen, None)

    def check(self):
        g, t = self._stamped
        if g[1] and self._mono() - t > self.max_age_s:
            self.counters['eligibility_stale'] = self.counters.get('eligibility_stale', 0) + 1
            return (g[0], False, 'eligibility_stale')
        return g

    def publish(self, ok, reason=None):
        with self._lock:
            g = (self._gen[0] + 1, ok is True, None if ok is True else str(reason or 'refused'))
            self._gen, self._stamped = g, (g, self._mono())
            return g


class _Corrupt(Exception):
    pass


# ------------------------------------------------------------------ the producer ledger (writer side, OFF by default)
class ProducerLedger(L.RotatingLedger):
    """Writer side of the AI-SCAN-04 data plane. Construction is cheap and does no segment I/O: the producer is
    published ineligible ('startup_verifying') until start() - the off-thread startup callable - completes. Nothing
    constructs this class outside tests; no engine hook exists (runtime_producer_hook is DEFERRED)."""

    def __init__(self, seg_dir, segment_bound, eligibility=None, clock=time.time, mono=time.monotonic,
                 sleep=_sleep, read_cap=None, max_active_rows=MAX_ACTIVE_ROWS, max_active_age_s=MAX_ACTIVE_AGE_S,
                 sealed_per_tick=SEALED_PER_TICK):
        self.dir, self._lock, self._tick_lock = str(seg_dir), threading.RLock(), threading.Lock()
        self.counters = {k: 0 for k in COUNTERS}
        self.eligibility = eligibility if eligibility is not None else AgedEligibility(mono, counters=self.counters)
        if isinstance(self.eligibility, AgedEligibility): self.eligibility.counters = self.counters
        self._clock, self._mono, self._sleep = clock, mono, sleep
        self.max_active_rows, self.max_active_age_s, self.sealed_per_tick = max_active_rows, max_active_age_s, \
            sealed_per_tick
        if read_cap is None or not (L._int(read_cap) and 0 < read_cap <= READ_CAP_BPS):
            if read_cap is not None: log.warning('ai_scan: startup read cap %r refused, using %d', read_cap, READ_CAP_BPS)
            read_cap = READ_CAP_BPS
        self.read_cap = read_cap
        self.disabled, self.error, self._seen, self._m, self._active_bytes = None, None, {}, None, 0
        self._cp, self._state, self._digest, self._arows = None, None, hashlib.sha256(), 0
        self._cursor, self.ready, self._started, self.trail = 0, False, False, []
        self._fault = lambda point: None                 # crash-injection seam (tests only)
        try:
            self.bound = L.check_bound(segment_bound)
        except A.AIScanError as ex:
            self.bound = None
            self._disable('config_invalid', ex)
            return
        self.eligibility.publish(False, 'startup_verifying')

    # ---------------------------------------------------------------- paths / persistence
    def _p(self, name):
        return os.path.join(self.dir, name)

    def _persist(self, name, obj):
        """C4: any failure of temp write / fsync / os.replace (after retries) / directory fsync disables the producer at
        once (counted checkpoint_persist_failed) and raises; the in-RAM state is never advanced past the durable one."""
        try:
            atomic_write(self._p(name), obj)
        except OSError as ex:
            self.counters['checkpoint_persist_failed'] += 1
            if not self.disabled: self._disable('checkpoint_persist_failed', ex)
            raise A.AIScanError(f'{name} persist failed, producer disabled ({type(ex).__name__})') from ex

    def _try_persist(self, name, obj):
        try:
            self._persist(name, obj)
            return True
        except A.AIScanError:
            return False

    def _set_cp(self, committed, pending=None, opened_at=None, first_row_at=None):
        cp = checkpoint_doc(committed, pending, self._cp['opened_at'] if opened_at is None and self._cp else
                            (opened_at if opened_at is not None else self._clock()), first_row_at)
        self._persist(CHECKPOINT_NAME, cp)
        self._cp = cp
        return cp

    def _remove_orphans(self):
        """E2: only after the canonical checkpoint validated. Temp files are never read."""
        for name in os.listdir(self.dir):
            if is_orphan_temp(name):
                try: os.remove(self._p(name))
                except OSError as ex: log.warning('ai_scan: orphan temp %s not removed (%s)', name, type(ex).__name__)

    # ---------------------------------------------------------------- disable / corrupt path
    def _disable(self, reason, ex=None):
        self.ready = False
        super()._disable(reason, ex)

    def _mark_active_corrupt(self, why):
        """Recovery steps 1-3: disable (publish ineligible), persist the manifest marker active_corrupt, open a durable
        coverage gap from the last sealed end through detection. Persist failures are counted; it stays disabled."""
        self._disable('active_corrupt', A.AIScanError(why))
        self.trail.append('disable')
        segs = self._m['segments']
        if self._m['disabled'] != 'active_corrupt':
            m = L._new_manifest(self.bound, segs, 'active_corrupt')
            if self._try_persist(L.MANIFEST_NAME, m): self._m = m
        self.trail.append('persist_marker')
        st = self._state or state_doc()
        start = self._cp['opened_at'] if self._cp else self._mtime(L.MANIFEST_NAME)
        gap = dict(from_utc=start, to_utc=self._clock(), segment=len(segs) + 1,
                   after_end=segs[-1]['chain'] if segs else L.GENESIS, reason=str(why)[:200])
        new = state_doc(st['recoveries'], st['gaps'] + [gap], st['quarantined'])
        if self._try_persist(STATE_NAME, new): self._state = new
        self.trail.append('open_gap')

    def _mtime(self, name):
        try: return os.path.getmtime(self._p(name))
        except OSError: return 0.0

    # ---------------------------------------------------------------- startup (off-thread callable)
    def start(self):
        """Startup recovery + full verification. Run it off the trading thread (e.g. threading.Thread(target=
        led.start)); the producer stays ineligible until it returns. -> the published eligibility generation."""
        with self._lock:
            if self._started or self.bound is None: return self.eligibility.check()
            self._started = True
            try:
                self._startup()
            except (A.AIScanError, OSError) as ex:
                if not self.disabled: self._disable('manifest_invalid', ex)
            return self.eligibility.check()

    def _startup(self):
        os.makedirs(self.dir, exist_ok=True)
        m = L.load_manifest(self.dir)
        if m is None:
            m = L._new_manifest(self.bound, [])
            self._persist(L.MANIFEST_NAME, m)
        if m['segment_bound'] != self.bound:
            self._disable('config_mismatch')
            return
        self._m = m
        try:
            self._state = load_state(self.dir)
        except A.AIScanError as ex:
            self._disable('state_invalid', ex)
            return
        if self._state is None:
            self._state = state_doc()
            self._persist(STATE_NAME, self._state)
        if m['disabled'] == 'manifest_full':
            self._disable('manifest_full')
            return
        recovering, active = m['disabled'] == 'active_corrupt', b''
        if recovering:                                   # marker + gap were persisted at live detection
            self._disable('active_corrupt')
            self.trail.append('disable')
        else:
            try:
                active = self._recover_checkpoint()
            except _Corrupt as ex:
                self.counters['checkpoint_mismatch'] += 1
                self._mark_active_corrupt(str(ex))
                recovering = True
        if recovering and not self._auto_recover():
            return
        self._verify_all()                               # sealed corruption -> manifest_invalid (never quarantined)
        if recovering:
            m = L._new_manifest(self.bound, self._m['segments'], None)
            self._persist(L.MANIFEST_NAME, m)
            self._m, self.disabled, self.error = m, None, None
            self.trail.append('clear_marker')
        recs, _ = L._parse_lines(active.split(b'\n'))
        self._index(recs, 0)
        self._active_bytes, self._arows, self._digest = len(active), len(recs), hashlib.sha256(active)
        if active:
            self._seal_locked('startup')
            if self.disabled: return
        self.ready = True
        self.eligibility.publish(True)

    def _recover_checkpoint(self):
        """C1 / E1 / R1 / E2 / E4 startup state model. -> the active bytes (committed state) or raises _Corrupt."""
        segs = self._m['segments']
        last, active_n = len(segs), len(segs) + 1
        try:
            cp = _load_json(self._p(CHECKPOINT_NAME), verify_checkpoint)
        except A.AIScanError as ex:
            raise _Corrupt(f'malformed canonical checkpoint ({ex})') from ex
        if cp is None:
            data = _read_limited(self._p(L.seg_name(active_n)), self.bound)
            if data != b'': raise _Corrupt('checkpoint missing for a non-empty active segment')
            self._set_cp((active_n, 0, EMPTY_SHA), opened_at=self._clock())
            self._remove_orphans()
            return b''
        self._cp = cp
        c, p = _tt(cp['committed']), _tt(cp['pending'])
        n = c[0]
        if last >= 1 and n == last:                      # E1: crash between the seal and the checkpoint advance
            if p is not None: raise _Corrupt('pending alongside sealed manifest entry n')
            e = segs[-1]
            if (e['n'], e['bytes'], e['sha256']) != c: raise _Corrupt('manifest entry n mismatches the checkpoint')
            fn = _read_limited(self._p(L.seg_name(n)), SEAL_HASH_BOUND)
            if not os.path.isfile(self._p(L.seg_name(n))) or fn is None or _triple(n, fn) != c:
                raise _Corrupt('sealed file n differs from manifest entry n (R1)')
            nxt = self._p(L.seg_name(n + 1))
            if os.path.exists(nxt) and (not os.path.isfile(nxt) or os.path.getsize(nxt) != 0):
                raise _Corrupt('file n+1 is not absent or empty (R1)')
            self._set_cp((n + 1, 0, EMPTY_SHA), opened_at=self._clock())
            self.counters['checkpoint_seal_advanced'] += 1
            self._remove_orphans()
            return b''
        if n != active_n: raise _Corrupt('checkpoint names a segment out of seal order')
        data = _read_limited(self._p(L.seg_name(active_n)), self.bound)
        if data is None: raise _Corrupt('active segment exceeds segment_bound')
        t = _triple(active_n, data)
        if t == c:
            if p is not None:
                self._set_cp(c, None, first_row_at=cp['first_row_at'] if c[1] else None)
                self.counters['checkpoint_pending_discarded'] += 1
        elif p is not None and t == p:
            self._set_cp(p, None, first_row_at=cp['first_row_at'])
            self.counters['checkpoint_pending_promoted'] += 1
        else:
            raise _Corrupt('active bytes match neither committed nor pending')
        bad = L._parse_lines(data.split(b'\n'))[1]
        if bad:
            self.counters['skipped_lines'] += bad
            raise _Corrupt(f'{bad} malformed line(s) in the active segment')
        self._remove_orphans()
        return data

    def _auto_recover(self):
        """Recovery steps 4-6 at writer start: within the budget (MAX_AUTO_RECOVERIES per RECOVERY_WINDOW_S, durable),
        rename every unsealed segment file to .quarantine-<UTC> (never deleted / truncated / sealed / read) and open a
        fresh empty active segment n. Over budget: recovery_refused, stays disabled. -> True when recovered."""
        now, st = self._clock(), self._state
        if sum(1 for t in st['recoveries'] if now - t < RECOVERY_WINDOW_S) >= MAX_AUTO_RECOVERIES:
            self.counters['recovery_refused'] += 1
            log.warning('ai_scan: active_corrupt again inside the recovery window, producer stays disabled')
            return False
        try:
            st = state_doc(st['recoveries'] + [now], st['gaps'], st['quarantined'])
            self._persist(STATE_NAME, st)
            self._state, last, moved = st, len(self._m['segments']), []
            for name in sorted(os.listdir(self.dir)):
                mm = _SEG_RE.match(name)
                if mm and int(mm.group(1)) > last:
                    q = self._quarantine_name(name, now)
                    os.rename(self._p(name), self._p(q))
                    moved.append(dict(name=q, segment=int(mm.group(1)), at=now))
                    self.counters['active_quarantined'] += 1
            self.trail.append('quarantine')
            if moved:
                st = state_doc(st['recoveries'], st['gaps'], st['quarantined'] + moved)
                self._persist(STATE_NAME, st)
                self._state = st
            self._cp = None
            self._set_cp((last + 1, 0, EMPTY_SHA), opened_at=now)
            self.trail.append('fresh_active')         # orphans stay until a later start validates normally (E2)
        except (A.AIScanError, OSError) as ex:
            log.warning('ai_scan: automatic recovery failed (%s)', type(ex).__name__)
            return False
        return True

    def _quarantine_name(self, name, now):
        q, i = f'{name}.quarantine-{_utc_stamp(now)}', 0
        while os.path.exists(self._p(q if not i else f'{q}-{i}')): i += 1
        return q if not i else f'{q}-{i}'

    def _verify_all(self):
        """Full startup verification: every sealed segment, sequentially, one buffer at a time, under the read cap with a
        cooperative yield after each segment; builds the restart dedupe index. Any mismatch raises (manifest_invalid)."""
        t0, total = self._mono(), 0
        for e in self._m['segments']:
            total += self._verify_entry(e, index=True)
            need = total / self.read_cap
            spent = self._mono() - t0
            if spent < need: self._sleep(need - spent)
            self._sleep(0)                               # cooperative yield between segments

    def _verify_entry(self, e, index=False):
        """One sealed segment against its manifest entry (bytes, sha256, rows, every line a valid record). The buffer
        is dropped before returning. -> bytes read."""
        data = _read_limited(self._p(e['name']), e['bytes'])
        if not data or len(data) != e['bytes'] or hashlib.sha256(data).hexdigest() != e['sha256']:
            raise A.AIScanError(f"{e['name']}: segment missing, changed or truncated")
        recs, bad = L._parse_lines(data.split(b'\n'))
        if bad or len(recs) != e['rows']: raise A.AIScanError(f"{e['name']}: malformed line or row count differs")
        if index: self._index(recs, 0)
        return len(data)

    # ---------------------------------------------------------------- seal (E1: seal manifest, then advance checkpoint)
    def _seal_locked(self, kind):
        """kind: 'bytes' / 'rows' / 'age' (forced), 'startup' or 'close'. Pre-seal check: the file must equal the
        running digest and the durable committed checkpoint, else active_corrupt and nothing is sealed."""
        segs = self._m['segments']
        n = len(segs) + 1
        data = _read_limited(self._p(L.seg_name(n)), self.bound)
        if (data is None or len(data) != self._active_bytes or hashlib.sha256(data).hexdigest() != self._digest.hexdigest()
                or self._cp['pending'] is not None or _tt(self._cp['committed']) != _triple(n, data)):
            self.counters['active_digest_mismatch'] += 1
            self._mark_active_corrupt('active segment differs from the running digest / checkpoint (pre-seal)')
            raise A.AIScanError('producer disabled (active_corrupt), seal refused')
        recs, bad = L._parse_lines(data.split(b'\n'))
        if bad:
            self.counters['skipped_lines'] += bad
            self._mark_active_corrupt(f'{bad} malformed line(s) in the active segment')
            raise A.AIScanError('producer disabled (active_corrupt), seal refused')
        e = dict(n=n, name=L.seg_name(n), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), rows=len(recs),
                 prev=segs[-1]['chain'] if segs else L.GENESIS)
        e['chain'] = L._chain(e)
        full = n >= L.MAX_SEGMENTS
        m = L._new_manifest(self.bound, segs + [e], 'manifest_full' if full else None)
        self._persist(L.MANIFEST_NAME, m)                # step 1: seal manifest entry n durably first
        self._m = m
        self._fault('after_seal_manifest')
        self._set_cp((n + 1, 0, EMPTY_SHA), opened_at=self._clock())   # step 2: advance to committed empty n+1
        self._active_bytes, self._arows, self._digest = 0, 0, hashlib.sha256()
        self.counters['rotations'] += 1
        if kind in ('bytes', 'rows', 'age'): self.counters['forced_seals'] += 1
        elif kind == 'startup': self.counters['startup_seals'] += 1
        if full:
            self._disable('manifest_full')
            return True
        try:                                             # seal refresh: manifest hash + the new entry only
            disk = L.load_manifest(self.dir)
            if disk is None or disk['manifest_hash'] != m['manifest_hash']: raise A.AIScanError('manifest changed')
            self._verify_entry(e)
        except (A.AIScanError, OSError) as ex:
            self._disable('manifest_invalid', ex)
            return False
        if self.ready: self.eligibility.publish(True)
        return False

    def seal(self):
        """Clean-close seal (E1 order). -> the new manifest end hash, or None when the active segment is empty."""
        with self._lock:
            if self.disabled or not self.ready: raise A.AIScanError(f'producer disabled ({self.disabled}), seal refused')
            if not self._active_bytes: return None
            self._seal_locked('close')
            return self._m['segments'][-1]['chain']

    def _rotate(self):                                   # legacy entry point -> the forced bytes seal
        return self._seal_locked('bytes')

    # ---------------------------------------------------------------- append: one row per transaction (C1, E3)
    def append(self, rec):
        """Append exactly one sealed record: persist intent {committed, pending} -> append + fsync -> commit. -> the
        record, or None for a same-hash replay. A partial append / ENOSPC is active_corrupt (E4); a checkpoint persist
        failure is C4. The running digest advances only after the commit is durable."""
        if not isinstance(rec, dict): raise A.AIScanError('a ledger row must be a sealed record')
        A._verify(rec)
        key = A.record_key(rec)
        line = (canonical_json(rec) + '\n').encode('utf-8')
        with self._lock:
            if self.disabled or not self.ready:
                raise A.AIScanError(f'producer disabled ({self.disabled or "startup_verifying"}), append refused')
            have = self._seen.get(key)
            if have is not None:
                if have != rec['record_hash']: raise A.AIScanError(f'{key}: conflicting replay of an immutable record')
                return None
            if len(line) + 1 > self.bound: raise A.AIScanError('row larger than segment_bound')
            if self._active_bytes and (self._active_bytes + len(line) + 1 > self.bound or
                                       self._arows >= self.max_active_rows):
                kind = 'bytes' if self._active_bytes + len(line) + 1 > self.bound else 'rows'
                if self._seal_locked(kind): raise A.AIScanError('producer disabled (manifest_full), append refused')
                if self.disabled: raise A.AIScanError(f'producer disabled ({self.disabled}), append refused')
            n = len(self._m['segments']) + 1
            committed = _tt(self._cp['committed'])
            nd = self._digest.copy()
            nd.update(line)
            pending = (n, self._active_bytes + len(line), nd.hexdigest())
            first = self._cp['first_row_at'] if self._active_bytes else self._clock()
            self._persist(CHECKPOINT_NAME, checkpoint_doc(committed, pending, self._cp['opened_at'], first))   # 1
            self._fault('after_intent')
            try:
                _append_bytes(self._p(L.seg_name(n)), line)                                                   # 2
            except OSError as ex:
                self._mark_active_corrupt(f'partial append or ENOSPC ({type(ex).__name__}: {ex})'[:200])
                raise A.AIScanError('producer disabled (active_corrupt), append failed') from ex
            self._fault('after_append')
            cp = checkpoint_doc(pending, None, self._cp['opened_at'], first)
            self._persist(CHECKPOINT_NAME, cp)                                                                # 3
            self._cp, self._digest, self._active_bytes = cp, nd, pending[1]
            self._arows += 1
            self._seen[key] = rec['record_hash']
        return rec

    # ---------------------------------------------------------------- refresh tick (writer thread, every 60 s)
    def tick(self):
        """One refresh tick. Never overlaps: a tick still running makes this one skip (refresh_overrun). Verifies the
        manifest hash, the next sealed_per_tick sealed entries at the rolling cursor sequentially (wrapping to segment
        1), then the active segment against the running digest and the durable checkpoint; seals an over-age active
        segment; publishes a new stamped generation. -> the eligibility generation."""
        if not self._tick_lock.acquire(blocking=False):
            self.counters['refresh_overrun'] += 1
            return self.eligibility.check()
        try:
            with self._lock:
                if self.disabled or not self.ready: return self.eligibility.check()
                self.counters['refresh_ticks'] += 1
                try:
                    disk = L.load_manifest(self.dir)
                    if disk is None or disk['manifest_hash'] != self._m['manifest_hash']:
                        raise A.AIScanError('manifest changed')
                    segs = self._m['segments']
                    for _ in range(min(self.sealed_per_tick, len(segs))):
                        if self._cursor >= len(segs): self._cursor = 0
                        self._verify_entry(segs[self._cursor])
                        self._cursor += 1
                        if self._cursor >= len(segs):
                            self._cursor = 0
                            self.counters['sweeps_completed'] += 1
                except (A.AIScanError, OSError) as ex:
                    self._disable('manifest_invalid', ex)
                    return self.eligibility.check()
                if not self._active_ok():
                    self.counters['active_digest_mismatch'] += 1
                    self._mark_active_corrupt('active segment differs from the running digest / durable checkpoint')
                    return self.eligibility.check()
                fr = self._cp['first_row_at']
                if self._active_bytes and fr is not None and self._clock() - fr > self.max_active_age_s:
                    try:
                        self._seal_locked('age')
                    except A.AIScanError:
                        return self.eligibility.check()
                    if self.disabled: return self.eligibility.check()
                return self.eligibility.publish(True)
        finally:
            self._tick_lock.release()

    refresh = tick

    def _active_ok(self):
        n = len(self._m['segments']) + 1
        data = _read_limited(self._p(L.seg_name(n)), self.bound)
        if data is None or len(data) != self._active_bytes or hashlib.sha256(data).hexdigest() != self._digest.hexdigest():
            return False
        try:
            disk = _load_json(self._p(CHECKPOINT_NAME), verify_checkpoint)
        except A.AIScanError:
            return False
        return disk == self._cp and disk['pending'] is None and _tt(disk['committed']) == _triple(n, data)
