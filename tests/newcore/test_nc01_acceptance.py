"""NC-01 implementation acceptance for the frozen contract (d7cb505): the residual items named at the freeze.

1. constructor / codec parity      4. protection-purpose cross-checks
2. lifecycle and HOLD tables       5. client-id collision and reuse
3. the fresh-snapshot rule         6. binding-confirmation cases
"""
import copy
import dataclasses
import enum
import typing
from decimal import Decimal as D
from itertools import product

import pytest

import nc01_factories as F
from nc01_factories import T0, replace
from newcore.domain import (Account, BindingChanged, BindingConfirmation, BindingState, EntriesMode, Environment,
                            HoldKind, IntentRecorded, IntentState, InvalidRecord, Op, Ownership, ProofKind, Purpose,
                            ReasonCode, ResultObserved, IntentStateChanged, check_account_portfolio, check_event_chain,
                            check_flat_snapshot_fresh, confirmation_phrase, decode_document, encode_document,
                            observe_binding, permitted, Permission)
from newcore.domain.base import field_spec

# ----------------------------------------------------------------------------------------------------------- 1. parity
JSON_VALUES = [1.5, True, False, 7, -1, 0, None, [], {}, 'x', '', '1', '2026-10-08T00:00:00Z', 'acct_' + '0' * 32]


def _memory_form(tp, v):
    """The in-memory value a caller would pass for wire value v (enum / Decimal are strings, tuples are lists)."""
    if type(v) is list:
        return tuple(v)
    base = next((a for a in typing.get_args(tp) if a is not type(None)), tp)
    if type(v) is str and isinstance(base, type) and issubclass(base, enum.Enum):
        try:
            return base(v)
        except ValueError:
            return v
    if type(v) is str and base is D:
        try:
            return D(v)
        except Exception:
            return v
    return v


def _outcome(fn):
    try:
        fn()
        return 'accepted'
    except InvalidRecord:
        return 'rejected'


@pytest.mark.parametrize('rec', F.samples(), ids=lambda r: type(r).__name__)
def test_constructor_and_codec_share_one_dialect(rec):
    """For every top-level field and every JSON value, building the record in memory and decoding it from the wire
    reach the same verdict: there is no permissive in-memory dialect and no permissive wire dialect."""
    doc = encode_document(rec)
    for (name, tp, _), v in product(field_spec(type(rec)), JSON_VALUES):
        mem = _memory_form(tp, v)
        ctor = _outcome(lambda: dataclasses.replace(rec, **{name: mem}))
        wire_doc = copy.deepcopy(doc)
        wire_doc['body'][name] = copy.deepcopy(v)
        wire = _outcome(lambda: decode_document(wire_doc))
        assert ctor == wire, f'{type(rec).__name__}.{name} = {v!r}: constructor {ctor}, codec {wire}'


def test_decimal_spelling_is_the_only_documented_asymmetry():
    """Constructors normalize numerically equal Decimals; the wire accepts only the canonical spelling (contract 5)."""
    r = F.rules()
    assert dataclasses.replace(r, min_notional=D('5.0')) == r
    doc = encode_document(r)
    doc['body']['min_notional'] = '5.0'
    with pytest.raises(InvalidRecord, match='canonical'):
        decode_document(doc)


# ----------------------------------------------------------------------------------------------------------- 2. tables
P, O = Purpose, Op
OPEN = {P.ENTRY, P.ADD}
ALL_P = set(P)
_q = {(u, O.QUERY) for u in ALL_P}
_adopt = {(u, O.ADOPT) for u in OPEN}
_drain = {(u, O.CANCEL) for u in OPEN}
_risk_reduce = {(u, o) for u in (P.PROTECT, P.CLOSE, P.REDUCE) for o in (O.PLACE, O.CANCEL, O.REPRICE, O.MANAGE)}
_resume = {(u, O.RESUME) for u in ALL_P}
# Pinned independently of the implementation: allowed (purpose, op) per mode, without a one-shot authorization.
TABLE = {
    (EntriesMode.ACTIVE, None): {(u, o) for u, o in product(ALL_P, O) if not (o in (O.ADOPT, O.FALLBACK) and u not in OPEN)},
    (EntriesMode.PAUSED, None): _q | _adopt | _drain | _risk_reduce | _resume,
    (EntriesMode.HALTED, None): _q | _adopt | _drain | _risk_reduce | _resume,
    (EntriesMode.FLATTENING, None): _q | _adopt | _drain | _risk_reduce | _resume,
    (EntriesMode.HOLD, HoldKind.NORMAL): _q | _adopt | _drain | {(P.PROTECT, O.PLACE)} |
    {(u, o) for u in (P.CLOSE, P.REDUCE) for o in (O.PLACE, O.CANCEL)},
    (EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE): _q | _adopt | _drain | {(P.PROTECT, O.PLACE)},
}


@pytest.mark.parametrize('mode', list(TABLE), ids=lambda m: f'{m[0]}-{m[1]}')
def test_permitted_action_table_is_pinned(mode):
    allowed = {(u, o) for u, o in product(P, O) if permitted(*mode, u, o) is Permission.ALLOWED}
    assert allowed == TABLE[mode]
    shot = {(u, o) for u, o in product(P, O) if permitted(*mode, u, o, one_shot=True) is Permission.ALLOWED}
    extra = {(u, O.PLACE) for u in OPEN} if mode[0] is EntriesMode.PAUSED else set()
    assert shot == TABLE[mode] | extra                              # a one-shot opens only from PAUSED, never HOLD


def test_lifecycle_table_has_no_path_back_from_terminal():
    from newcore.domain.orders import INTENT_TRANSITIONS, TERMINAL
    reach = {s: set(INTENT_TRANSITIONS[s]) for s in IntentState}
    for _ in IntentState:                                           # transitive closure
        for s in reach:
            reach[s] |= {y for x in reach[s] for y in INTENT_TRANSITIONS[x]}
    for t in TERMINAL:
        assert reach[t] == set()
    for s in set(IntentState) - TERMINAL:
        assert reach[s] & TERMINAL                                  # every live state can end
    assert IntentState.PLANNED not in set().union(*reach.values())


# ----------------------------------------------------------------------------------------------------------- 3. fresh snapshot
def _empty(acct, at=T0 + 5000):
    prf = F.build(F.OwnershipProof, kind=ProofKind.FLAT_SNAPSHOT, at_ms=at, reconciliation_id=F.Ids(9).id('rec'),
                  key_digest=F.DIGEST)
    return F.portfolio(acct, prf=prf)


def test_fresh_snapshot_rule():
    acct = F.Ids(31).id('acct')
    a, pf = F.account(acct), _empty(acct)
    assert pf.ownership is Ownership.KNOWN_EMPTY
    check_flat_snapshot_fresh(a, pf, now_ms=T0 + 9000, max_age_ms=5000)
    check_flat_snapshot_fresh(a, pf, now_ms=T0 + 10_000, max_age_ms=5000)          # exactly at the limit
    with pytest.raises(InvalidRecord, match='ms old'):
        check_flat_snapshot_fresh(a, pf, now_ms=T0 + 10_001, max_age_ms=5000)
    with pytest.raises(InvalidRecord, match='future'):
        check_flat_snapshot_fresh(a, pf, now_ms=T0, max_age_ms=5000)
    with pytest.raises(InvalidRecord, match='integer'):
        check_flat_snapshot_fresh(a, pf, now_ms=float(T0 + 9000), max_age_ms=5000)
    with pytest.raises(InvalidRecord, match='predates the binding confirmation'):
        check_account_portfolio(a, _empty(acct, at=T0 - 1))
    with pytest.raises(InvalidRecord, match='confirmed binding'):
        check_flat_snapshot_fresh(F.account(acct, digest='fedcba9876543210'), pf, now_ms=T0 + 9000, max_age_ms=5000)
    held = replace(pf, entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL,
                   pause_reasons=(ReasonCode.BINDING_UNCONFIRMED,))
    with pytest.raises(InvalidRecord):
        check_flat_snapshot_fresh(F.account(acct, state=BindingState.UNCONFIRMED), held, now_ms=T0 + 9000,
                                  max_age_ms=5000)
    owning, _ = F.single_lot_portfolio(32)
    with pytest.raises(InvalidRecord, match='only a KNOWN_EMPTY'):
        check_flat_snapshot_fresh(F.account(owning.account_id), owning, now_ms=T0 + 9000, max_age_ms=5000)


# ----------------------------------------------------------------------------------------------------------- 4. purposes
PF, IDS = F.single_lot_portfolio(41, stop_state='confirmed', in_flight='add')
LOT = PF.lots[0]
POS = PF.positions[0]
ADD = next(i for i in PF.intents if i.purpose is Purpose.ADD)
STOP = next(i for i in PF.intents if i.purpose is Purpose.PROTECT)


def _with_lot(lt, intents=None):
    return replace(PF, positions=(replace(POS, lots=(lt,)),), intents=PF.intents if intents is None else intents)


def test_a_stop_is_carried_only_by_a_protect_intent():
    with pytest.raises(InvalidRecord, match='not a PROTECT intent'):         # the add carries the stop
        _with_lot(replace(LOT, in_flight=None, stop=replace(LOT.stop, order=ADD.intent_id)))
    close = F.intent(IDS, PF.account_id, Purpose.CLOSE, LOT.symbol, LOT.side, LOT.qty, owner_id=LOT.lot_id)
    with pytest.raises(InvalidRecord, match='not a PROTECT intent'):         # a close as the replacement
        _with_lot(replace(LOT, stop=replace(LOT.stop, replacement=close.intent_id)), PF.intents + (close,))


def test_a_lot_in_flight_order_is_never_a_protect_or_entry_intent():
    for iid in (STOP.intent_id,):
        with pytest.raises(InvalidRecord, match='names no open add / reduce / close'):
            _with_lot(replace(LOT, in_flight=iid))
    me = F.market_entry(IDS, PF.account_id)
    with pytest.raises(InvalidRecord, match='names no open add / reduce / close'):
        _with_lot(replace(LOT, in_flight=me.intent_id), PF.intents + (me,))


def test_provisional_stops_belong_only_to_unresolved_market_entries():
    maker = F.maker_entry(IDS, PF.account_id)
    prov = F.intent(IDS, PF.account_id, Purpose.PROTECT, maker.symbol, maker.side, D('1'), owner_id=maker.intent_id,
                    stop_price=D('1.9'), state=IntentState.CANCELLING)
    with pytest.raises(InvalidRecord, match='only an unresolved market ENTRY'):
        replace(PF, intents=PF.intents + (maker, prov))
    me = F.market_entry(IDS, PF.account_id)
    stop = F.intent(IDS, PF.account_id, Purpose.PROTECT, me.symbol, me.side, D('400'), owner_id=me.intent_id,
                    stop_price=D('0.1'))
    with pytest.raises(InvalidRecord, match='cancel-only'):               # a working provisional stop nobody carries
        replace(PF, intents=PF.intents + (me, stop))
    p = F.build(F.Protection, owner_id=me.intent_id, price=D('0.1'), qty=D('400'), order=stop.intent_id)
    replace(PF, intents=PF.intents + (me, stop), entry_stops=(p,))         # carried by its entry stop: valid
    with pytest.raises(InvalidRecord, match='side'):                        # a stop on the opening side
        short = replace(stop, side=F.Side.SHORT)
        replace(PF, intents=PF.intents + (me, short), entry_stops=(replace(p, order=short.intent_id),))


# ----------------------------------------------------------------------------------------------------------- 5. client ids
def test_client_id_collisions_inside_a_portfolio():
    acct = PF.account_id
    other = F.maker_entry(IDS, acct)
    twin = replace(F.maker_entry(IDS, acct), client_order_id=other.client_order_id)
    clashes = {
        'new primary vs live primary': (replace(other, client_order_id=ADD.client_order_id),),
        'orphan cancel vs live stop': (replace(F.orphan_stop(IDS, acct), client_order_id=STOP.client_order_id),),
        'two new intents, one id': (other, twin),
    }
    for name, extra in clashes.items():
        with pytest.raises(InvalidRecord, match='duplicate client order id'):
            replace(PF, intents=PF.intents + extra)
            raise AssertionError(name)
    alt_stop = replace(STOP, alt_client_order_id=ADD.client_order_id)                          # algo id vs primary
    with pytest.raises(InvalidRecord, match='duplicate client order id'):
        replace(PF, intents=tuple(alt_stop if i is STOP else i for i in PF.intents))
    with pytest.raises(InvalidRecord, match='equals the primary'):
        replace(STOP, alt_client_order_id=STOP.client_order_id)


def test_client_id_is_never_reused_across_the_journal():
    acct = PF.account_id
    first = F.market_entry(IDS, acct)
    res = F.results(IDS, acct, first)
    ev = [F.event(IntentRecorded, IDS, acct, 1, intent=replace(first, state=IntentState.DURABLE), reason=first.reason),
          F.event(IntentStateChanged, IDS, acct, 2, intent_id=first.intent_id, from_state=IntentState.DURABLE,
                  to_state=IntentState.SUBMITTED),
          F.event(ResultObserved, IDS, acct, 3, at=T0 + 40_000, result=res['refused']),
          F.event(IntentStateChanged, IDS, acct, 4, at=T0 + 40_001, intent_id=first.intent_id,
                  from_state=IntentState.SUBMITTED, to_state=IntentState.REJECTED)]
    assert check_event_chain(ev) == {}
    again = replace(F.market_entry(IDS, acct), client_order_id=first.client_order_id, state=IntentState.DURABLE)
    with pytest.raises(InvalidRecord, match='never reused'):              # even after the first intent ended
        check_event_chain(ev + [F.event(IntentRecorded, IDS, acct, 5, intent=again, reason=again.reason)])
    with pytest.raises(InvalidRecord, match='belongs to another intent'):  # a result naming another intent's id
        check_event_chain(ev[:2] + [F.event(ResultObserved, IDS, acct, 3, result=replace(
            res['refused'], client_order_id=ADD.client_order_id))])


def test_an_owned_client_id_is_never_foreign():
    m = F.miss(F.MissPhase.OWNER_CHECK, 1, STOP.client_order_id)
    with pytest.raises(InvalidRecord, match='never foreign'):
        _with_lot(replace(LOT, stop=replace(LOT.stop, confirmed_at_ms=None, miss=m)))


# ----------------------------------------------------------------------------------------------------------- 6. binding
ACCT = F.Ids(61).id('acct')
NEW = 'fedcba9876543210'


def _conf(old, new, acct=ACCT, phrase=None):
    return BindingConfirmation(account_id=acct, old_key_digest=old, new_key_digest=new,
                               typed_phrase=confirmation_phrase(acct, new) if phrase is None else phrase,
                               confirmed_at_ms=T0)


def test_typed_confirmation_phrase_is_exact():
    _conf(None, F.DIGEST)
    for bad in ('', 'yes', confirmation_phrase(ACCT, NEW), confirmation_phrase(F.Ids(62).id('acct'), F.DIGEST),
                confirmation_phrase(ACCT, F.DIGEST).lower(), confirmation_phrase(ACCT, F.DIGEST) + ' '):
        with pytest.raises(InvalidRecord, match='typed_phrase'):
            _conf(None, F.DIGEST, phrase=bad)


def test_rotation_states_need_the_right_confirmation():
    first = F.account(ACCT)
    pending = replace(first, binding_state=BindingState.ROTATION_PENDING, proposed_binding=F.binding(NEW))
    assert observe_binding(pending, F.binding(NEW)) is BindingState.ROTATION_PENDING
    assert observe_binding(pending, F.binding('1111111111111111')) is BindingState.MISMATCH
    rotated = replace(first, binding=F.binding(NEW), binding_state=BindingState.RECONCILING,
                      confirmation=_conf(F.DIGEST, NEW))
    assert not rotated.entries_allowed
    with pytest.raises(InvalidRecord, match='RECONCILING follows a rotation'):
        replace(rotated, confirmation=_conf(None, NEW))
    with pytest.raises(InvalidRecord, match='confirms another account or key'):
        replace(rotated, confirmation=_conf(F.DIGEST, NEW, acct=F.Ids(63).id('acct')))
    with pytest.raises(InvalidRecord, match='confirms another account or key'):
        replace(rotated, confirmation=_conf(NEW, F.DIGEST))
    with pytest.raises(InvalidRecord, match='never changes venue, environment'):
        replace(pending, proposed_binding=F.binding(NEW, Environment.MAINNET))


def test_binding_change_events_check_their_confirmation():
    ids = F.Ids(64)
    ev = lambda **kw: F.event(BindingChanged, ids, ACCT, 1, reason=ReasonCode.BINDING_RECONCILING, **kw)    # noqa
    ev(from_state=BindingState.ROTATION_PENDING, to_state=BindingState.RECONCILING, binding=F.binding(NEW),
       confirmation=_conf(F.DIGEST, NEW))
    with pytest.raises(InvalidRecord, match='old key'):                      # first-bind confirmation used for a rotation
        ev(from_state=BindingState.ROTATION_PENDING, to_state=BindingState.RECONCILING, binding=F.binding(NEW),
           confirmation=_conf(None, NEW))
    with pytest.raises(InvalidRecord, match='another account or key'):      # confirms a key the event does not bind
        ev(from_state=BindingState.ROTATION_PENDING, to_state=BindingState.RECONCILING, binding=F.binding(NEW),
           confirmation=_conf(F.DIGEST, '1111111111111111'))
    with pytest.raises(InvalidRecord, match='old key'):                      # a rotation confirmation for a first bind
        ev(from_state=BindingState.UNCONFIRMED, to_state=BindingState.CONFIRMED, binding=F.binding(NEW),
           confirmation=_conf(F.DIGEST, NEW))
    with pytest.raises(InvalidRecord, match='typed confirmation exactly where'):
        ev(from_state=BindingState.CONFIRMED, to_state=BindingState.ROTATION_PENDING, binding=F.binding(),
           confirmation=_conf(None, F.DIGEST))
    ev(from_state=BindingState.RECONCILING, to_state=BindingState.CONFIRMED, binding=F.binding(NEW),
       reconciliation_id=ids.id('rec'))
    with pytest.raises(InvalidRecord, match='reconciliation record'):
        ev(from_state=BindingState.ROTATION_PENDING, to_state=BindingState.CONFIRMED, binding=F.binding(),
           reconciliation_id=ids.id('rec'))


def test_unconfirmed_or_rotating_binding_never_runs_active():
    pf = F.portfolio(ACCT)
    for state in (BindingState.UNCONFIRMED, BindingState.MISMATCH, BindingState.ROTATION_PENDING):
        a = F.account(ACCT, state=state) if state is BindingState.UNCONFIRMED else replace(
            F.account(ACCT), binding_state=state, proposed_binding=F.binding(NEW))
        assert isinstance(a, Account) and not a.entries_allowed
        with pytest.raises(InvalidRecord):
            check_account_portfolio(a, pf)
    assert permitted(EntriesMode.HOLD, HoldKind.NORMAL, Purpose.ENTRY, Op.RESUME) is Permission.FORBIDDEN
