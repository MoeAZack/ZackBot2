"""Bounded, rotated, append-only JSONL ledger + the decision-time hook.

Rotation: when the active file would exceed max_bytes it is renamed to <path>.1 (older ones shift to .2 ...), and files
beyond max_files in total are deleted - so disk use is bounded by ~max_files * max_bytes. Rows are never rewritten.
Dedupe: every row carries a `decision_id`; ids already present in any retained file are skipped (restart-safe).
"""
import json, logging, os, threading
from datetime import datetime, timezone


from .common import SCHEMA_VERSION, STATUSES, canonical_json, corr_id_of, sha256_hex, to_utc
from .sanitize import sanitize_row, _features
from .features import features_at

log = logging.getLogger('zackbot.ml_shadow')


class JsonlLedger:
    def __init__(self, path, max_bytes=5_000_000, max_files=5, id_field='decision_id'):
        if max_files < 1 or max_bytes < 1:
            raise ValueError('max_files and max_bytes must be >= 1')
        self.path, self.max_bytes, self.max_files, self.id_field = str(path), int(max_bytes), int(max_files), id_field
        self._lock = threading.Lock()
        self._tail_ok = False                           # the active file's last byte has not been checked yet
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        self._seen = {r.get(id_field) for r in self.rows()}

    def files(self):
        """Retained files, oldest first."""
        out = [f'{self.path}.{k}' for k in range(self.max_files - 1, 0, -1)] + [self.path]
        return [p for p in out if os.path.exists(p)]

    def rows(self):
        for p in self.files():
            with open(p, 'r', encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        log.warning('ml_shadow: skipping corrupt ledger line in %s', p)

    def __contains__(self, rid):
        return rid in self._seen

    def _rotate(self):
        oldest = f'{self.path}.{self.max_files - 1}' if self.max_files > 1 else self.path
        if os.path.exists(oldest):
            os.remove(oldest)
        for k in range(self.max_files - 2, 0, -1):
            if os.path.exists(f'{self.path}.{k}'):
                os.replace(f'{self.path}.{k}', f'{self.path}.{k + 1}')
        if self.max_files > 1 and os.path.exists(self.path):
            os.replace(self.path, f'{self.path}.1')
        self._seen = {r.get(self.id_field) for r in self.rows()}

    def append(self, row):
        """Append one row of a known kind ('decision' | 'label'). The row is rebuilt by sanitize_row from a fixed
        per-kind allow-list (unknown keys dropped, strings redacted, numbers finite) - nothing else reaches disk.
        Returns the written row, or None if its id is already present. Unknown kind -> ValueError, nothing written."""
        row = sanitize_row(row.get('kind') if isinstance(row, dict) else None, row)
        rid = row.get(self.id_field)
        line = canonical_json(row) + '\n'
        with self._lock:
            if rid is not None and rid in self._seen:
                return None
            size = os.path.getsize(self.path) if os.path.exists(self.path) else 0
            if size and size + len(line.encode('utf-8')) > self.max_bytes:
                self._rotate(); size = 0
            if size and not self._tail_ok:            # #104 / Cowork T1: a torn last line (crash mid-append) is closed
                with open(self.path, 'rb') as fb:       # off first, so the next row never lands on the fragment
                    fb.seek(-1, os.SEEK_END)
                    if fb.read(1) != b'\n': line = '\n' + line
            with open(self.path, 'a', encoding='utf-8') as fh:
                # Only sanitize_row output reaches this sink: fixed field allow-list, every string through
                # redact_text, raw trade ids hashed (corr_id_of). Proven by tests/test_ml_shadow01.py redaction probes
                # (planted keys/tokens/ids across ctx, features, reason, exit_reason and rotated files).
                fh.write(line)  # lgtm[py/clear-text-storage-sensitive-data]  # codeql[py/clear-text-storage-sensitive-data]
                fh.flush()
            self._tail_ok = True                        # every row we write ends in a newline
            self._seen.add(rid)
        return row


def _iso(ts):
    return to_utc(ts).strftime('%Y-%m-%dT%H:%M:%SZ')


def decision_id(sleeve, strategy, symbol, side, tf, candle_time):
    """Deterministic id: identical candidate on the same candle after a restart -> same id."""
    parts = [str(sleeve), str(strategy), str(symbol).upper(), str(side).upper(), str(tf), _iso(candle_time)]
    return sha256_hex('|'.join(parts))[:32]


def labelable(row):
    """A decision that may receive a close label: taken, or pending (a resting/armed entry that later filled).
    Skipped decisions never do. v1 rows without status fall back to `taken`."""
    st = row.get('status') if isinstance(row, dict) else None
    if not isinstance(row, dict) or st == 'skipped':
        return False
    return st == 'pending' or bool(row.get('taken'))          # 'taken' (or a v1 row) needs taken=True as well


def build_decision_record(*, sleeve, strategy, family, symbol, side, tf, candle_time, taken, reason='',
                          rotation_group=None, df=None, i=None, ctx=None, features=None, trade_id=None, now=None,
                          status=None):
    if features is None:
        if df is None or i is None:
            raise ValueError('need df+i or precomputed features')
        features = features_at(df, i, ctx)
    status = status or ('taken' if taken else 'skipped')
    if status not in STATUSES:
        raise ValueError('status must be taken, skipped or pending')
    taken = status == 'taken'
    feats = _features(features['features'])
    missing = sorted(k for k, v in feats.items() if v is None)
    rec = {
        'kind': 'decision', 'schema': SCHEMA_VERSION,
        'decision_id': decision_id(sleeve, strategy, symbol, side, tf, candle_time),
        'corr_id': corr_id_of(trade_id) if taken else None,
        'ts_utc': (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'candle_time': _iso(candle_time),
        'sleeve': sleeve, 'strategy': strategy, 'family': family, 'symbol': str(symbol).upper(),
        'side': str(side).upper(), 'tf': str(tf), 'taken': bool(taken), 'status': status, 'reason': str(reason or ''),
        'rotation_group': rotation_group, 'features': feats, 'missing': missing,
    }
    rec['feature_hash'] = sha256_hex(canonical_json(feats))
    return rec


def record_decision(ledger, **kw):
    """ENGINE HOOK (call once per candidate signal, before acting on it). Observation-only: never raises, never
    returns anything the engine should act on. Returns the written row, or None on dedupe / any error."""
    try:
        return ledger.append(build_decision_record(**kw))
    except Exception as e:                                   # noqa: BLE001 - an observer must never break the engine
        log.warning('ml_shadow: record_decision failed (%s, ignored)', type(e).__name__)   # type only: no data in logs
        return None
