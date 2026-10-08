"""Strict, versioned JSON codec for domain documents (contract section 5). No pickle, no permissive construction.

Envelope: {"format": "zackbot.newcore", "schema_version": 1, "record_type": "<type>", "body": {...}}.

Decoding order (each step before any later one is attempted):
1. not an object, or `format` is not ours (a legacy state.json, anything else): ForeignDocument. Never imported.
2. `schema_version` is not exactly SCHEMA_VERSION: UnsupportedVersion (FutureSchema / UnknownSchema / OlderSchema). The
   body is NOT visited, so an unsupported document is never judged as damage; what to do is NC-02 policy.
3. envelope keys / record_type, then the body: exact key sets (unknown, missing and duplicate keys rejected), JSON types
   checked against the field annotations (a Decimal is a canonical decimal STRING, never a JSON number; an int is an
   int, never a bool or float; a timestamp is an int, never an ISO string), then the record constructors run every
   invariant, cross-record references included.

`loads` (text or bytes) parses leniently only to reach step 2 first: duplicate keys, NaN / Infinity, any JSON float and
integers beyond int64 are recorded and rejected as damage after the version check; truncated input is a typed failure.
`decode_result` returns a typed outcome instead of raising (OK / INVALID / UNSUPPORTED_VERSION / FOREIGN).

Canonical bytes (contract section 5): `canonical_bytes(record)` is the ONE serialization used for identity and hashing
(UTF-8 JSON, sorted keys, no insignificant whitespace, ASCII escapes, canonical decimals), and `contract_sha256(record)`
is the lowercase hex SHA-256 over exactly those bytes. Equal records give identical bytes.
"""
from __future__ import annotations

import dataclasses
import enum
import functools
import hashlib
import json
import re
import types
import typing
from decimal import Decimal

from . import account, decision, events, instrument, orders, portfolio, snapshot
from .base import INT64, Record, canonical_decimal, dec_str, field_spec, req
from .errors import DomainError, ForeignDocument, FutureSchema, InvalidRecord, OlderSchema, UnknownSchema, \
    UnsupportedVersion

FORMAT = 'zackbot.newcore'
SCHEMA_VERSION = 1
ENVELOPE = frozenset({'format', 'schema_version', 'record_type', 'body'})
DECIMAL_RE = re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?')    # contract invariant 2, ASCII digits only
DECIMAL_MAX_CHARS = 64                     # 38 significant digits within |adjusted exponent| <= 18 fit in 59

RECORD_TYPES = {
    'account': account.Account,
    'instrument_rules': instrument.InstrumentRules,
    'order_intent': orders.OrderIntent,
    'order_result': orders.OrderResult,
    'portfolio': portfolio.Portfolio,
    'decision': decision.Decision,
    'decision_key': decision.DecisionKey,
    'snapshot': snapshot.Snapshot,
    'high_water': snapshot.HighWater,
    'event_intent_recorded': events.IntentRecorded,
    'event_intent_state_changed': events.IntentStateChanged,
    'event_result_observed': events.ResultObserved,
    'event_decision_recorded': events.DecisionRecorded,
    'event_mode_changed': events.ModeChanged,
    'event_binding_changed': events.BindingChanged,
}
TYPE_OF = {cls: k for k, cls in RECORD_TYPES.items()}


# ----------------------------------------------------------------------------------------------------------- encode
def to_json(obj):
    if isinstance(obj, Record):
        return {name: to_json(getattr(obj, name)) for name, _, _ in field_spec(type(obj))}
    if isinstance(obj, enum.Enum):
        return obj.value
    if type(obj) is Decimal:
        return dec_str(obj)
    if type(obj) is tuple:
        return [to_json(x) for x in obj]
    if obj is None or type(obj) in (str, int, bool):
        return obj
    raise InvalidRecord('encode', f'not encodable: {type(obj).__name__}')


def encode_document(obj):
    rtype = TYPE_OF.get(type(obj))
    req(rtype is not None, 'encode', f'{type(obj).__name__} is not a document record type')
    return {'format': FORMAT, 'schema_version': SCHEMA_VERSION, 'record_type': rtype, 'body': to_json(obj)}


def dumps(obj):
    return json.dumps(encode_document(obj), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def canonical_bytes(obj):
    """The single canonical serialization of a document record (envelope included)."""
    return dumps(obj).encode('utf-8')


def contract_sha256(obj):
    """Lowercase hex SHA-256 of canonical_bytes(obj): the only domain hash input."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


# ----------------------------------------------------------------------------------------------------------- decode
def _bad(path, msg):
    raise InvalidRecord(path, msg)


def _decoder(tp):
    """A decoder closure for one annotation, built once per field (the decode hot path does no type introspection)."""
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        inner = _decoder(next(a for a in typing.get_args(tp) if a is not type(None)))
        return lambda v, path: None if v is None else inner(v, path)
    if origin is tuple:
        el = _decoder(typing.get_args(tp)[0])

        def dec_tuple(v, path):
            if type(v) is not list:
                _bad(path, f'not a list ({type(v).__name__})')
            return tuple(el(x, f'{path}[{i}]') for i, x in enumerate(v))
        return dec_tuple
    if isinstance(tp, type) and issubclass(tp, Record):
        return lambda v, path: from_json(tp, v, path)
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        members = {m.value: m for m in tp}

        def dec_enum(v, path):
            m = members.get(v) if type(v) is str else None
            if m is None:
                _bad(path, f'{v!r} is not a {tp.__name__}')
            return m
        return dec_enum
    if tp is Decimal:
        def dec_decimal(v, path):
            if type(v) is not str or len(v) > DECIMAL_MAX_CHARS or DECIMAL_RE.fullmatch(v) is None:
                _bad(path, f'{v!r:.40} is not a canonical bounded decimal string (a JSON number is never accepted)')
            d = canonical_decimal(Decimal(v), path)
            if format(d, 'f') != v:
                _bad(path, f'{v!r} is not the canonical spelling (trailing zeroes / negative zero)')
            return d
        return dec_decimal
    if tp in (int, str, bool):
        def dec_plain(v, path):
            if type(v) is not tp:
                _bad(path, f'not a {tp.__name__} ({type(v).__name__})')
            return v
        return dec_plain
    raise TypeError(f'unsupported field type {tp!r}')


@functools.cache
def _record_decoder(cls):
    spec = tuple((name, _decoder(tp)) for name, tp, _ in field_spec(cls))
    return spec, frozenset(name for name, _ in spec)


def from_json(cls, d, path):
    if type(d) is not dict:
        _bad(path, f'not an object ({type(d).__name__})')
    spec, names = _record_decoder(cls)
    if d.keys() != names:
        extra, missing = set(d) - names, names - set(d)
        _bad(path, f'unknown keys {sorted(map(repr, extra))}' if extra else f'missing keys {sorted(missing)}')
    kw = {name: dec(d[name], f'{path}.{name}') for name, dec in spec}
    try:
        return cls(**kw)
    except InvalidRecord as ex:
        rel = ex.path.split('.', 1)[1] if '.' in ex.path else ''
        raise InvalidRecord(f'{path}.{rel}' if rel else path, ex.msg) from None


def check_header(doc):
    """Steps 1-2 (+ envelope shape). Returns the record type. Never looks at the body."""
    if type(doc) is not dict or doc.get('format') != FORMAT:
        raise ForeignDocument('document.format', f'not a {FORMAT} document (legacy state is never imported)')
    if 'schema_version' not in doc:
        raise InvalidRecord('document.schema_version', 'missing')
    v = doc['schema_version']
    if type(v) is not int or v < 0:
        raise UnknownSchema('document.schema_version', f'{v!r} is not a JSON integer >= 0: unknown format')
    if v > SCHEMA_VERSION:
        raise FutureSchema('document.schema_version', f'{v} is newer than this build ({SCHEMA_VERSION})')
    if v < SCHEMA_VERSION:
        raise OlderSchema('document.schema_version', f'{v} is older than {SCHEMA_VERSION}; migration is NC-02 policy')
    if set(doc) != ENVELOPE:
        _bad('document', f'envelope keys {sorted(map(repr, doc))}')
    rtype = doc['record_type']
    if type(rtype) is not str or rtype not in RECORD_TYPES:     # type first: never hash an unhashable wire value
        _bad('document.record_type', f'unknown record type {rtype!r:.80}')
    return rtype


def decode_document(doc, *, expect=None):
    rtype = check_header(doc)
    req(expect is None or rtype == expect, 'document.record_type', f'{rtype!r} where {expect!r} was expected')
    return from_json(RECORD_TYPES[rtype], doc['body'], rtype)


# ----------------------------------------------------------------------------------------------------------- text
class _Bad:
    """Marker for a JSON token that is damage (float / NaN / Infinity / huge int). Fails every typed field."""
    __slots__ = ('text',)

    def __init__(self, text):
        self.text = text


def loads(text, *, expect=None):
    """Strict decode of JSON text (str or bytes). An unsupported version wins over any damage in the body."""
    problems = []

    def bad(kind):
        def hook(s):
            problems.append(f'{kind} {s[:40]!r}')
            return _Bad(s)
        return hook

    def pairs(kv):
        out = {}
        for k, v in kv:
            if k in out:
                problems.append(f'duplicate key {k!r}')
            out[k] = v
        return out

    def parse_int(s):
        try:
            n = int(s)
        except ValueError:
            problems.append(f'integer too long ({len(s)} digits)')
            return _Bad(s)
        if not INT64[0] <= n <= INT64[1]:
            problems.append('integer outside int64')
            return _Bad(s)
        return n

    if isinstance(text, bytes):
        try:
            text = text.decode('utf-8')
        except UnicodeDecodeError:
            raise InvalidRecord('document', 'not UTF-8') from None
    req(type(text) is str, 'document', 'not text')
    try:
        doc = json.loads(text, object_pairs_hook=pairs, parse_float=bad('float'), parse_constant=bad('constant'),
                         parse_int=parse_int)
    except (ValueError, RecursionError) as ex:
        raise InvalidRecord('document', f'not one JSON document ({type(ex).__name__})') from None
    check_header(doc)
    if problems:
        raise InvalidRecord('document', f'hostile JSON: {problems[0]}')
    return decode_document(doc, expect=expect)


class Outcome(enum.StrEnum):
    OK = 'ok'
    INVALID = 'invalid'
    UNSUPPORTED_VERSION = 'unsupported_version'
    FOREIGN = 'foreign'


@dataclasses.dataclass(frozen=True, slots=True)
class DecodeResult:
    outcome: Outcome
    record: object = None
    error: DomainError | None = None


def decode_result(text, *, expect=None):
    """`loads` as a typed outcome: never raises a DomainError, never yields empty ownership for a failure."""
    try:
        return DecodeResult(Outcome.OK, loads(text, expect=expect))
    except UnsupportedVersion as ex:
        return DecodeResult(Outcome.UNSUPPORTED_VERSION, error=ex)
    except ForeignDocument as ex:
        return DecodeResult(Outcome.FOREIGN, error=ex)
    except InvalidRecord as ex:
        return DecodeResult(Outcome.INVALID, error=ex)
