"""Regression tests for the Cowork attack on PR #38 (135 cases): the verified items E07, I05, S05, H05 and CP05/I03.
Each test here fails on ee79655 and passes after its fix; each has a mutation in nc01_mutations.py."""
import json

import pytest

import nc01_factories as F
from newcore.domain import DomainError, Outcome, decode_document, decode_result, encode_document, loads

# ----------------------------------------------------------------------------------------------------------- E07
ENVELOPE_FIELDS = ('format', 'schema_version', 'record_type', 'body')
WRONG = [[], {}, [1], {'a': 1}, 0, 1, -1, None, True, False, 1.5, '', 'x']


@pytest.mark.parametrize('field', ENVELOPE_FIELDS)
@pytest.mark.parametrize('value', WRONG, ids=repr)
def test_every_damaged_envelope_field_is_a_typed_failure(field, value):
    """E07: a damaged envelope field (record_type=[] crashed with an untyped TypeError) is always a typed failure, on
    every entry point: the dict decoder, text, bytes and the typed outcome API."""
    doc = encode_document(F.single_lot_portfolio()[0])
    if type(doc[field]) is type(value) and doc[field] == value:
        pytest.skip('the valid value itself')
    doc[field] = value
    with pytest.raises(DomainError):
        decode_document(doc)
    text = json.dumps(doc)
    for blob in (text, text.encode('utf-8')):
        with pytest.raises(DomainError):
            loads(blob)
        res = decode_result(blob)
        assert res.outcome is not Outcome.OK and res.record is None and isinstance(res.error, DomainError)


@pytest.mark.parametrize('doc', [[], 'x', 7, None, {1: 2}, {'format': 'zackbot.newcore', 'schema_version': 1,
                                                          'record_type': 'portfolio', 'body': {}, 3: 4}], ids=repr)
def test_non_dict_documents_and_non_text_keys_are_typed_failures(doc):
    with pytest.raises(DomainError):
        decode_document(doc)


# ----------------------------------------------------------------------------------------------------------- I05
def test_owner_kind_is_explicit_and_drives_ownership():
    """I05: what owns an intent is the typed owner_kind field; the id is opaque."""
    from decimal import Decimal as D
    from newcore.domain import InvalidRecord, IntentState, OwnerFamily, OwnerKind, Purpose
    from nc01_factories import replace
    p, ids = F.single_lot_portfolio(91, stop_state='confirmed', in_flight='add')
    acct, lt = p.account_id, p.lots[0]
    add = next(i for i in p.intents if i.purpose is Purpose.ADD)
    assert add.owner_kind is OwnerKind.LOT and add.family is OwnerFamily.LOT
    orphan = F.orphan_stop(ids, acct)
    assert orphan.owner_kind is OwnerKind.PORTFOLIO and orphan.family is OwnerFamily.ORPHAN and orphan.orphan
    with pytest.raises(InvalidRecord, match='cancel-only'):          # portfolio-owned work may only be cancelling
        replace(orphan, state=IntentState.WORKING)
    for bad in (dict(owner_kind=None),                                # an owner id without its kind
                dict(owner_kind=OwnerKind.ENTRY_INTENT),              # an add is never owned by an entry
                dict(owner_kind=OwnerKind.PORTFOLIO)):                # kind and id family disagree (lot id, pf kind)
        with pytest.raises(InvalidRecord):
            replace(add, **bad)
    entry = F.market_entry(ids, acct)
    with pytest.raises(InvalidRecord):                                # an ENTRY owns itself: no kind
        replace(entry, owner_kind=OwnerKind.LOT)
    stop = F.intent(ids, acct, Purpose.PROTECT, entry.symbol, entry.side, D('1'), owner_id=entry.intent_id,
                    owner_kind=OwnerKind.ENTRY_INTENT, stop_price=D('0.1'), state=IntentState.CANCELLING)
    replace(p, intents=p.intents + (entry, stop))                     # resolved by kind: the entry intent
    with pytest.raises(InvalidRecord):
        replace(p, intents=p.intents + (entry, replace(stop, owner_kind=OwnerKind.LOT)))


def test_no_domain_logic_parses_an_id():
    """I05: no newcore source slices or prefix-tests an id string (startswith / endswith / split / [:n] on *_id)."""
    import ast
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'newcore')
    hits = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dp, fn)
            for node in ast.walk(ast.parse(open(path, encoding='utf-8').read())):
                if isinstance(node, ast.Attribute) and node.attr in ('startswith', 'endswith', 'partition', 'split'):
                    target = ast.unparse(node.value)
                    if 'id' in target:
                        hits.append(f'{fn}:{node.lineno} {target}.{node.attr}')
                if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice) and '_id' in ast.unparse(node.value):
                    hits.append(f'{fn}:{node.lineno} {ast.unparse(node)}')
    assert hits == []
