"""The evidence envelope (nc02_design.md 4.3 and 8; A04, A15, A16; decisions D4, D5).

Bytes the store must preserve but never read as state (a torn tail, the bytes of a failed append, an interrupted segment
create, a damaged or missing-binding member) are COPIED - never moved - into one encrypted file each:

    <account_dir>/evidence/<source>-<sha16>-<key8>.zbe        (directory with a protected DACL: user + SYSTEM, D5)
    file header (kind 6) | header record: cleartext metadata only | body record: ciphertext | end: sha256(ciphertext)

Cleartext metadata (the only thing logs, UI and reports may carry, A16): account_id, cipher, format / version,
incident_id, length, mtime_ms, offset, reason, rel_path (relative to the account dir), sha256 of the plaintext, source.
Never a byte of the plaintext, a prefix or a parse error excerpt.

Protocol (V0-V3): seal -> O_EXCL create -> write -> fsync -> directory flush -> read back, open, sha256 must match. A file
of ours that fails that check (a crash mid-copy, or a failed write of this attempt) is deleted with unlink_own_partial
(D4, the single delete in the store) and copied again, so "no partial evidence is left" (A04). A byte-identical, valid
envelope from an earlier attempt is reused (idempotent by name = content hash + metadata hash).
"""
from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import dataclass

from .cipher import default_cipher
from .frame import FILE_HEADER, KIND_EVIDENCE, RT_END, RT_EVIDENCE_BODY, RT_HEADER, file_header, frame, record_at
from .header import HeaderError, VersionVerdict, canonical_json, check_version, strict_json

EVIDENCE_DIR = 'evidence'
FORMAT = 'zackbot.newcore.evidence'
FORMAT_VERSION = 1
META_KEYS = frozenset({'account_id', 'cipher', 'format', 'format_version', 'incident_id', 'length',
                       'min_reader_version', 'mtime_ms', 'offset', 'reason', 'rel_path', 'sha256', 'source'})


class EnvelopeError(ValueError):
    """An envelope that does not verify (damaged, partial, other account, wrong key). Never names content."""


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    name: str                 # relative to the account dir, '/'-separated
    sha256: str               # of the preserved plaintext bytes
    size: int
    source: str               # torn_tail | failed_append | void_segment | damaged_member | ...
    rel_path: str             # the preserved member, relative to the account dir
    offset: int
    incident_id: str | None

    @property
    def segment(self):
        return self.rel_path.split('/')[-1]


def _sha(b):
    return hashlib.sha256(bytes(b)).hexdigest()


def ensure_evidence_dir(fs, account_dir):
    """Create evidence/ with its private DACL BEFORE anything is put in it (design I1 / 8.3)."""
    ed = os.path.join(account_dir, EVIDENCE_DIR)
    k = fs.kind(ed)
    if k == 'missing':
        fs.mkdir(ed)
        fs.set_private_acl(ed)
        fs.fsync_dir(account_dir)
    elif k != 'dir':
        raise OSError(errno.ENOTDIR, 'the evidence path is not a directory')
    return ed


def envelope_bytes(meta, ciphertext):
    return (file_header(KIND_EVIDENCE) + frame(RT_HEADER, canonical_json(meta)) + frame(RT_EVIDENCE_BODY, ciphertext)
            + frame(RT_END, canonical_json({'sha256': _sha(ciphertext)})))


def peek_version(raw):
    """Rule 1 on an envelope: OK / FUTURE / UNKNOWN (UNKNOWN also for a frame version this reader does not know)."""
    if len(raw) >= FILE_HEADER.size and raw[:4] == b'ZBNC' and (raw[5] != 1 or raw[6:8] != b'\0\0'):
        return VersionVerdict.UNKNOWN
    r = record_at(raw, FILE_HEADER.size) if len(raw) >= FILE_HEADER.size else None
    if r is None or r.rtype != RT_HEADER:
        return VersionVerdict.OK
    try:
        doc, _ = strict_json(r.payload)
    except HeaderError:
        return VersionVerdict.OK
    return check_version(doc)


def read_envelope(raw, cipher, account_id):
    """(metadata, plaintext) of a verified envelope, or EnvelopeError."""
    raw = bytes(raw)
    if raw[:FILE_HEADER.size] != file_header(KIND_EVIDENCE):
        raise EnvelopeError('not an evidence envelope (header)')
    recs, off = [], FILE_HEADER.size
    for _ in range(3):
        r = record_at(raw, off)
        if r is None:
            raise EnvelopeError('incomplete or damaged envelope')
        recs.append(r)
        off = r.end
    if off != len(raw) or [r.rtype for r in recs] != [RT_HEADER, RT_EVIDENCE_BODY, RT_END]:
        raise EnvelopeError('envelope layout')
    try:
        meta, problems = strict_json(recs[0].payload)
        end, problems2 = strict_json(recs[2].payload)
    except HeaderError:
        raise EnvelopeError('envelope metadata is not JSON') from None
    if problems or problems2 or type(meta) is not dict or set(meta) != META_KEYS or meta['format'] != FORMAT:
        raise EnvelopeError('envelope metadata keys')
    if check_version(meta) is not VersionVerdict.OK:
        raise EnvelopeError('envelope format version')
    if end != {'sha256': _sha(recs[1].payload)}:
        raise EnvelopeError('envelope ciphertext hash')
    if meta['account_id'] != account_id or meta['cipher'] != cipher.name:
        raise EnvelopeError('envelope of another account or cipher')
    try:
        plain = cipher.open(recs[1].payload, account_id)
    except OSError:
        raise EnvelopeError('envelope does not decrypt with this key') from None
    if len(plain) != meta['length'] or _sha(plain) != meta['sha256']:
        raise EnvelopeError('envelope plaintext hash')
    return meta, plain


def write_envelope(fs, account_dir, account_id, data, *, source, rel_path, offset=0, mtime_ms=None, incident_id=None,
                   reason=None, cipher=None):
    """(EvidenceRef, created). Raises OSError (incl. CipherUnavailable); the caller keeps / enters HOLD."""
    cipher = cipher or default_cipher()
    data = bytes(data)
    meta = {'account_id': account_id, 'cipher': cipher.name, 'format': FORMAT, 'format_version': FORMAT_VERSION,
            'incident_id': incident_id, 'length': len(data), 'min_reader_version': FORMAT_VERSION, 'mtime_ms': mtime_ms,
            'offset': offset, 'reason': reason, 'rel_path': rel_path, 'sha256': _sha(data), 'source': source}
    key = hashlib.sha256(canonical_json(meta)).hexdigest()
    name = f'{source}-{meta["sha256"][:16]}-{key[:8]}.zbe'
    ref = EvidenceRef(f'{EVIDENCE_DIR}/{name}', meta['sha256'], len(data), source, rel_path, offset, incident_id)
    ed = ensure_evidence_dir(fs, account_dir)
    p = os.path.join(ed, name)
    k = fs.kind(p)
    if k == 'file':
        try:
            read_envelope(fs.read_bytes(p), cipher, account_id)
            return ref, False                                     # V-idempotent: already copied and verified
        except EnvelopeError:
            fs.unlink_own_partial(p)                              # D4: our own unindexed partial from a crash
    elif k != 'missing':
        raise OSError(errno.EEXIST, 'a non-file at an evidence path')
    content = envelope_bytes(meta, cipher.seal(data, account_id))
    h = fs.open_new(p)
    created = True
    try:
        fs.write(h, content)
        fs.fsync(h)
    except OSError:
        _close(fs, h)
        h = None
        _drop(fs, p)
        raise
    finally:
        if h is not None:
            _close(fs, h)
    fs.fsync_dir(ed)
    try:
        read_envelope(fs.read_bytes(p), cipher, account_id)      # V3: verify before it counts
    except EnvelopeError:
        _drop(fs, p)
        raise OSError(errno.EIO, 'evidence read-back does not verify') from None
    return ref, created


def _close(fs, h):
    try:
        fs.close(h)
    except OSError:
        pass


def _drop(fs, p):
    try:
        fs.unlink_own_partial(p)
    except OSError:
        pass


def write_evidence(fs, account_dir, account_id, segment, offset, data, source='torn_tail', cipher=None):
    """Journal-level helper (NC-02a call sites): preserve bytes of journal/<segment> at `offset`."""
    return write_envelope(fs, account_dir, account_id, data, source=source, rel_path=f'journal/{segment}',
                          offset=offset, cipher=cipher)


__all__ = ['EVIDENCE_DIR', 'EnvelopeError', 'EvidenceRef', 'ensure_evidence_dir', 'envelope_bytes', 'peek_version',
           'read_envelope', 'write_envelope', 'write_evidence']
