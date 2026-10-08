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
BAD_NUMBERS = ['NaN', 'Infinity', '-Infinity', '1e3', '1E+2', ' 1', '1 ', '1_0', '+1', '01', '1.', '.5', '1.50', '-0',
               '0x10', '', '1' * 16, '0.' + '1' * 13]


@pytest.mark.parametrize('bad', BAD_NUMBERS)
def test_decimal_strings_are_strict(bad):
    doc = encode_document(F.rules())
    doc['body']['tick_size'] = bad
    with pytest.raises(InvalidRecord) as e:
        decode_document(doc)
    assert e.value.path == 'instrument_rules.tick_size'


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


@pytest.mark.parametrize('field,bad', [('opened_at_ms', '2026-10-07T10:00:00Z'), ('opened_at_ms', 1791400000.5),
                                       ('opened_at_ms', 1791400000), ('opened_at_ms', True), ('opened_at_ms', -1),
                                       ('adds_done', True), ('adds_done', 2 ** 63)])
def test_timestamps_are_integer_utc_ms(field, bad):
    p, _ = F.single_lot_portfolio()
    doc = encode_document(p)
    doc['body']['positions'][0]['lots'][0][field] = bad
    with pytest.raises(InvalidRecord) as e:
        decode_document(doc)
    assert e.value.path.endswith(field)


def test_duplicate_keys_are_damage():
    text = dumps(F.rules())
    dup = text.replace('"min_qty":"0.01"', '"min_qty":"0.01","min_qty":"0.02"')
    with pytest.raises(InvalidRecord, match='duplicate key'):
        loads(dup)


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
