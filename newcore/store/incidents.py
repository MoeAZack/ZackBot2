"""The out-of-band boot incident log (design 2 / P1 / ABORT-RO; decision D14: per install, every line names its account).

    <base>/incidents/boot.jsonl        OUTSIDE data\\: ABORT-RO writes nothing in data\\ but can still report

One canonical JSON object per line: {account_id, kind, reason, written_ms, details}. Details carry hashes, sizes, names
and counts only (A16): never file content, never a parse-error excerpt, never a credential. Best-effort: a failure to
log is returned, never raised, and never changes the boot outcome.
"""
from __future__ import annotations

import os

from .header import canonical_json

INCIDENTS_DIR = 'incidents'
BOOT_LOG = 'boot.jsonl'


def append_incident(fs, base, account_id, kind, reason, written_ms, details=None):
    """True when the line is durable."""
    d = os.path.join(base, INCIDENTS_DIR)
    p = os.path.join(d, BOOT_LOG)
    line = canonical_json({'account_id': account_id, 'details': details or {}, 'kind': kind, 'reason': reason,
                           'written_ms': written_ms}) + b'\n'
    try:
        if fs.kind(d) == 'missing':
            fs.mkdir(d)
            fs.fsync_dir(base)
        h = fs.open_new(p) if fs.kind(p) == 'missing' else fs.open_append(p)
        try:
            fs.write(h, line)
            fs.fsync(h)
        finally:
            fs.close(h)
        return True
    except OSError:
        return False
