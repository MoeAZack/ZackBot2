"""Evidence copies of bytes the journal will never read again (a torn tail, an interrupted create, the bytes of a failed
append), written BEFORE the segment that seals them. NC-02a keeps a plain framed copy inside the account dir; the
encrypted, access-restricted envelope is NC-02b (design section 8).

    file header (kind 5) | header record: {account_id, format, format_version, length, offset, segment, sha256, source}
                         | body record: the exact bytes

Idempotent: a byte-identical copy from an earlier attempt is reused; a partial copy of a crashed attempt is kept in
place (never trusted, never deleted) and the next free name is used.
"""
from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import dataclass

from .frame import KIND_EVIDENCE, RT_EVIDENCE_BODY, RT_HEADER, file_header, frame
from .header import EVIDENCE_FORMAT, canonical_json

EVIDENCE_DIR = 'evidence'


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    name: str                 # relative to the account dir, '/'-separated
    sha256: str
    size: int
    segment: str
    offset: int


def _sha(b):
    return hashlib.sha256(bytes(b)).hexdigest()


def evidence_bytes(account_id, segment, offset, data, source='torn_tail'):
    meta = canonical_json({'account_id': account_id, 'format': EVIDENCE_FORMAT, 'format_version': 1,
                           'length': len(data), 'offset': offset, 'segment': segment, 'sha256': _sha(data),
                           'source': source})
    return file_header(KIND_EVIDENCE) + frame(RT_HEADER, meta) + frame(RT_EVIDENCE_BODY, bytes(data))


def write_evidence(fs, account_dir, account_id, segment, offset, data, source='torn_tail'):
    """(EvidenceRef, created). Raises OSError (the caller maps it to DurabilityUnavailable)."""
    ed = os.path.join(account_dir, EVIDENCE_DIR)
    k = fs.kind(ed)
    if k == 'missing':
        fs.mkdir(ed)
        fs.fsync_dir(account_dir)
    elif k != 'dir':
        raise OSError(errno.ENOTDIR, 'the evidence path is not a directory')
    content = evidence_bytes(account_id, segment, offset, data, source)
    sha = _sha(data)
    stem = f'{"torn" if source == "torn_tail" else "failed"}-{segment[:-4]}-o{offset:010d}-{sha[:16]}'
    for attempt in range(64):
        name = stem + (f'-a{attempt}' if attempt else '') + '.ev'
        p = os.path.join(ed, name)
        ref = EvidenceRef(f'{EVIDENCE_DIR}/{name}', sha, len(data), segment, offset)
        kk = fs.kind(p)
        if kk == 'file':
            if fs.read_bytes(p) == content:
                return ref, False
            continue
        if kk != 'missing':
            continue
        h = fs.open_new(p)
        try:
            fs.write(h, content)
            fs.fsync(h)
        finally:
            try:
                fs.close(h)
            except OSError:
                pass
        fs.fsync_dir(ed)
        if fs.read_bytes(p) != content:
            raise OSError(errno.EIO, 'evidence read-back differs')
        return ref, True
    raise OSError(errno.EEXIST, 'no free evidence name')
