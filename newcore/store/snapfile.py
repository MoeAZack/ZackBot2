"""Snapshot files snap/g<20 digits>.snap (design 3.3): write-once, one generation each.

    file header (kind 1) | header record      {account_id, binding_digest, format, format_version, generation, lsn_upto,
                                               min_reader_version, writer_build, writer_seq, written_ms}
                         | binding record     NC-01 canonical bytes of the Account (binding, its confirmation state)
                         | settings record    {format, format_version, min_reader_version, values} (D13)
                         | snapshot record    NC-01 canonical bytes of the domain Snapshot (generation, last_sequence,
                                              portfolio with its explicit ownership and proof)
                         | provenance record  records.validate_provenance
                         | end record         {"records": 5, "sha256": sha256 of every preceding byte}
Nothing may follow the end record (A17: NUL padding included).

Decoding order: file header and header record versions first (rule 1: FUTURE for a future / unknown frame or format
version, a min_reader_version above this reader or a newer writer_seq; OLDER for a format this build only migrates),
then every record strictly (NC-01 codec for the NC-01 documents; a future NC-01 schema is FUTURE too), then the
cross-record identity checks. Anything else is SnapDamage. The writer re-decodes what it encodes before committing (A18).
"""
from __future__ import annotations

from dataclasses import dataclass

from newcore.domain import canonical_bytes, decode_result
from newcore.domain.codec import Outcome

from .frame import (FILE_HEADER, KIND_SNAPSHOT, RT_BINDING, RT_END, RT_HEADER, RT_PROVENANCE, RT_SETTINGS, RT_SNAPSHOT,
                    file_header, frame, record_at, resync_records)
from .header import HeaderError, canonical_json, strict_json
from .records import (FORMAT_SNAPSHOT, RecordError, is_count, settings_doc, sha256_hex, validate_provenance,
                      validate_settings)

HEADER_KEYS = frozenset({'account_id', 'binding_digest', 'format', 'format_version', 'generation', 'lsn_upto',
                         'min_reader_version', 'writer_build', 'writer_seq', 'written_ms'})
ORDER = (RT_HEADER, RT_BINDING, RT_SETTINGS, RT_SNAPSHOT, RT_PROVENANCE, RT_END)


class SnapFuture(Exception):
    """Rule 1: a future / unknown format somewhere in the file (ABORT_RO)."""


class SnapOlder(Exception):
    """F < R with no migrator: an unknown path (ABORT_RO) unless a migrator exists."""

    def __init__(self, fmt):
        super().__init__(f'snapshot format {fmt}')
        self.fmt = fmt


class SnapDamage(Exception):
    """Current-format damage (rule 4). Never names content."""


@dataclass(frozen=True)
class SnapFile:
    header: dict
    account: object                 # NC-01 Account
    settings: dict
    snapshot: object                # NC-01 Snapshot
    provenance: dict
    sha256: str
    length: int

    @property
    def generation(self):
        return self.header['generation']

    @property
    def portfolio(self):
        return self.snapshot.portfolio

    @property
    def lsn_upto(self):
        return self.header['lsn_upto']


def encode_snapshot(*, account, settings, snapshot, prov, writer_build, writer_seq, written_ms, fmt=1):
    header = {'account_id': snapshot.account_id, 'binding_digest': account.binding.key_digest,
              'format': FORMAT_SNAPSHOT, 'format_version': fmt, 'generation': snapshot.generation,
              'lsn_upto': snapshot.last_sequence, 'min_reader_version': fmt, 'writer_build': writer_build,
              'writer_seq': writer_seq, 'written_ms': written_ms}
    body = (file_header(KIND_SNAPSHOT) + frame(RT_HEADER, canonical_json(header))
            + frame(RT_BINDING, canonical_bytes(account)) + frame(RT_SETTINGS, canonical_json(settings_doc(settings)))
            + frame(RT_SNAPSHOT, canonical_bytes(snapshot)) + frame(RT_PROVENANCE, canonical_json(prov)))
    return body + frame(RT_END, canonical_json({'records': 5, 'sha256': sha256_hex(body)}))


def _version(doc, reader_format, reader_seq):
    """'ok' / 'future' / 'older' for a header-like document (rule 1 on non-integer and newer values)."""
    fv, mr = doc.get('format_version'), doc.get('min_reader_version')
    if not (is_count(fv) and is_count(mr)) or fv == 0:
        return 'future'
    if 'writer_seq' in doc and not is_count(doc['writer_seq']):
        return 'future'
    if fv > reader_format or mr > reader_format or doc.get('writer_seq', 0) > reader_seq:
        return 'future'
    return 'older' if fv < reader_format else 'ok'


def _future_anywhere(raw, reader):
    """HIGH-2 carry-over (Codex #43): rule 1 over EVERY independently CRC-valid record of a damaged snapshot (resync on
    the sync bytes, read-only, bounded by the file size): a future header / settings version or a future NC-01 schema
    anywhere makes the file a future member (ABORT_RO), never damage."""
    for r in resync_records(raw, 0, max_len=64 * 1024 * 1024):
        if r.rtype in (RT_HEADER, RT_SETTINGS):
            try:
                doc, _ = strict_json(r.payload)
            except HeaderError:
                continue
            if type(doc) is dict and 'format_version' in doc and \
                    _version(doc, reader.snapshot_format if r.rtype == RT_HEADER else 1, reader.writer_seq) == 'future':
                return True
        elif r.rtype in (RT_BINDING, RT_SNAPSHOT):
            res = decode_result(r.payload, expect='account' if r.rtype == RT_BINDING else 'snapshot')
            if res.outcome is Outcome.UNSUPPORTED_VERSION:
                return True
    return False


def peek(raw, reader):
    """Rule 1 on the file header and the header record: 'ok' | 'future' | 'older' | 'damage'; a damaged file is
    swept for a future record anywhere first (HIGH-2)."""
    v = _peek(raw, reader)
    if v == 'damage' and _future_anywhere(bytes(raw), reader):
        return 'future'
    return v


def _peek(raw, reader):
    raw = bytes(raw)
    if len(raw) >= FILE_HEADER.size and raw[:4] == b'ZBNC' and raw[4] == KIND_SNAPSHOT and (
            raw[5] != 1 or raw[6:8] != b'\0\0'):
        return 'future'
    if raw[:FILE_HEADER.size] != file_header(KIND_SNAPSHOT):
        return 'damage'
    r = record_at(raw, FILE_HEADER.size)
    if r is None or r.rtype != RT_HEADER:
        return 'damage'
    try:
        doc, _ = strict_json(r.payload)
    except HeaderError:
        return 'damage'
    if type(doc) is not dict:
        return 'damage'
    return _version(doc, reader.snapshot_format, reader.writer_seq)


def _nc01(payload, expect):
    res = decode_result(payload, expect=expect)
    if res.outcome is Outcome.UNSUPPORTED_VERSION:
        raise SnapFuture(f'{expect} schema {res.error.direction}')
    if res.outcome is not Outcome.OK or canonical_bytes(res.record) != payload:
        raise SnapDamage(f'{expect} record is not a canonical NC-01 document')
    return res.record


def decode_snapshot(raw, reader, *, account_id, migrate=True):
    """SnapFile, or SnapFuture / SnapOlder / SnapDamage. An older format with a migrator is decoded by the migrator
    (which returns a SnapFile in the current shape); the caller then commits it as a new MIGRATION generation.
    Rule 1 wins over damage: a future record anywhere in a damaged file is SnapFuture (HIGH-2)."""
    raw = bytes(raw)
    try:
        return _decode_snapshot(raw, reader, account_id=account_id, migrate=migrate)
    except SnapDamage:
        if _future_anywhere(raw, reader):
            raise SnapFuture('a future record behind damage') from None
        raise


def _decode_snapshot(raw, reader, *, account_id, migrate):
    v = peek(raw, reader)
    if v == 'future':
        raise SnapFuture('snapshot header version')
    if v == 'damage':
        raise SnapDamage('snapshot header')
    recs, off = [], FILE_HEADER.size
    while off < len(raw) and len(recs) < len(ORDER):
        r = record_at(raw, off, max_len=64 * 1024 * 1024)
        if r is None:
            raise SnapDamage(f'invalid record at {off}')
        recs.append(r)
        off = r.end
    if [r.rtype for r in recs] != list(ORDER) or off != len(raw):
        raise SnapDamage('record layout (or bytes after the end record)')
    header, _ = strict_json(recs[0].payload)
    if v == 'older':
        fmt = header['format_version']
        if not migrate or fmt not in reader.migrators:
            raise SnapOlder(fmt)
        return reader.migrators[fmt](raw, account_id)
    try:
        hdr, problems = strict_json(recs[0].payload)
        settings, p3 = strict_json(recs[2].payload)
        prov, p5 = strict_json(recs[4].payload)
        end, p6 = strict_json(recs[5].payload)
    except HeaderError:
        raise SnapDamage('a store record is not JSON') from None
    if type(settings) is dict and _version(settings, 1, reader.writer_seq) == 'future':
        raise SnapFuture('settings record version')                   # NF-03 / NF-04: a future settings member
    account = _nc01(recs[1].payload, 'account')
    snap = _nc01(recs[3].payload, 'snapshot')
    if problems or p3 or p5 or p6:
        raise SnapDamage('hostile JSON in a store record')
    if set(hdr) != HEADER_KEYS or hdr['format'] != FORMAT_SNAPSHOT:
        raise SnapDamage('snapshot header keys')
    if end != {'records': 5, 'sha256': sha256_hex(raw[:recs[5].offset])}:
        raise SnapDamage('snapshot end hash')
    try:
        values = validate_settings(settings)
        validate_provenance(prov)
    except RecordError as ex:
        raise SnapDamage(str(ex)) from None
    if not (is_count(hdr['generation']) and is_count(hdr['lsn_upto']) and is_count(hdr['written_ms'])):
        raise SnapDamage('snapshot header counters')
    ident = (hdr['account_id'], account.account_id, snap.account_id)
    if ident != (account_id,) * 3:
        raise SnapDamage('the snapshot names another account')
    if (hdr['generation'], hdr['lsn_upto']) != (snap.generation, snap.last_sequence):
        raise SnapDamage('snapshot header disagrees with its record')
    if hdr['binding_digest'] != account.binding.key_digest:
        raise SnapDamage('snapshot binding digest disagrees with its binding record')
    return SnapFile(hdr, account, values, snap, prov, sha256_hex(raw), len(raw))
