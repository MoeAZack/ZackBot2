"""Record 0 of every segment: the typed version header (nc02_design.md 3.2 / 3.4), checked before any body is decoded.

    {"account_id": "acct_...", "aggregate_id": "pf_...", "format": "zackbot.newcore.journal", "format_version": 1,
     "lsn_base": 41, "min_reader_version": 1, "seals": [{"segment_no": 1, "sealed_len": 40960, "sha256": "..."}],
     "segment_no": 2, "writer_build": "nc-02a"}

Canonical UTF-8 JSON (sorted keys, no whitespace, ASCII). `seals` lists EVERY earlier segment of the journal with the
length a reader must stop at and the sha256 of exactly those bytes: segment N+1's header is what seals segment N (no
HEAD file in NC-02a). A torn tail or an interrupted segment create is sealed at its last good byte; the bytes after it
stay in place (copied to evidence first: design D3 seal-and-roll, never truncate).

Version check order (rule 1, zero writes): `format_version` and `min_reader_version` must be JSON integers >= 0 (not a
bool, float, string or null) else the format is UNKNOWN; either one above READER_VERSION is FUTURE; below the first
format (0) is UNKNOWN too (there is no older journal format to migrate). Both mean ABORT-RO. Only then is the rest of
the header validated, and any problem there is damage.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass

FORMAT = 'zackbot.newcore.journal'
EVIDENCE_FORMAT = 'zackbot.newcore.evidence'
FORMAT_VERSION = 1            # what this build writes
READER_VERSION = 1            # the newest format this build reads
HEADER_KEYS = frozenset({'account_id', 'aggregate_id', 'format', 'format_version', 'lsn_base', 'min_reader_version',
                         'seals', 'segment_no', 'writer_build'})
SEAL_KEYS = frozenset({'segment_no', 'sealed_len', 'sha256'})
ID_RE = re.compile(r'(acct|pf)_[0-9a-f]{32}')
SHA_RE = re.compile(r'[0-9a-f]{64}')
EMPTY_SHA = hashlib.sha256(b'').hexdigest()
INT64 = 2 ** 63 - 1


class VersionVerdict(enum.StrEnum):
    OK = 'ok'
    FUTURE = 'future'
    UNKNOWN = 'unknown'


class HeaderError(ValueError):
    """Header damage (current format). Never names content."""


@dataclass(frozen=True, slots=True)
class Seal:
    segment_no: int
    sealed_len: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SegmentHeader:
    account_id: str
    aggregate_id: str
    segment_no: int
    lsn_base: int
    seals: tuple
    writer_build: str


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')


def encode_header(h):
    return canonical_json({
        'account_id': h.account_id, 'aggregate_id': h.aggregate_id, 'format': FORMAT, 'format_version': FORMAT_VERSION,
        'lsn_base': h.lsn_base, 'min_reader_version': FORMAT_VERSION,
        'seals': [{'segment_no': s.segment_no, 'sealed_len': s.sealed_len, 'sha256': s.sha256} for s in h.seals],
        'segment_no': h.segment_no, 'writer_build': h.writer_build})


class _Bad:
    __slots__ = ()


def strict_json(payload):
    """(document, problems): duplicate keys, NaN / Infinity, every float and integers outside int64 are recorded as
    problems (the value becomes a marker that fails every type check). Raises HeaderError for non-JSON / non-UTF-8."""
    problems = []

    def pairs(kv):
        out = {}
        for k, v in kv:
            if k in out:
                problems.append('duplicate key')
            out[k] = v
        return out

    def bad(kind):
        def hook(_s):
            problems.append(kind)
            return _Bad()
        return hook

    def parse_int(s):
        if len(s) > 20:
            problems.append('huge integer')
            return _Bad()
        n = int(s)
        if not -INT64 - 1 <= n <= INT64:
            problems.append('integer outside int64')
            return _Bad()
        return n

    try:
        doc = json.loads(payload.decode('utf-8'), object_pairs_hook=pairs, parse_float=bad('float'),
                         parse_constant=bad('constant'), parse_int=parse_int)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise HeaderError('header record is not one JSON document') from None
    return doc, problems


def _is_count(v):
    return type(v) is int and v >= 0


def check_version(doc):
    """Rule 1 on a parsed header document (before anything else in it is trusted)."""
    if type(doc) is not dict or 'format_version' not in doc or 'min_reader_version' not in doc:
        return VersionVerdict.OK          # not a header at all: decided as damage by decode_header
    fv, mr = doc['format_version'], doc['min_reader_version']
    if not (_is_count(fv) and _is_count(mr)) or fv == 0:
        return VersionVerdict.UNKNOWN
    if fv > READER_VERSION or mr > READER_VERSION:
        return VersionVerdict.FUTURE
    return VersionVerdict.OK


def decode_header(doc, problems):
    """The SegmentHeader of a version-checked document. Raises HeaderError on any damage."""
    if type(doc) is not dict or set(doc) != HEADER_KEYS:
        raise HeaderError('header keys')
    if problems:
        raise HeaderError(f'hostile JSON in header ({problems[0]})')
    if doc['format'] != FORMAT:
        raise HeaderError('not a journal segment header')
    for k in ('account_id', 'aggregate_id'):
        if type(doc[k]) is not str or not ID_RE.fullmatch(doc[k]):
            raise HeaderError(f'{k} is not an id')
    if not (_is_count(doc['segment_no']) and doc['segment_no'] >= 1 and _is_count(doc['lsn_base'])):
        raise HeaderError('segment_no / lsn_base')
    wb = doc['writer_build']
    if type(wb) is not str or not (0 < len(wb) <= 64 and wb.isprintable()):
        raise HeaderError('writer_build')
    seals = doc['seals']
    if type(seals) is not list:
        raise HeaderError('seals')
    out = []
    for i, s in enumerate(seals):
        if type(s) is not dict or set(s) != SEAL_KEYS or s['segment_no'] != i + 1 or not _is_count(s['sealed_len']) \
                or type(s['sha256']) is not str or not SHA_RE.fullmatch(s['sha256']):
            raise HeaderError(f'seal {i + 1}')
        out.append(Seal(s['segment_no'], s['sealed_len'], s['sha256']))
    if len(out) != doc['segment_no'] - 1:
        raise HeaderError('a header seals every earlier segment')
    return SegmentHeader(doc['account_id'], doc['aggregate_id'], doc['segment_no'], doc['lsn_base'], tuple(out), wb)


def header_frame_max(segment_no):
    """Upper bound of file header + header frame for segment `segment_no` (a torn create is never longer)."""
    return 8 + 12 + 512 + 160 * (segment_no - 1)
