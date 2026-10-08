"""Strict JSON codec for domain documents.

Envelope: {"format": "zackbot.newcore", "schema": 1, "kind": "<kind>", "body": {...}}.

Decoding order (each step before any later one is attempted):
1. not an object, or `format` is not ours (a legacy state.json, anything else): ForeignDocument. Never imported.
2. `schema` not a JSON integer >= 0: UnknownSchema; newer than this build: FutureSchema. The body is NOT visited, so a
   future document is never judged as damage (NC-02 aborts read-only).
3. older schema: InvalidRecord (no migration exists for schema 1; migrations are NC-02's and write a new generation).
4. envelope keys / kind, then the body: exact key sets (unknown and missing keys rejected), JSON types checked against
   the field annotations (a Decimal is a canonical decimal STRING, never a JSON number; an int is an int, never a bool or
   float; a timestamp is an int, never an ISO string), then the record constructors run every invariant.

`loads` parses text leniently only to reach step 2 first: duplicate keys, NaN / Infinity, any JSON float and integers
beyond the int64 range are recorded and rejected as damage after the schema check. `dumps` is canonical: sorted keys, no
whitespace, ASCII, canonical decimals, so equal records give identical bytes.
"""
from __future__ import annotations

import dataclasses
import enum
import json
import re
import types
import typing
from decimal import Decimal, InvalidOperation

from . import account, decision, events, instrument, orders, portfolio, snapshot
from .base import INT64, Record, dec_str, field_spec, req
from .errors import ForeignDocument, FutureSchema, InvalidRecord, UnknownSchema

FORMAT = 'zackbot.newcore'
SCHEMA = 1
ENVELOPE = frozenset({'format', 'schema', 'kind', 'body'})
DECIMAL_RE = re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?')

KINDS = {
    'account': account.Account,
    'instrument_rules': instrument.InstrumentRules,
    'order_intent': orders.OrderIntent,
    'order_result': orders.OrderResult,
    'portfolio': portfolio.Portfolio,
    'decision': decision.Decision,
    'snapshot': snapshot.Snapshot,
    'high_water': snapshot.HighWater,
    'event.intent_recorded': events.IntentRecorded,
    'event.intent_state_changed': events.IntentStateChanged,
    'event.result_observed': events.ResultObserved,
    'event.decision_recorded': events.DecisionRecorded,
    'event.mode_changed': events.ModeChanged,
    'event.binding_changed': events.BindingChanged,
}
KIND_OF = {cls: k for k, cls in KINDS.items()}


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
    kind = KIND_OF.get(type(obj))
    req(kind is not None, 'encode', f'{type(obj).__name__} is not a document kind')
    return {'format': FORMAT, 'schema': SCHEMA, 'kind': kind, 'body': to_json(obj)}


def dumps(obj):
    return json.dumps(encode_document(obj), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


# ----------------------------------------------------------------------------------------------------------- decode
def _value(tp, v, path):
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        inner = next(a for a in typing.get_args(tp) if a is not type(None))
        return None if v is None else _value(inner, v, path)
    if origin is tuple:
        req(type(v) is list, path, f'not a list ({type(v).__name__})')
        el = typing.get_args(tp)[0]
        return tuple(_value(el, x, f'{path}[{i}]') for i, x in enumerate(v))
    if isinstance(tp, type) and issubclass(tp, Record):
        return from_json(tp, v, path)
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        req(type(v) is str, path, f'not a string ({type(v).__name__})')
        try:
            return tp(v)
        except ValueError:
            raise InvalidRecord(path, f'{v!r} is not a {tp.__name__}') from None
    if tp is Decimal:
        req(type(v) is str, path, f'a decimal is a JSON string, not {type(v).__name__}')
        req(len(v) <= 40 and DECIMAL_RE.fullmatch(v) is not None, path, f'{v!r} is not a canonical decimal')
        try:
            return Decimal(v)
        except InvalidOperation:       # unreachable after the regex; kept as a controlled failure
            raise InvalidRecord(path, f'{v!r} is not a decimal') from None
    if tp is int:
        req(type(v) is int, path, f'not an int ({type(v).__name__})')
        return v
    if tp in (str, bool):
        req(type(v) is tp, path, f'not a {tp.__name__} ({type(v).__name__})')
        return v
    raise InvalidRecord(path, f'unsupported field type {tp!r}')


def from_json(cls, d, path):
    req(type(d) is dict, path, f'not an object ({type(d).__name__})')
    spec = field_spec(cls)
    names = {name for name, _, _ in spec}
    extra = set(d) - names
    req(not extra, path, f'unknown keys {sorted(extra)}')
    missing = names - set(d)
    req(not missing, path, f'missing keys {sorted(missing)}')
    kw = {name: _value(tp, d[name], f'{path}.{name}') for name, tp, _ in spec}
    try:
        return cls(**kw)
    except InvalidRecord as ex:
        rel = ex.path.split('.', 1)[1] if '.' in ex.path else ''
        raise InvalidRecord(f'{path}.{rel}' if rel else path, ex.msg) from None


def check_header(doc):
    """Steps 1-3. Returns the kind. Never looks at the body."""
    if type(doc) is not dict or doc.get('format') != FORMAT:
        raise ForeignDocument('document.format', f'not a {FORMAT} document (legacy state is never imported)')
    if 'schema' not in doc:
        raise InvalidRecord('document.schema', 'missing schema header')
    v = doc['schema']
    if type(v) is not int or v < 0:
        raise UnknownSchema('document.schema', f'{v!r} is not a JSON integer >= 0: unknown format')
    if v > SCHEMA:
        raise FutureSchema('document.schema', f'schema {v} is newer than this build ({SCHEMA})')
    if v < SCHEMA:
        raise InvalidRecord('document.schema', f'schema {v} is older than {SCHEMA}; no migration exists')
    req(set(doc) == ENVELOPE, 'document', f'envelope keys {sorted(doc)}')
    req(doc['kind'] in KINDS, 'document.kind', f'unknown kind {doc["kind"]!r}')
    return doc['kind']


def decode_document(doc, *, expect=None):
    kind = check_header(doc)
    req(expect is None or kind == expect, 'document.kind', f'{kind!r} where {expect!r} was expected')
    return from_json(KINDS[kind], doc['body'], kind)


# ----------------------------------------------------------------------------------------------------------- text
class _Bad:
    """Marker for a JSON token that is damage (float / NaN / Infinity / huge int). Fails every typed field."""
    __slots__ = ('text',)

    def __init__(self, text):
        self.text = text


def loads(text, *, expect=None):
    """Strict decode of JSON text (str or bytes). Future / unknown schema wins over any damage in the body."""
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
    req(not problems, 'document', f'hostile JSON: {problems[0]}')
    return decode_document(doc, expect=expect)
