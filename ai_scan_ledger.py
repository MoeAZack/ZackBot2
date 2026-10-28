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
"""
import json
import logging
import os
import threading

import ai_scan as A
from ml_shadow.common import canonical_json

log = logging.getLogger('zackbot.ai_scan')

LEDGER_DIR, LEDGER_FILE = 'ai_scan', 'ai_scan.jsonl'
VIEW_LIMIT = 500


def ledger_path(data_dir):
    return os.path.join(str(data_dir), LEDGER_DIR, LEDGER_FILE)


def read_records(path):
    """-> (valid records in file order, skipped count). Invalid lines are skipped with a warning, never raised; an
    unreadable ledger path raises AIScanError, which scan_view turns into an error response."""
    bad, out = 0, []
    if os.path.exists(path):
        try:
            fh = open(path, 'rb')
        except OSError as ex:                                   # e.g. a directory at the ledger path (Cowork R1)
            raise A.AIScanError(f'ledger unreadable ({type(ex).__name__})') from ex
        with fh:
            for n, raw in enumerate(fh, 1):
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


class ScanLedger:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.Lock()
        self.skipped = 0                                 # corrupt / torn / edited lines seen by the last full read
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._seen = {A.record_key(r): r['record_hash'] for r in self.records()}

    def records(self):
        """Every valid record, file order. Invalid lines are skipped and counted (self.skipped), never raised."""
        out, self.skipped = read_records(self.path)
        return out

    def __contains__(self, key):
        return key in self._seen

    def append(self, rec):
        """Append one sealed record. -> the record, or None when its key is already on disk with the same hash.
        A conflicting record (same key, different content) or an unsealed / forged one raises AIScanError, unwritten."""
        if not isinstance(rec, dict): raise A.AIScanError('a ledger row must be a sealed record')
        A._verify(rec)
        key = A.record_key(rec)
        line = (canonical_json(rec) + '\n').encode('utf-8')
        with self._lock:
            have = self._seen.get(key)
            if have is not None:
                if have != rec['record_hash']: raise A.AIScanError(f'{key}: conflicting replay of an immutable record')
                return None
            with open(self.path, 'ab') as fh:
                if fh.tell():                            # close off a torn last line (crash mid-append) first
                    with open(self.path, 'rb') as fr:
                        fr.seek(-1, os.SEEK_END)
                        if fr.read(1) != b'\n': line = b'\n' + line
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
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
