"""Byte framing of every store file (nc02_design.md 3.1). Pure: bytes in, typed scan out.

    FileHeader  8 bytes : b"ZBNC" | file_kind:u8 | frame_version:u8 | reserved:u16 = 0
    Record     12 bytes : sync:u16 = 0xB25A | rtype:u8 | flags:u8 = 0 | length:u32 LE | crc32:u32 LE, then `length`
                          payload bytes. crc32 = zlib.crc32(rtype | flags | length | payload).

`scan` walks a file from the header and classifies what follows the last valid record:
- nothing: clean;
- a TORN tail: an INCOMPLETE last record only (a short record header, or a length past EOF whose bytes are not a
  complete record with a corrupted length field), with NO valid record anywhere after it, at most one frame long;
- DAMAGE (fail closed, Cowork finding 1): an invalid record followed by a valid one later, a COMPLETE-length record
  that fails its sync / CRC / flags check even at the very end (a bit flip in the last acknowledged record must never
  drop it), a CRC-valid record with flags or an rtype this reader does not write, or a "tail" longer than one frame.
  An all-NUL complete frame (NTFS zero fill) is damage too: Codex ruling, no relaxation.
The file header decides first: an unknown frame_version / reserved bits is an UNKNOWN format (rule 1, ABORT-RO); a
wrong magic is damage, except a file that is entirely a torn header (shorter than a header frame and either NUL or a
prefix of the expected header), which the segment logic may treat as an interrupted create.
"""
from __future__ import annotations

import enum
import struct
import zlib
from dataclasses import dataclass

MAGIC = b'ZBNC'
FRAME_VERSION = 1
KIND_SEGMENT = 2
KIND_EVIDENCE = 5
FILE_HEADER = struct.Struct('<4sBBH')
REC = struct.Struct('<HBBII')
SYNC = 0xB25A
SYNC_BYTES = struct.pack('<H', SYNC)
RT_HEADER = 1
RT_EVENT = 10
RT_EVIDENCE_BODY = 30
KNOWN_RTYPES = frozenset({RT_HEADER, RT_EVENT, RT_EVIDENCE_BODY})
MAX_RECORD = 16 * 1024 * 1024                # one event / header record
MAX_TAIL = MAX_RECORD + 2 * REC.size            # a torn tail is at most one frame
MAX_EVIDENCE_BODY = MAX_TAIL


def file_header(kind):
    return FILE_HEADER.pack(MAGIC, kind, FRAME_VERSION, 0)


def _crc(rtype, flags, payload):
    return zlib.crc32(struct.pack('<BBI', rtype, flags, len(payload)) + payload) & 0xFFFFFFFF


def frame(rtype, payload):
    if not (isinstance(payload, bytes) and len(payload) <= (MAX_EVIDENCE_BODY if rtype == RT_EVIDENCE_BODY else MAX_RECORD)):
        raise ValueError('frame payload too large or not bytes')
    return REC.pack(SYNC, rtype, 0, len(payload), _crc(rtype, 0, payload)) + payload


@dataclass(frozen=True, slots=True)
class Rec:
    offset: int
    rtype: int
    flags: int
    payload: bytes

    @property
    def end(self):
        return self.offset + REC.size + len(self.payload)


def record_at(data, off, max_len=MAX_EVIDENCE_BODY):
    """The CRC-valid, complete record starting at `off`, or None."""
    if off + REC.size > len(data):
        return None
    sync, rtype, flags, length, crc = REC.unpack_from(data, off)
    if sync != SYNC or length > max_len or off + REC.size + length > len(data):
        return None
    payload = bytes(data[off + REC.size: off + REC.size + length])
    if _crc(rtype, flags, payload) != crc:
        return None
    return Rec(off, rtype, flags, payload)


def _valid_record_after(data, off):
    i = data.find(SYNC_BYTES, off + 1)
    while i != -1:
        if record_at(data, i) is not None:
            return i
        i = data.find(SYNC_BYTES, i + 1)
    return None


class HeaderState(enum.StrEnum):
    OK = 'ok'
    TORN = 'torn'                  # shorter than a file header and NUL / a prefix of the expected header
    ZERO = 'zero'                  # the whole file is NUL bytes (zero fill)
    BAD_MAGIC = 'bad_magic'        # damage
    WRONG_KIND = 'wrong_kind'      # damage
    UNKNOWN_FORMAT = 'unknown_format'   # frame_version / reserved bits this reader does not know: rule 1


@dataclass(frozen=True, slots=True)
class Scan:
    header: HeaderState
    frame_version: int | None
    records: tuple
    good_end: int                  # end offset of the last valid record (or of the file header)
    tail_offset: int | None        # torn tail start (bytes [tail_offset:] are the tail)
    damage: tuple | None           # (offset, why)

    @property
    def clean(self):
        return self.header is HeaderState.OK and self.tail_offset is None and self.damage is None


def classify_invalid(data, off):
    """Why the bytes at `off` (the first invalid record, with no valid record after it) are not a record:
    (is_tail, reason). Codex ruling (final, PR #38): a complete-length frame that fails its check is ALWAYS damage
    (HOLD / ABORT-RO with zero writes); only a short record header or a declared frame running past EOF is an incomplete
    tail. No zero-fill relaxation exists. To keep the mandatory invariant "recovery never turns a sent / unknown intent
    into durable_not_sent", a frame whose declared length runs past EOF but whose present bytes are exactly a complete
    record with a corrupted length field (its CRC verifies over them) is damage too: that is a flipped length, not an
    unfinished append."""
    rest = len(data) - off
    if rest < REC.size:
        return True, 'short record header'
    sync, rtype, flags, length, crc = REC.unpack_from(data, off)
    if off + REC.size + length <= len(data):
        return False, 'a complete-length record fails its check'
    payload = bytes(data[off + REC.size:])
    if _crc(rtype, flags, payload) == crc:
        return False, 'a complete record with a corrupted length field'
    if rest > MAX_TAIL:
        return False, 'invalid tail longer than one frame'
    return True, 'incomplete record'


def scan(data, kind):
    data = bytes(data)
    want = file_header(kind)
    if len(data) < FILE_HEADER.size:
        torn = data == want[:len(data)] or data.count(0) == len(data)
        return Scan(HeaderState.TORN if torn else HeaderState.BAD_MAGIC, None, (), 0, 0 if data else None,
                    None if torn else (0, 'short file header'))
    magic, fkind, fver, reserved = FILE_HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        if data.count(0) == len(data):
            return Scan(HeaderState.ZERO, None, (), 0, 0, None)
        return Scan(HeaderState.BAD_MAGIC, None, (), 0, None, (0, 'bad magic'))
    if fkind != kind:
        return Scan(HeaderState.WRONG_KIND, fver, (), 0, None, (0, f'file kind {fkind} where {kind} was expected'))
    if fver != FRAME_VERSION or reserved != 0:
        return Scan(HeaderState.UNKNOWN_FORMAT, fver, (), 0, None, None)
    recs, off = [], FILE_HEADER.size
    while off < len(data):
        r = record_at(data, off)
        if r is None:
            later = _valid_record_after(data, off)
            if later is not None:
                return Scan(HeaderState.OK, fver, tuple(recs), off, None, (off, f'invalid record before a valid one at {later}'))
            tail, why = classify_invalid(data, off)
            if not tail:
                return Scan(HeaderState.OK, fver, tuple(recs), off, None, (off, why))
            return Scan(HeaderState.OK, fver, tuple(recs), off, off, None)
        if r.flags != 0 or r.rtype not in KNOWN_RTYPES:
            return Scan(HeaderState.OK, fver, tuple(recs), off, None, (off, f'record type {r.rtype} / flags {r.flags}'))
        recs.append(r)
        off = r.end
    return Scan(HeaderState.OK, fver, tuple(recs), off, None, None)
