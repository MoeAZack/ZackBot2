"""AI-SCAN-01 (#199) wiring slice: the append-only JSONL ledger of scan signals / shadows / outcomes + the read-only view
behind GET /api/ai_scan. PAPER, shadow, report-only.

Ledger rules (same family as ml_shadow.ledger.JsonlLedger):
- append only: one sealed record per line, written whole (one write + flush + fsync) under a lock; no row is ever
  rewritten, reordered or deleted;
- idempotent: every record carries its immutable key (ai_scan.record_key - decision id, epoch, cost revision id); a key
  already on disk with the same record_hash is a no-op (restart), with a different one it is refused (AIScanError) and
  nothing is written;
- fail-closed reads: a corrupt, torn or edited line (bad JSON, not a record, wrong schema, hash mismatch) is skipped and
  counted in `skipped` with a warning - it never raises into the caller; a torn last line is closed off before the
  next append so a new row never lands on the fragment.

Nothing here places, sizes, gates or cancels an order, reads or writes settings, imports the engine or calls a model.
The view never holds the engine lock and is computed only when the AI_SCAN flag is True; otherwise it is exactly
ai_scan.api_off() and the ledger is not touched.

AI-SCAN-03A (contract §6b, R6): RotatingLedger / bind_read / read_page / fold_pages / read_window / Eligibility below are
the bounded rotation + manifest + verified-pagination prerequisite. Nothing wires them to an engine hook or enables a
producer; the single-file ScanLedger above is unchanged.
"""
import hashlib
import json
import logging
import os
import threading

import ai_scan as A
from ml_shadow.common import canonical_json, sha256_hex

log = logging.getLogger('zackbot.ai_scan')

LEDGER_DIR, LEDGER_FILE = 'ai_scan', 'ai_scan.jsonl'
VIEW_LIMIT = 500


def ledger_path(data_dir):
    return os.path.join(str(data_dir), LEDGER_DIR, LEDGER_FILE)


def _parse_lines(lines):
    """-> (valid records in order, skipped count) over raw byte lines; invalid lines are skipped with a warning."""
    bad, out = 0, []
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line: continue
        try:
            rec = json.loads(line.decode('utf-8'))
            if not isinstance(rec, dict): raise ValueError('not a record')
            A._verify(rec)
            A.record_key(rec)
        except (ValueError, KeyError, TypeError, UnicodeDecodeError) as ex:   # AIScanError is a ValueError
            bad += 1
            log.warning('ai_scan: skipping invalid ledger line %d (%s)', n, type(ex).__name__)
            continue
        out.append(rec)
    return out, bad


def read_records(path):
    """-> (valid records in file order, skipped count). Invalid lines are skipped with a warning, never raised; an
    unreadable ledger path raises AIScanError, which scan_view turns into an error response."""
    if not os.path.exists(path): return [], 0
    try:
        fh = open(path, 'rb')
    except OSError as ex:                                       # e.g. a directory at the ledger path (Cowork R1)
        raise A.AIScanError(f'ledger unreadable ({type(ex).__name__})') from ex
    with fh:
        return _parse_lines(fh)


def _append_line(path, line):
    """One whole line: write + flush + fsync; a torn last line (crash mid-append) is closed off first."""
    with open(path, 'ab') as fh:
        if fh.tell():
            with open(path, 'rb') as fr:
                fr.seek(-1, os.SEEK_END)
                if fr.read(1) != b'\n': line = b'\n' + line
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return len(line)


class ScanLedger:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.Lock()
        self.skipped = 0                                 # corrupt / torn / edited lines seen by the last full read
        self.error = None                                # set when the ledger path cannot be read: appends refuse
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        try:
            self._seen = {A.record_key(r): r['record_hash'] for r in self.records()}
        except A.AIScanError as ex:                      # never raises through construction (Codex 6460427480)
            self._seen, self.error = {}, str(ex)[:200]
            log.warning('ai_scan: ledger unreadable, appends refused (%s)', self.error)

    def records(self):
        """Every valid record, file order. Invalid lines are skipped and counted (self.skipped), never raised."""
        out, self.skipped = read_records(self.path)
        return out

    def __contains__(self, key):
        return key in self._seen

    def append(self, rec):
        """Append one sealed record. -> the record, or None when its key is already on disk with the same hash.
        A conflicting record (same key, different content) or an unsealed / forged one raises AIScanError, unwritten."""
        if self.error is not None: raise A.AIScanError(f'ledger unreadable, append refused: {self.error}')
        if not isinstance(rec, dict): raise A.AIScanError('a ledger row must be a sealed record')
        A._verify(rec)
        key = A.record_key(rec)
        line = (canonical_json(rec) + '\n').encode('utf-8')
        with self._lock:
            have = self._seen.get(key)
            if have is not None:
                if have != rec['record_hash']: raise A.AIScanError(f'{key}: conflicting replay of an immutable record')
                return None
            _append_line(self.path, line)
            self._seen[key] = rec['record_hash']
        return rec


def _decided(view):
    return view['signal'].get('decided_at') or ''


def scan_view(settings, data_dir, now=None, limit=VIEW_LIMIT):
    """GET /api/ai_scan. Flag not exactly True -> ai_scan.api_off() (no ledger read). Otherwise the displayed rows from
    the ledger, newest decision first. A replay that fails closed returns no rows and the reason - never a guess."""
    if (settings or {}).get(A.FLAG) is not True:
        return A.api_off()
    try:
        recs, skipped = read_records(ledger_path(data_dir))   # read-only: never creates the folder or the file
    except A.AIScanError as ex:
        return dict(on=True, rows=[], skipped=0, error=str(ex)[:200])
    try:
        views = A.fold(recs)
    except A.AIScanError as ex:
        return dict(on=True, rows=[], skipped=skipped, error=f'replay refused: {str(ex)[:200]}')
    rows = [A.display(v, now) for v in sorted(views.values(), key=_decided, reverse=True)[:max(0, int(limit))]]
    return dict(on=True, rows=rows, skipped=skipped, error=None)


# ------------------------------------------------------------------ AI-SCAN-03A: rotation / manifest prerequisite (§6b)
# Layout: <data>/ai_scan/segments/seg-000001.jsonl ... + manifest.json. Segment n is sealed (immutable, listed in the
# manifest with sha256 / bytes / rows and a prev -> chain hash link) or it is the one ACTIVE segment n = sealed + 1.
# The active segment is never in the manifest: the writer indexes it (row-level record hashes) for restart dedupe, but
# verified reads cover sealed segments only - rows become readable once sealed (rotation, or seal() at clean close).
SEG_DIR, MANIFEST_NAME, ROT_SCHEMA = 'segments', 'manifest.json', 'ai-scan-rotation-v1'
SEG_MIN, SEG_MAX = 1 << 20, 16 << 20                    # frozen segment_bound range, bytes (rotation_prerequisite.limits)
SEGS_PER_READ, MAX_SEGMENTS = 4, 4096                   # max segments per verified page / in one manifest
GENESIS = '0' * 64
_ENTRY = ('n', 'name', 'sha256', 'bytes', 'rows', 'prev')
_MANIFEST = {'schema', 'segment_bound', 'segments', 'disabled', 'manifest_hash'}
DISABLE_MARKERS = (None, 'manifest_full', 'active_corrupt')    # AI-SCAN-04A adds the durable active_corrupt marker


def segments_dir(data_dir):
    return os.path.join(str(data_dir), LEDGER_DIR, SEG_DIR)


def seg_name(n):
    return f'seg-{n:06d}.jsonl'


def _int(v):
    return type(v) is int                                # never a bool or a truthy flag


def check_bound(b):
    """segment_bound: a plain int of bytes within [SEG_MIN, SEG_MAX], never a bool / str / float. -> b."""
    if not (_int(b) and SEG_MIN <= b <= SEG_MAX):
        raise A.AIScanError(f'segment_bound must be an int in [{SEG_MIN}, {SEG_MAX}] bytes, got {b!r}')
    return b


def _chain(e):
    return sha256_hex(canonical_json({k: e[k] for k in _ENTRY}))


def _manifest_hash(m):
    return sha256_hex(canonical_json({k: v for k, v in m.items() if k != 'manifest_hash'}))


def _new_manifest(bound, segments, disabled=None):
    m = dict(schema=ROT_SCHEMA, segment_bound=bound, segments=segments, disabled=disabled)
    m['manifest_hash'] = _manifest_hash(m)
    return m


def verify_rotation_manifest(m):
    """-> m when hash-valid: exact keys, manifest_hash, a valid bound, <= MAX_SEGMENTS entries numbered 1..k with their
    canonical names, each prev = the previous chain (GENESIS first) and chain re-derived. Else AIScanError."""
    if not (isinstance(m, dict) and set(m) == _MANIFEST and m['schema'] == ROT_SCHEMA):
        raise A.AIScanError('not a rotation manifest')
    if m['manifest_hash'] != _manifest_hash(m): raise A.AIScanError('manifest hash mismatch (edited manifest)')
    bound, segs = check_bound(m['segment_bound']), m['segments']
    if not (isinstance(segs, list) and len(segs) <= MAX_SEGMENTS) or m['disabled'] not in DISABLE_MARKERS:
        raise A.AIScanError('manifest segment list or disable marker is malformed')
    prev = GENESIS
    for i, e in enumerate(segs, 1):
        ok = (isinstance(e, dict) and set(e) == set(_ENTRY) | {'chain'} and _int(e['n']) and e['n'] == i
              and e['name'] == seg_name(i) and e['prev'] == prev and A._hex64(e['sha256'], e['chain'])
              and _int(e['bytes']) and 0 < e['bytes'] <= bound and _int(e['rows']) and e['rows'] >= 0
              and e['chain'] == _chain(e))
        if not ok: raise A.AIScanError(f'manifest entry {i} breaks the hash chain')
        prev = e['chain']
    return m


def load_manifest(seg_dir):
    """-> the verified manifest, or None when there is none. Unreadable / not hash-valid -> AIScanError."""
    p = os.path.join(str(seg_dir), MANIFEST_NAME)
    try:
        with open(p, 'rb') as fh:
            m = json.loads(fh.read().decode('utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeDecodeError) as ex:
        raise A.AIScanError(f'manifest unreadable ({type(ex).__name__})') from ex
    return verify_rotation_manifest(m)


def _write_manifest(seg_dir, m):
    """Atomic: temp file + fsync + os.replace - a reader sees the old or the new manifest, never a torn one."""
    p = os.path.join(str(seg_dir), MANIFEST_NAME)
    tmp = p + '.tmp'
    with open(tmp, 'wb') as fh:
        fh.write(canonical_json(m).encode('utf-8'))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def _read_entry(seg_dir, e):
    """One sealed segment, verified against its manifest entry: bytes, sha256 and row count, and every line a valid
    record. -> (records, 0). Missing, changed, truncated or any malformed / torn line -> AIScanError (never skipped)."""
    try:
        with open(os.path.join(str(seg_dir), e['name']), 'rb') as fh:
            data = fh.read(e['bytes'] + 1)               # never more than the bound + 1 byte
    except OSError as ex:
        raise A.AIScanError(f"{e['name']}: segment missing or unreadable ({type(ex).__name__})") from ex
    if len(data) != e['bytes'] or hashlib.sha256(data).hexdigest() != e['sha256']:
        raise A.AIScanError(f"{e['name']}: segment changed or truncated")
    recs, bad = _parse_lines(data.split(b'\n'))
    if bad: raise A.AIScanError(f"{e['name']}: {bad} malformed line(s) in a sealed segment")
    if len(recs) != e['rows']: raise A.AIScanError(f"{e['name']}: row count differs from the manifest")
    return recs, bad


class Eligibility:
    """G4 seam: check() is the per-hook call - ONE read of an immutable (generation, ok, reason) tuple, no lock, disk
    I/O, hashing or manifest traversal. publish() is writer-side only (startup / refresh / seal / disable), after the
    manifest, hashes and config were verified; it swaps the tuple atomically with a new generation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._gen = (0, False, 'not_verified')

    def check(self):
        return self._gen

    def publish(self, ok, reason=None):
        with self._lock:
            self._gen = (self._gen[0] + 1, ok is True, None if ok is True else str(reason or 'refused'))
            return self._gen


class RotatingLedger:
    """Append-only AI-SCAN segments with a hash-chained manifest (writer side; not wired to any hook).
    - rotation: a row that would push the active segment past segment_bound seals it first (hash -> manifest entry,
      atomic manifest write) and opens segment n + 1; a single row larger than the bound is refused;
    - full: when sealing would leave no room for another segment (MAX_SEGMENTS), the active segment is sealed with the
      persisted disable marker manifest_full and the append is refused - disable BEFORE append, counted
      producer_disabled / manifest_full; a restart stays disabled;
    - idempotent across segments: keys of every sealed (hash-verified) and the active segment are indexed at startup; a
      same-hash replay is a no-op, a conflicting one AIScanError;
    - fail closed: an invalid bound, a manifest that is not hash-valid, a bound differing from the manifest's, or a
      sealed segment that is missing / changed disables the producer (counted producer_disabled); it never appends on."""

    def __init__(self, seg_dir, segment_bound, eligibility=None):
        self.dir, self._lock = str(seg_dir), threading.Lock()
        self.eligibility = eligibility if eligibility is not None else Eligibility()
        self.counters = dict(producer_disabled=0, manifest_full=0, rotations=0, skipped_lines=0)
        self.disabled, self.error, self._seen, self._m, self._active_bytes = None, None, {}, None, 0
        try:
            self.bound = check_bound(segment_bound)
        except A.AIScanError as ex:
            self.bound = None
            self._disable('config_invalid', ex)
            return
        try:
            os.makedirs(self.dir, exist_ok=True)
            m = load_manifest(self.dir)
            if m is None:
                m = _new_manifest(self.bound, [])
                _write_manifest(self.dir, m)
            if m['segment_bound'] != self.bound:
                self._disable('config_mismatch')
                return
            self._m = m
            for e in m['segments']:                      # restart index: every sealed segment, hash-verified
                recs, bad = _read_entry(self.dir, e)
                self._index(recs, bad)
            active = self._active_path()
            recs, bad = read_records(active)
            self._active_bytes = os.path.getsize(active) if os.path.isfile(active) else 0
            if self._active_bytes > self.bound: raise A.AIScanError('active segment exceeds segment_bound')
        except (A.AIScanError, OSError) as ex:
            self._disable('manifest_invalid', ex)
            return
        if bad:                                          # a malformed / torn active line is never indexed or sealed
            self.counters['skipped_lines'] += bad
            self._disable('active_corrupt', A.AIScanError(f'{bad} malformed line(s) in the active segment'))
            return
        self._index(recs, 0)
        if m['disabled']:
            self._disable(m['disabled'])
            return
        self.refresh()

    def _index(self, recs, bad):
        self.counters['skipped_lines'] += bad
        for r in recs:
            self._seen.setdefault(A.record_key(r), r['record_hash'])

    def _active_path(self):
        return os.path.join(self.dir, seg_name(len(self._m['segments']) + 1))

    def _disable(self, reason, ex=None):
        self.disabled, self.error = reason, (str(ex)[:200] if ex is not None else reason)
        self.counters['producer_disabled'] += 1
        if reason == 'manifest_full': self.counters['manifest_full'] += 1
        log.warning('ai_scan: producer disabled (%s: %s)', reason, self.error)
        self.eligibility.publish(False, reason)

    def refresh(self):
        """Writer-side re-verification (startup, after a seal, or periodic - never the trading thread): the manifest on
        disk is hash-valid and identical to ours, the bound matches config, every sealed file matches its entry (bytes,
        sha256, rows, no malformed line) and the active segment has no malformed line. Publishes a new eligibility
        generation; a mismatch disables the producer. -> the published generation. Cost: reads every sealed byte."""
        if self.disabled: return self.eligibility.check()
        try:
            m = load_manifest(self.dir)
            if m is None or m['manifest_hash'] != self._m['manifest_hash']: raise A.AIScanError('manifest changed')
            if m['segment_bound'] != self.bound: raise A.AIScanError('segment_bound differs from the manifest')
            for e in m['segments']: _read_entry(self.dir, e)
            bad = read_records(self._active_path())[1]
        except (A.AIScanError, OSError) as ex:
            self._disable('manifest_invalid', ex)
            return self.eligibility.check()
        if bad:
            self._disable('active_corrupt', A.AIScanError(f'{bad} malformed line(s) in the active segment'))
            return self.eligibility.check()
        return self.eligibility.publish(True)

    def _rotate(self):
        """Seal the active segment as manifest entry k (caller holds the lock; active is non-empty). -> True when that
        left the manifest full (producer now disabled). An active segment with any malformed / torn line is never sealed:
        the producer is disabled (active_corrupt) and AIScanError raised, the manifest unchanged."""
        segs, k = self._m['segments'], len(self._m['segments']) + 1
        with open(self._active_path(), 'rb') as fh:
            data = fh.read()
        recs, bad = _parse_lines(data.split(b'\n'))
        if bad:
            self.counters['skipped_lines'] += bad
            self._disable('active_corrupt', A.AIScanError(f'{bad} malformed line(s) in the active segment'))
            raise A.AIScanError('producer disabled (active_corrupt), seal refused')
        e = dict(n=k, name=seg_name(k), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                 rows=len(recs), prev=segs[-1]['chain'] if segs else GENESIS)
        e['chain'] = _chain(e)
        full = k >= MAX_SEGMENTS
        m = _new_manifest(self.bound, segs + [e], 'manifest_full' if full else None)
        _write_manifest(self.dir, m)
        self._m, self._active_bytes = m, 0
        self.counters['rotations'] += 1
        if full: self._disable('manifest_full')
        else: self.refresh()
        return full

    def seal(self):
        """Seal the active segment now (e.g. clean close) so its rows become readable. -> the new manifest end hash, or
        None when the active segment is empty."""
        with self._lock:
            if self.disabled: raise A.AIScanError(f'producer disabled ({self.disabled}), seal refused')
            if not self._active_bytes: return None
            self._rotate()
            return self._m['segments'][-1]['chain']

    def append(self, rec):
        """Append one sealed record. -> the record, or None for a same-hash replay already in any segment."""
        if not isinstance(rec, dict): raise A.AIScanError('a ledger row must be a sealed record')
        A._verify(rec)
        key = A.record_key(rec)
        line = (canonical_json(rec) + '\n').encode('utf-8')
        with self._lock:
            if self.disabled: raise A.AIScanError(f'producer disabled ({self.disabled}), append refused')
            have = self._seen.get(key)
            if have is not None:
                if have != rec['record_hash']: raise A.AIScanError(f'{key}: conflicting replay of an immutable record')
                return None
            if len(line) + 1 > self.bound: raise A.AIScanError('row larger than segment_bound')
            if self._active_bytes and self._active_bytes + len(line) + 1 > self.bound and self._rotate():
                raise A.AIScanError('producer disabled (manifest_full), append refused')
            self._active_bytes += _append_line(self._active_path(), line)
            self._seen[key] = rec['record_hash']
        return rec


# ------------------------------------------------------------------ verified pagination (reader side)
def _derive_binding(m):
    segs = m['segments']
    pages = [segs[i:i + SEGS_PER_READ] for i in range(0, len(segs), SEGS_PER_READ)]
    return dict(manifest_hash=m['manifest_hash'], end_hash=segs[-1]['chain'] if segs else GENESIS,
                page_count=len(pages), cursors=[(p[0]['prev'], p[-1]['chain']) for p in pages])


def bind_read(seg_dir):
    """Bind BEFORE reading: -> {manifest_hash, end_hash, page_count, cursors[(start, end)]} from the verified manifest.
    Pages are consecutive runs of <= SEGS_PER_READ sealed segments; a page's cursors are its first prev / last chain."""
    m = load_manifest(seg_dir)
    if m is None: raise A.AIScanError('no rotation manifest')
    return _derive_binding(m)


def _check_binding(seg_dir, binding):
    """A binding is never trusted as given: re-derive every field from the verified manifest on disk, which must hash to
    binding['manifest_hash']. -> the re-derived binding; any forged / stale field or a changed manifest -> AIScanError."""
    m = load_manifest(seg_dir)
    if m is None or not isinstance(binding, dict) or m['manifest_hash'] != binding.get('manifest_hash'):
        raise A.AIScanError('manifest changed since bind')
    d = _derive_binding(m)
    try:
        same = set(binding) == set(d) and all(binding[k] == d[k] for k in ('manifest_hash', 'end_hash', 'page_count')) \
            and _int(binding['page_count']) and [tuple(c) for c in binding['cursors']] == d['cursors']
    except TypeError:
        same = False
    if not same: raise A.AIScanError('binding does not match the verified manifest')
    return d, m


def read_page(seg_dir, binding, i):
    """Read page i (<= SEGS_PER_READ segments) under the binding, re-derived from the manifest (_check_binding); every
    segment must match its entry. -> {index, manifest_hash, start, end, records, skipped}; else AIScanError."""
    d, m = _check_binding(seg_dir, binding)
    if not (_int(i) and 0 <= i < d['page_count']): raise A.AIScanError(f'page {i!r} outside the binding')
    recs = []
    for e in m['segments'][i * SEGS_PER_READ:(i + 1) * SEGS_PER_READ]:
        recs += _read_entry(seg_dir, e)[0]
    start, end = d['cursors'][i]
    return dict(index=i, manifest_hash=m['manifest_hash'], start=start, end=end, records=recs, skipped=0)


def fold_pages(seg_dir, binding, pages):
    """Fold pages against the binding -> {complete, views, error}. The binding is re-derived from the verified manifest
    before and again after the pages (_check_binding). Complete only when exactly page_count pages arrive in order under
    that manifest hash, each starting at the previous end cursor (GENESIS first) and the last ending on end_hash. A
    forged binding, a missing middle / final page, a changed or reordered page or a valid truncated prefix is incomplete
    with NO views - never a plausible partial row. `pages` may be lazy: one page (<= SEGS_PER_READ segments) at a time.
    Page records are trusted as returned by read_page (same process); their segments were hash-verified there."""
    def stream():
        d = _check_binding(seg_dir, binding)[0]
        cur, n = GENESIS, 0
        for p in pages:
            if not (isinstance(p, dict) and n < d['page_count'] and p.get('index') == n
                    and p.get('manifest_hash') == d['manifest_hash'] and p.get('start') == cur == d['cursors'][n][0]
                    and p.get('end') == d['cursors'][n][1] and p.get('skipped') == 0):
                raise A.AIScanError(f'page {n} missing, changed or out of order')
            yield from p['records']
            cur, n = p['end'], n + 1
        if n != d['page_count'] or cur != d['end_hash']:
            raise A.AIScanError(f"incomplete: {n} of {d['page_count']} pages, terminal cursor not reached")
        _check_binding(seg_dir, binding)                     # the manifest did not change under the fold
    try:
        return dict(complete=True, views=A.fold(stream()), error=None)
    except A.AIScanError as ex:
        return dict(complete=False, views={}, error=str(ex)[:200])


def read_window(seg_dir, max_pages=None):
    """Bounded, causally complete read of every sealed segment: bind, then fold page by page (<= SEGS_PER_READ segments
    in memory). A window needing more than max_pages pages, or one that cannot be proven complete, is incomplete with no
    views. The active segment is excluded by design (see the layout note above)."""
    try:
        b = bind_read(seg_dir)
    except A.AIScanError as ex:
        return dict(complete=False, views={}, error=str(ex)[:200], pages=0)
    if max_pages is not None and b['page_count'] > max_pages:
        return dict(complete=False, views={}, error=f"window needs {b['page_count']} pages > bound {max_pages}",
                    pages=b['page_count'])
    out = fold_pages(seg_dir, b, (read_page(seg_dir, b, i) for i in range(b['page_count'])))
    out['pages'] = b['page_count']
    return out
