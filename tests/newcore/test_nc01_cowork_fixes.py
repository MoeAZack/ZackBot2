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
    """I05: no newcore/domain source slices or prefix-tests an id string (startswith / endswith / split / [:n] on *_id).
    Scoped to the NC-01 domain (Codex ruling); newcore/ports derives ids and is checked by its own tests."""
    import ast
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'newcore', 'domain')
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


# ----------------------------------------------------------------------------------------------------------- S05
def _with_reduce(qty, *, extra=(), purpose=None):
    from decimal import Decimal as D
    from newcore.domain import IntentState, Purpose
    from nc01_factories import replace
    p, ids = F.single_lot_portfolio(92, stop_state='confirmed')            # one lot of 1.5
    lt = p.lots[0]
    red = F.intent(ids, p.account_id, purpose or Purpose.REDUCE, lt.symbol, lt.side, D(qty), owner_id=lt.lot_id,
                   state=IntentState.SUBMITTED)
    lot = replace(lt, in_flight=red.intent_id)
    return lambda: replace(p, positions=(replace(p.positions[0], lots=(lot,)),),
                           intents=p.intents + (red,) + tuple(f(ids, p, lt) for f in extra))


def test_a_reducing_intent_is_bounded_by_its_lot():
    """S05: a lot of 1.5 with a REDUCE of 1000 built a Portfolio."""
    from newcore.domain import InvalidRecord, Purpose
    for qty in ('1000', '1.51'):
        for purpose in (Purpose.REDUCE, Purpose.CLOSE):
            with pytest.raises(InvalidRecord, match='exceeds its lot|exceed the lot qty'):
                _with_reduce(qty, purpose=purpose)()
    _with_reduce('1.5')()                                                     # exactly the lot: valid
    _with_reduce('0.5')()


def test_reducing_intents_are_bounded_net_of_each_other():
    """An uncarried (cancelling) reduce of the same lot still counts until it is final."""
    from decimal import Decimal as D
    from newcore.domain import IntentState, InvalidRecord, OwnerKind, Purpose
    old = lambda ids, p, lt, q='1': F.intent(ids, p.account_id, Purpose.REDUCE, lt.symbol, lt.side, D(q),   # noqa: E731
                                             owner_id=lt.lot_id, state=IntentState.CANCELLING)
    with pytest.raises(InvalidRecord, match='exceed the lot qty'):
        _with_reduce('1', extra=(old,))()                                    # 1 + 1 > 1.5
    _with_reduce('1', extra=(lambda ids, p, lt: old(ids, p, lt, '0.5'),))()   # 1 + 0.5 = 1.5
    # an UNLINKED old close + new close on one lot stays invalid (1.5 + 1.5 > 1.5); the explicitly linked cancel-replace
    # pair is the one representable overlap - see test_nc01_cancel_replace.py (Codex P1 on af4e5f3)
    old_close = lambda ids, p, lt: F.intent(ids, p.account_id, Purpose.CLOSE, lt.symbol, lt.side, lt.qty,   # noqa: E731
                                            owner_id=lt.lot_id, state=IntentState.CANCELLING)
    with pytest.raises(InvalidRecord, match='exceed the lot qty'):
        _with_reduce('1.5', purpose=Purpose.CLOSE, extra=(old_close,))()


@pytest.mark.parametrize('purpose', ['close', 'reduce'])
@pytest.mark.parametrize('held', [True, False], ids=['with_position', 'no_position'])
def test_orphan_reducing_cancel_work_is_representable(purpose, held):
    """Cowork re-check of S05: a portfolio-owned (orphan) cancel-only CLOSE / REDUCE is valid with or without a position
    on its symbol; it is not counted against any lot or position because it can only be cancelling."""
    from decimal import Decimal as D
    from newcore.domain import IntentState, InvalidRecord, OwnerKind, Purpose
    p, ids = F.single_lot_portfolio(98, stop_state='confirmed')
    lt = p.lots[0]
    symbol = lt.symbol if held else 'LINKUSDT'
    orphan = F.intent(ids, p.account_id, Purpose(purpose), symbol, lt.side, D('1000'), owner_id=F.pf_id(p.account_id),
                      owner_kind=OwnerKind.PORTFOLIO, state=IntentState.CANCELLING)
    pf = F.replace(p, intents=p.intents + (orphan,))
    assert orphan in pf.intents and orphan.orphan
    for state in (IntentState.DURABLE, IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN):
        with pytest.raises(InvalidRecord, match='cancel-only'):          # never sendable: only ever cancelling
            F.replace(orphan, state=state)


# ----------------------------------------------------------------------------------------------------------- H05
def test_every_required_type_round_trips_standalone():
    """H05: Lot, Position, Protection, AccountBinding and InstrumentId raised "not a document record type". Fill was
    listed by Cowork too, but it is not a contract-2 required type, so (Codex ruling) it stays nested-only."""
    from newcore.domain import canonical_bytes, contract_sha256
    p, _ = F.single_lot_portfolio(93, stop_state='confirmed')
    lt = p.lots[0]
    for rec in (lt, p.positions[0], lt.stop, F.binding(), F.rules().instrument):
        b = canonical_bytes(rec)
        back = loads(b)
        assert back == rec and type(back) is type(rec) and canonical_bytes(back) == b
        assert len(contract_sha256(rec)) == 64
    with pytest.raises(DomainError, match='not a document record type'):     # not a universal root serializer
        canonical_bytes(lt.fills[0])


def test_the_standalone_set_is_pinned():
    """Codex ruling on H05: the contract's required types plus the document types of cd721c5 - nothing more."""
    from newcore.domain.codec import RECORD_TYPES
    assert set(RECORD_TYPES) == {
        'account', 'instrument_rules', 'order_intent', 'order_result', 'portfolio', 'decision', 'decision_key',
        'snapshot', 'high_water', 'event_intent_recorded', 'event_intent_state_changed', 'event_result_observed',
        'event_decision_recorded', 'event_mode_changed', 'event_binding_changed', 'event_incident_recorded',
        'event_management_input_recorded',                                   # r3 draft item 7
        'account_binding', 'instrument_id', 'protection', 'lot', 'position'}


# ----------------------------------------------------------------------------------------------------------- CP05 / I03
def test_a_bare_position_rejects_duplicate_lots():
    """CP05/I03: Position(lots=(l, l)) built on its own and Position.qty double counted."""
    from newcore.domain import InvalidRecord, Position
    p, ids = F.single_lot_portfolio(94)
    lt = p.lots[0]
    with pytest.raises(InvalidRecord, match='duplicate lot id'):
        Position(position_id=ids.id('pos'), symbol=lt.symbol, side=lt.side, lots=(lt, lt))
    twin = F.replace(lt, stop=F.replace(lt.stop, order=None, confirmed_at_ms=None))      # same id, other content
    with pytest.raises(InvalidRecord, match='duplicate lot id'):
        Position(position_id=ids.id('pos'), symbol=lt.symbol, side=lt.side, lots=(lt, twin))


# ----------------------------------------------------------------------------------------------------------- Codex rulings
@pytest.mark.parametrize('bad', ['', ' ', 'solusdt', 'SOL USDT', 'SOL/USDT', 'S', 'X' * 31, 'SOLUSDT\n'], ids=repr)
def test_ruling_1_decision_symbol_uses_the_canonical_validator(bad):
    from newcore.domain import Action, InvalidRecord, ReasonCode
    ids = F.Ids(95)
    d = F.decision(ids, ids.id('acct'), Action.WAIT, ReasonCode.FILTER_HOURS)
    assert F.replace(d, symbol='SOLUSDT').symbol == 'SOLUSDT' and d.symbol is None   # present: valid; absent: None
    with pytest.raises(InvalidRecord, match='symbol'):
        F.replace(d, symbol=bad)


def test_ruling_3_binding_confirmation_stays_on_the_account():
    """No boolean on AccountBinding: confirmation is Account.binding_state + BindingConfirmation (Codex ruling 3)."""
    from newcore.domain import Account, AccountBinding
    from newcore.domain.base import field_spec
    assert [n for n, _, _ in field_spec(AccountBinding)] == ['venue', 'environment', 'settlement_asset', 'key_digest',
                                                             'exchange_uid']
    assert {'binding_state', 'confirmation'} <= {n for n, _, _ in field_spec(Account)}


@pytest.mark.parametrize('blank', [' ', '   ', '　', ' '], ids=repr)
def test_ruling_2_slot_id_and_exchange_order_id_are_never_blank(blank):
    from newcore.domain import InvalidRecord
    ids = F.Ids(96)
    acct = ids.id('acct')
    entry = F.market_entry(ids, acct)
    assert F.replace(entry, slot_id=None).slot_id is None                       # absent stays None
    with pytest.raises(InvalidRecord, match='slot_id'):
        F.replace(entry, slot_id=blank)
    p, _ = F.single_lot_portfolio(96)
    with pytest.raises(InvalidRecord, match='slot_id'):
        F.replace(p.lots[0], slot_id=blank)
    known = F.results(ids, acct, entry)['known']
    with pytest.raises(InvalidRecord, match='exchange_order_id'):
        F.replace(known, exchange_order_id=blank)


def test_ruling_4_position_lots_have_one_canonical_order():
    """Lots are kept in stable lot-id order whatever order they were given in, so snapshot bytes and replay are
    deterministic; duplicate ids are refused at construction (also CP05)."""
    from decimal import Decimal as D
    from newcore.domain import Position, canonical_bytes, loads
    ids = F.Ids(97)
    acct = ids.id('acct')
    lots = [F.lot(ids, acct, 'SOLUSDT', qty=D(q), stop_state='none')[0] for q in ('1', '2', '3')]
    pid = ids.id('pos')
    orders = [lots, lots[::-1], [lots[1], lots[2], lots[0]]]
    built = [Position(position_id=pid, symbol='SOLUSDT', side=lots[0].side, lots=tuple(o)) for o in orders]
    assert all(b == built[0] for b in built)
    assert [x.lot_id for x in built[0].lots] == sorted(x.lot_id for x in lots)
    assert len({canonical_bytes(b) for b in built}) == 1                  # one encoding
    assert loads(canonical_bytes(built[1])).lots == built[0].lots       # replay from bytes: same order


def test_p2_portfolio_collections_are_canonical():
    """Codex P2 on af4e5f3: positions and intents keep one canonical order (symbol / side, intent id) whatever order
    they are given in, at construction and on decode, so bytes and hashes are permutation-free. UNKNOWN stays None."""
    import random
    from newcore.domain import canonical_bytes, contract_sha256, decode_document, encode_document
    p = F.typical_portfolio()
    variants = []
    for seed in range(5):
        rng = random.Random(seed)
        pos, its = list(p.positions), list(p.intents)
        rng.shuffle(pos)
        rng.shuffle(its)
        variants.append(F.replace(p, positions=tuple(pos), intents=tuple(its)))
    assert all(v == p for v in variants)
    assert {canonical_bytes(v) for v in variants} == {canonical_bytes(p)}
    assert {contract_sha256(v) for v in variants} == {contract_sha256(p)}
    doc = encode_document(p)
    doc['body']['positions'].reverse()
    doc['body']['intents'].reverse()
    assert decode_document(doc) == p                                        # on decode too
    assert [x.intent_id for x in p.intents] == sorted(x.intent_id for x in p.intents)
    assert [(x.symbol, x.side.value) for x in p.positions] == sorted((x.symbol, x.side.value) for x in p.positions)
    u = F.unknown_portfolio(F.Ids(7).id('acct'))
    assert u.positions is None and u.intents is None
