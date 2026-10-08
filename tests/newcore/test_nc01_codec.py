"""NC-01 strict codec (contract section 5): round trips, deterministic snapshots, hostile JSON, unknown / missing /
duplicate fields, unsupported versions (body never visited), foreign (legacy) documents, typed decode outcomes."""
import copy
import json
import os

import pytest

import nc01_factories as F
from newcore.domain import (DomainError, ForeignDocument, FutureSchema, InvalidRecord, OlderSchema, Outcome,
                            UnknownSchema, UnsupportedVersion, decode_document, decode_result, dumps, encode_document,
                            loads)
from newcore.domain.codec import RECORD_TYPES

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = F.samples()


@pytest.mark.parametrize('rec', SAMPLES, ids=lambda r: type(r).__name__)
def test_round_trip_and_canonical_bytes(rec):
    text = dumps(rec)
    back = loads(text)
    assert back == rec and type(back) is type(rec)
    assert dumps(back) == text                                       # byte-identical re-encode
    assert json.loads(text) == encode_document(rec)


def test_every_record_type_has_a_sample():
    assert {type(s) for s in SAMPLES} == set(RECORD_TYPES.values())


def test_serialization_snapshot_is_stable():
    """The pinned canonical bytes decode and re-encode byte-identically, and the deterministic factories still produce
    exactly them: a renamed field, a reordered enum value or a changed decimal spelling fails here."""
    lines = open(os.path.join(HERE, F.SNAPSHOT_FILE), encoding='ascii').read().splitlines()
    assert len(lines) == len(SAMPLES)
    for line in lines:
        assert dumps(loads(line)) == line
    assert F.snapshot_lines() == lines


def test_equal_values_encode_identically():
    from decimal import Decimal as D
    r = F.rules()
    a = F.replace(r, min_notional=D('5.00'))
    assert a == r and dumps(a) == dumps(r)                          # '5.00' and '5' are one canonical spelling


# ----------------------------------------------------------------------------------------------------------- numbers
# contract invariant 2: exactly ^-?(0|[1-9][0-9]*)(\.[0-9]+)?$ in canonical form, <= 38 significant digits,
# |adjusted exponent| <= 18
BAD_NUMBERS = ['NaN', 'Infinity', '-Infinity', 'sNaN', '1e3', '1E+2', '1E+999999', ' 1', '1 ', '1_0', '+1', '01', '1.',
               '.5', '1.50', '1.0', '10.0', '-0', '-0.0', '0.0', '0x10', '', '\u0661', '1\u0662', '\uff11', '1,5',
               '1' * 20, '0.' + '0' * 18 + '1', '1.' + '1' * 38, '9' * 70]
GOOD_NUMBERS = ['0', '1', '-1', '0.5', '1' * 19, '0.' + '0' * 17 + '1', '1.' + '1' * 37, '123456789.123456789']


@pytest.mark.parametrize('bad', BAD_NUMBERS)
def test_decimal_strings_are_strict(bad):
    doc = encode_document(F.rules())
    doc['body']['tick_size'] = bad
    with pytest.raises(InvalidRecord) as e:
        decode_document(doc)
    assert e.value.path == 'instrument_rules.tick_size'


@pytest.mark.parametrize('good', GOOD_NUMBERS)
def test_canonical_decimal_strings_round_trip(good):
    doc = encode_document(F.rules())
    doc['body']['min_notional'] = good
    if good.startswith('-'):
        with pytest.raises(InvalidRecord, match='must be >= 0'):
            decode_document(doc)
        return
    rec = decode_document(doc)
    assert encode_document(rec)['body']['min_notional'] == good


@pytest.mark.parametrize('bad', [1.5, 1, True, None, [], {}], ids=repr)
def test_json_numbers_and_other_types_are_never_decimals(bad):
    doc = encode_document(F.rules())
    doc['body']['step_size'] = bad
    with pytest.raises(InvalidRecord):
        decode_document(doc)


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', '1.0', '1e2', '1' * 30])
def test_hostile_json_tokens_are_damage(token):
    text = dumps(F.rules()).replace('"min_notional":"5"', f'"min_notional":{token}')
    assert token in text
    with pytest.raises(InvalidRecord, match='hostile JSON'):
        loads(text)


@pytest.mark.parametrize('field,bad', [('opened_at_ms', '2026-10-07T10:00:00Z'), ('opened_at_ms', '2026-10-07'),
                                       ('opened_at_ms', '2026-10-07 13:00'), ('opened_at_ms', 1791400000.5),
                                       ('opened_at_ms', 1791400000), ('opened_at_ms', True), ('opened_at_ms', -1),
                                       ('opened_at_ms', 946684799999), ('opened_at_ms', 4102444800000),
                                       ('opened_at_ms', None), ('opened_at_ms', float('nan')), ('adds_done', True),
                                       ('adds_done', 2 ** 63)])
def test_timestamps_are_integer_utc_ms(field, bad):
    p, _ = F.single_lot_portfolio()
    doc = encode_document(p)
    doc['body']['positions'][0]['lots'][0][field] = bad
    with pytest.raises(InvalidRecord) as e:
        decode_document(doc)
    assert e.value.path.endswith(field)


@pytest.mark.parametrize('ms', [946684800000, 4102444799999])
def test_timestamp_bounds_are_inclusive(ms):
    p, _ = F.single_lot_portfolio()
    doc = encode_document(p)
    lot = doc['body']['positions'][0]['lots'][0]
    lot['opened_at_ms'] = lot['fills'][0]['at_ms'] = ms
    assert decode_document(doc).lots[0].opened_at_ms == ms


def test_canonical_bytes_and_hash_api():
    from newcore.domain import canonical_bytes, contract_sha256
    import hashlib
    for rec in SAMPLES:
        b = canonical_bytes(rec)
        assert isinstance(b, bytes) and b == dumps(rec).encode('utf-8') and b'": ' not in b and b', "' not in b
        assert contract_sha256(rec) == hashlib.sha256(b).hexdigest() and contract_sha256(rec).islower()
        assert loads(b) == rec                                         # the bytes entry point
    r = F.rules()
    assert contract_sha256(r) == contract_sha256(F.replace(r, tick_size=F.D('0.010')))


@pytest.mark.parametrize('cut', [1, 2, 10, 100])
def test_truncated_input_is_a_typed_failure(cut):
    text = dumps(F.single_lot_portfolio()[0])
    res = decode_result(text[:-cut].encode('utf-8'))
    assert res.outcome is Outcome.INVALID and res.record is None


def test_torn_document_never_gets_business_defaults():
    """A missing optional / flag / collection field is damage, never None / False / 0 / () / KNOWN_EMPTY."""
    p, _ = F.single_lot_portfolio(in_flight='add')
    for path in (('positions', 0, 'lots', 0, 'in_flight'), ('positions', 0, 'lots', 0, 'tp1_done'),
                 ('positions', 0, 'lots', 0, 'adds_done'), ('positions', 0, 'lots', 0, 'ladder_done'),
                 ('entry_stops',), ('hold_kind',), ('ownership',), ('intents', 0, 'seen_qty')):
        doc = encode_document(p)
        node = doc['body']
        for k in path[:-1]:
            node = node[k]
        del node[path[-1]]
        with pytest.raises(InvalidRecord, match='missing keys'):
            decode_document(doc)


def test_duplicate_keys_are_damage():
    text = dumps(F.rules())
    dup = text.replace('"min_qty":"0.01"', '"min_qty":"0.01","min_qty":"0.02"')
    with pytest.raises(InvalidRecord, match='duplicate key'):
        loads(dup)
    with pytest.raises(InvalidRecord, match='duplicate key'):
        loads(dup.encode('utf-8'))                                      # the bytes entry point uses the same hook
    same = text.replace('"min_qty":"0.01"', '"min_qty":"0.01","min_qty":"0.01"')
    with pytest.raises(InvalidRecord, match='duplicate key'):           # even an identical duplicate
        loads(same)


@pytest.mark.parametrize('blob', [b'', b'\x00' * 64, b'{"a":1} {"b":2}', b'\xff\xfe', b'[' * 5000, b'null'])
def test_garbage_is_a_typed_failure(blob):
    with pytest.raises(DomainError):
        loads(blob)


# ----------------------------------------------------------------------------------------------------------- keys
def _paths(node, path=()):
    """Every object-key path in an encoded document body."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (k,)
            yield from _paths(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _paths(v, path + (i,))


def _parent(doc, path):
    node = doc['body']
    for k in path[:-1]:
        node = node[k]
    return node


@pytest.mark.parametrize('rec', SAMPLES, ids=lambda r: type(r).__name__)
def test_every_missing_or_unknown_key_is_rejected(rec):
    base = encode_document(rec)
    paths = list(_paths(base['body']))
    assert paths
    for path in paths:
        doc = copy.deepcopy(base)
        del _parent(doc, path)[path[-1]]
        with pytest.raises(InvalidRecord, match='missing keys'):
            decode_document(doc)
        doc = copy.deepcopy(base)
        _parent(doc, path)['surprise_field'] = None
        with pytest.raises(InvalidRecord, match='unknown keys'):
            decode_document(doc)


def test_invalid_enum_and_reason_values():
    doc = encode_document(F.market_entry(F.Ids(3), F.Ids(4).id('acct')))
    for field, bad in (('purpose', 'ENTRY'), ('state', 'armed'), ('reason', 'UNMAPPED:flatten'), ('reason', 'other.other'),
                       ('side', 'long'), ('order_type', 'limit_gtx')):
        d = copy.deepcopy(doc)
        d['body'][field] = bad
        with pytest.raises(InvalidRecord) as e:
            decode_document(d)
        assert e.value.path == f'order_intent.{field}'


def test_cross_record_reference_failure_is_rejected_at_decode():
    p, _ = F.single_lot_portfolio(stop_state='confirmed')
    doc = encode_document(p)
    doc['body']['intents'] = []                                     # the stop's carrying order disappears
    with pytest.raises(InvalidRecord, match='names no open intent'):
        decode_document(doc)


# ----------------------------------------------------------------------------------------------------------- versions
class _Exploding(dict):
    """A body that fails the test if anything looks at it."""
    def __getitem__(self, k):
        raise AssertionError('body visited')

    def keys(self):
        raise AssertionError('body visited')

    def __iter__(self):
        raise AssertionError('body visited')


@pytest.mark.parametrize('version,exc', [(2, FutureSchema), (99, FutureSchema), (0, OlderSchema), ('1', UnknownSchema),
                                         (1.0, UnknownSchema), (True, UnknownSchema), (None, UnknownSchema),
                                         (-1, UnknownSchema)], ids=repr)
def test_unsupported_version_is_detected_before_the_body(version, exc):
    doc = {'format': 'zackbot.newcore', 'schema_version': version, 'record_type': 'from-the-future', 'body': _Exploding()}
    with pytest.raises(exc) as e:
        decode_document(doc)
    assert isinstance(e.value, UnsupportedVersion) and not isinstance(e.value, InvalidRecord)


def test_future_version_wins_over_hostile_body_text():
    text = '{"format":"zackbot.newcore","schema_version":2,"record_type":"x","body":{"a":NaN,"a":1.5,"b":1' + '0' * 30 + '}}'
    res = decode_result(text)
    assert res.outcome is Outcome.UNSUPPORTED_VERSION and res.record is None
    assert res.error.direction == 'newer'


def test_decode_result_outcomes_are_typed():
    good = dumps(F.rules())
    assert decode_result(good).outcome is Outcome.OK
    assert decode_result(good.replace('"schema_version":1', '"schema_version":7')).outcome is Outcome.UNSUPPORTED_VERSION
    assert decode_result(good.replace('"tick_size":"0.01"', '"tick_size":"-1"')).outcome is Outcome.INVALID
    assert decode_result('{"schema_version":1,"lots":{}}').outcome is Outcome.FOREIGN
    assert decode_result('{"version":1}').record is None


def test_legacy_state_is_never_imported():
    legacy = {'schema_version': 1, 'lots': {'S1|SOLUSDT|LONG|1791400000': {'qty': 1.5, 'avg': 100.0, 'stop': 95.0}},
              'unconfirmed_entries': {}, 'resting_entries': {}, 'pending_entries': {}, 'orphans': []}
    for doc in (legacy, {'format': 'zackbot', **legacy}, [legacy]):
        with pytest.raises(ForeignDocument):
            decode_document(doc)
    assert decode_result(json.dumps(legacy)).outcome is Outcome.FOREIGN


def test_wrong_record_type_is_rejected():
    with pytest.raises(InvalidRecord):
        loads(dumps(F.rules()), expect='portfolio')
    doc = encode_document(F.rules())
    doc['record_type'] = 'portfolio'
    with pytest.raises(InvalidRecord):
        decode_document(doc)
