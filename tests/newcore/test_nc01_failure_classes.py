"""Audit2 / NC-02 failure classes (contract 6.4): each one is unrepresentable in the NC-01 types, or detectable as
UNKNOWN / HOLD / a typed failure, instead of being silently resolved. NC-02+ supply the behaviour; these prove the
types give them nothing wrong to start from."""
from decimal import Decimal as D

import pytest

import nc01_factories as F
from nc01_factories import T0, replace
from newcore.domain import (Account, BindingState, EntriesMode, Evidence, GenerationVerdict, HighWater, HoldKind,
                            IntentRecorded, IntentState, IntentStateChanged, InvalidRecord, Lookup, Op, Outcome,
                            OwnershipUnknown, ProofKind, Purpose, ReasonCode, ResultObserved, ResultPhase, Snapshot,
                            check_account_portfolio, check_event_chain, check_generation, decode_result, dumps,
                            encode_document, observe_binding, owned_client_ids)


# ---------------------------------------------------------------------------------- future schema (both copies) / damage
def test_future_schema_in_both_copies_is_unsupported_not_damage():
    p, _ = F.single_lot_portfolio()
    primary = dumps(p).replace('"schema_version":1', '"schema_version":2')
    backup = primary.replace('"generation":7', '"generation":6')
    for text in (primary, backup):
        res = decode_result(text)
        assert res.outcome is Outcome.UNSUPPORTED_VERSION and res.record is None
    # a damaged primary next to a future backup: two DIFFERENT typed outcomes, so NC-02 can let "future" win
    damaged = dumps(p).replace('"qty":"1.5"', '"qty":"NaN"', 1)
    assert decode_result(damaged).outcome is Outcome.INVALID
    assert decode_result(backup).outcome is Outcome.UNSUPPORTED_VERSION


@pytest.mark.parametrize('damage', ['"qty":1.5', '"qty":"1.5","qty":"2"', '"qty":"-1"', '"qty":"1e300"'])
def test_current_schema_damage_never_yields_a_portfolio(damage):
    p, _ = F.single_lot_portfolio()
    text = dumps(p).replace('"qty":"1.5"', damage, 1)
    res = decode_result(text)
    assert res.outcome is Outcome.INVALID and res.record is None


# ---------------------------------------------------------------------------------- empty managed account vs unknown
def test_unknown_ownership_is_never_an_empty_account():
    u = F.unknown_portfolio(F.Ids(5).id('acct'))
    assert u.entries_mode is EntriesMode.HOLD and u.positions is None
    with pytest.raises(OwnershipUnknown):
        u.lots
    with pytest.raises(OwnershipUnknown):
        owned_client_ids(u)
    with pytest.raises(InvalidRecord):                                  # "unknown, but here is an empty list"
        replace(u, positions=(), intents=(), entry_stops=())


def test_known_empty_needs_a_flat_snapshot_under_the_confirmed_binding():
    ids = F.Ids(6)
    acct = ids.id('acct')
    empty = F.portfolio(acct)                                           # KNOWN_EMPTY with a FLAT_SNAPSHOT proof
    check_account_portfolio(F.account(acct), empty)
    with pytest.raises(InvalidRecord):                                  # binding not confirmed
        check_account_portfolio(F.account(acct, state=BindingState.UNCONFIRMED), replace(
            empty, entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL,
            pause_reasons=(ReasonCode.BINDING_UNCONFIRMED,)))
    with pytest.raises(InvalidRecord):                                  # snapshot taken under another key
        check_account_portfolio(F.account(acct), replace(empty, proof=F.proof(ProofKind.FLAT_SNAPSHOT,
                                                                             digest='fedcba9876543210')))
    for kind in (ProofKind.RECONCILED, ProofKind.JOURNAL, ProofKind.OWNER_ADOPTED):
        with pytest.raises(InvalidRecord):                              # a trivially-empty candidate never matches in
            replace(empty, proof=F.proof(kind))


# ---------------------------------------------------------------------------------- qty-only pending
def test_qty_only_pending_is_unrepresentable():
    p, ids = F.single_lot_portfolio(9, in_flight='add')
    lt = p.lots[0]
    add = next(i for i in p.intents if i.intent_id == lt.in_flight)
    with pytest.raises(InvalidRecord):                                  # an order with no client id
        replace(add, client_order_id='')
    with pytest.raises(InvalidRecord):                                  # a pending marker with no durable intent
        replace(p, intents=tuple(i for i in p.intents if i is not add))
    with pytest.raises(InvalidRecord):                                  # memory ahead of the ledger (qty-only resolve)
        replace(lt, qty=lt.qty + add.qty)


# ---------------------------------------------------------------------------------- bare not-found
def test_bare_not_found_resolves_nothing():
    p, ids = F.single_lot_portfolio(10, in_flight='add')
    lt = p.lots[0]
    add = next(i for i in p.intents if i.intent_id == lt.in_flight)
    nf = F.results(ids, p.account_id, add)['not_found']
    assert nf.phase is ResultPhase.UNKNOWN and nf.lookup is Lookup.NOT_FOUND and nf.booked_qty is None
    with pytest.raises(InvalidRecord):                                  # not-found cannot carry an executed value
        replace(nf, executed_qty=D('0'))
    with pytest.raises(InvalidRecord):                                  # nor become final without evidence
        replace(nf, phase=ResultPhase.FINAL, lookup=None, executed_qty=D('0'))
    with pytest.raises(InvalidRecord):                                  # nor a corroborated "nothing" with no decision
        replace(nf, phase=ResultPhase.FINAL, lookup=None, executed_qty=D('0'), evidence=Evidence.NOT_FOUND_CORROBORATED)
    # the add stays owned (live) after a not-found, so a SECOND add for the lot is not representable (1.5 -> 2.0 race)
    second = F.intent(ids, p.account_id, Purpose.ADD, lt.symbol, lt.side, D('0.5'), owner_id=lt.lot_id)
    with pytest.raises(InvalidRecord):
        replace(p, intents=p.intents + (second,))
    # in the log the not-found is recorded and the intent is still live afterwards
    acct = p.account_id
    at = add.created_at_ms
    log = [F.event(IntentRecorded, ids, acct, 1, at=at, intent=replace(add, state=IntentState.DURABLE), reason=add.reason),
           F.event(IntentStateChanged, ids, acct, 2, at=at, intent_id=add.intent_id, from_state=IntentState.DURABLE,
                   to_state=IntentState.SUBMITTED),
           F.event(ResultObserved, ids, acct, 3, at=T0 + 30_000, result=nf,
                   reason=ReasonCode.EVIDENCE_NOT_FOUND_UNCORROBORATED)]
    assert add.intent_id in check_event_chain(log)


# ---------------------------------------------------------------------------------- same positions, different account
def test_identical_positions_never_prove_identity():
    a, _ = F.single_lot_portfolio(11)
    b_acct = F.Ids(12).id('acct')
    account_b = F.account(b_acct, digest='fedcba9876543210')
    with pytest.raises(InvalidRecord, match='another account'):
        check_account_portfolio(account_b, a)
    # the venue reports another key for this account: MISMATCH (entries blocked) whatever the positions say
    acct_a = F.account(a.account_id)
    assert observe_binding(acct_a, account_b.binding) is BindingState.MISMATCH
    mismatched = Account(account_id=acct_a.account_id, label=acct_a.label, hedge_mode=True, binding=acct_a.binding,
                         binding_state=BindingState.MISMATCH, proposed_binding=account_b.binding,
                         confirmation=acct_a.confirmation)
    assert not mismatched.entries_allowed
    with pytest.raises(InvalidRecord):
        check_account_portfolio(mismatched, a)                          # a is ACTIVE: not allowed under MISMATCH
    check_account_portfolio(mismatched, replace(a, entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL,
                                                pause_reasons=(ReasonCode.BINDING_MISMATCH,)))


# ---------------------------------------------------------------------------------- generation rollback
def test_generation_rollback_is_detectable():
    p, _ = F.single_lot_portfolio(13)
    acct = p.account_id
    snap = Snapshot(account_id=acct, generation=7, last_sequence=40, written_at_ms=T0, writer_build='b1', portfolio=p)
    mark = lambda g, s: HighWater(account_id=acct, generation=g, last_sequence=s, writer_build='b1')
    assert check_generation(snap, mark(7, 40)) is GenerationVerdict.CURRENT
    assert check_generation(snap, mark(9, 55)) is GenerationVerdict.ROLLED_BACK
    assert check_generation(snap, mark(7, 41)) is GenerationVerdict.ROLLED_BACK       # same generation, fewer events
    assert check_generation(snap, mark(6, 30)) is GenerationVerdict.AHEAD
    other = HighWater(account_id=F.Ids(1).id('acct'), generation=7, last_sequence=40, writer_build='b1')
    assert check_generation(snap, other) is GenerationVerdict.FOREIGN
    # an event log that continues a NEWER snapshot cannot follow this one
    newer = replace(F.market_entry(F.Ids(2), acct), state=IntentState.DURABLE)
    ev = F.event(IntentRecorded, F.Ids(2), acct, 56, intent=newer, reason=newer.reason)
    with pytest.raises(InvalidRecord, match='expected sequence 41'):
        check_event_chain([ev], after_sequence=snap.last_sequence)


# ---------------------------------------------------------------------------------- resting maker / pause / flatten
def test_flatten_and_pause_must_drain_resting_and_armed_entries():
    p, ids = F.single_lot_portfolio(14)
    maker = F.maker_entry(ids, p.account_id)
    trailing = F.trailing_entry(ids, p.account_id)
    flat = dict(entries_mode=EntriesMode.FLATTENING, pause_reasons=(ReasonCode.OPERATOR_FLATTEN,))
    for it in (maker, trailing):
        with pytest.raises(InvalidRecord, match='must be cancelling'):
            replace(p, intents=p.intents + (it,), **flat)
        replace(p, intents=p.intents + (replace(it, state=IntentState.CANCELLING),), **flat)


def test_late_fill_is_adopted_explicitly_never_silently():
    ids = F.Ids(15)
    acct = ids.id('acct')
    maker = F.maker_entry(ids, acct)
    adopted = F.results(ids, acct, maker)['adopted']
    assert adopted.evidence is Evidence.POSITION_ADOPTED and adopted.resolved_by and len(adopted.corroboration) >= 2
    with pytest.raises(InvalidRecord):
        replace(adopted, resolved_by=None)
    for mode, hold in ((EntriesMode.FLATTENING, None), (EntriesMode.HOLD, HoldKind.NORMAL),
                       (EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE)):
        pf = F.portfolio(acct, mode=mode, hold_kind=hold)
        assert pf.permits(Purpose.ENTRY, Op.ADOPT)                      # adopt + protect, never ignore
        assert not pf.permits(Purpose.ENTRY, Op.PLACE)


def test_manual_entry_never_bypasses_pause_or_hold():
    p, ids = F.single_lot_portfolio(16)
    manual = F.market_entry(ids, p.account_id, created=T0 + 20_000, reason=ReasonCode.ENTRY_MANUAL)
    for kw in (dict(entries_mode=EntriesMode.PAUSED, pause_reasons=(ReasonCode.OPERATOR_PAUSE,)),
               dict(entries_mode=EntriesMode.HALTED, pause_reasons=(ReasonCode.FILTER_HALT,)),
               dict(entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL,
                    pause_reasons=(ReasonCode.RECONCILE_UNRECONCILED,))):
        with pytest.raises(InvalidRecord, match='not permitted'):
            replace(p, intents=p.intents + (manual,), **kw)


# ---------------------------------------------------------------------------------- no legacy import
def test_legacy_state_files_are_foreign():
    legacy = '{"schema_version": 1, "lots": {}, "halted": false, "orphans": []}'
    assert decode_result(legacy).outcome is Outcome.FOREIGN
    assert encode_document(F.portfolio(F.Ids(1).id('acct')))['format'] == 'zackbot.newcore'
