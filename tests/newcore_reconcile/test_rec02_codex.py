"""Codex review of nc-rec02 e3c0e2f (CHANGES REQUIRED, three P1s in the pure fold): one repro group per P1, written
to FAIL on e3c0e2f and pass once fixed.

  X1 one venue order / trade explains at most ONE owned intent: a second claimant (same exchange order id, same trade
     id, a duplicate client id) is a typed shared-evidence HOLD for every claimant, never a doubled delta
  X2 the reconciliation id covers every verdict input with a TAGGED canonical form: every policy field, the trigger,
     the attempt and each role-bearing view / snapshot field; the same id implies the same verdict
  X3 duplicate keyed evidence (by-id answers, fills, trade windows) never depends on input order: identical
     duplicates collapse once, conflicting duplicates are a typed HOLD, never last-wins
"""
import dataclasses
import itertools
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, HoldKind, IntentState
from newcore.reconcile import DecisionKind as K
from newcore.reconcile import Outcome, RecPolicy, TradeWindow, Trigger, reconcile
from rec_helpers import (SYM, T, cid, fact, fill, final, kinds, lot, not_found, ok, order, pos, snap, view)

LOT1 = 'lot_' + f'{1:032x}'
HINT = [((SYM, 'LONG'), D('90'))]


def resolved(v):
    return [d for d in v.decisions if d.kind in (K.RESOLVE_FILLED, K.RESOLVE_NOT_EXECUTED, K.ADOPT)]


def permuted(vw, s):
    """Every order of the intents and of the keyed evidence (bounded)."""
    out = []
    for ints in itertools.permutations(vw.intents):
        for qs in itertools.permutations(s.queries):
            out.append((dataclasses.replace(vw, intents=tuple(ints)), dataclasses.replace(s, queries=tuple(qs))))
    return out[:24]


# ------------------------------------------------------------------------------------------------ X1
def test_X1_same_exchange_order_id_cannot_resolve_two_intents():
    a = fact(1, 'entry', state=IntentState.UNKNOWN)
    b = fact(3, 'entry', state=IntentState.UNKNOWN)
    s = snap(positions=[pos('1')], queries=[(a.client_id, final(1, '1', eoid='6001')),
                                           (b.client_id, final(3, '1', eoid='6001'))],
             fills=[('6001', ok([fill('6001', '1', trade='t1')]))])
    vw = view(intents=[a, b], hints=HINT)
    v = reconcile(vw, s, now_ms=T)
    assert not resolved(v), kinds(v)
    shared = [d for d in v.of(K.HOLD) if d.detail == 'shared_venue_evidence']
    assert sorted(d.intent_id for d in shared) == sorted([a.intent_id, b.intent_id])
    assert not v.of(K.QUARANTINE) and v.outcome is Outcome.HOLD
    for pv, ps in permuted(vw, s):
        assert reconcile(pv, ps, now_ms=T) == v


def test_X1_same_trade_id_under_two_order_ids_cannot_resolve_two_intents():
    a = fact(1, 'entry', state=IntentState.UNKNOWN)
    b = fact(3, 'entry', state=IntentState.UNKNOWN)
    s = snap(positions=[pos('2')], queries=[(a.client_id, final(1, '1', eoid='6001')),
                                           (b.client_id, final(3, '1', eoid='6003'))],
             fills=[('6001', ok([fill('6001', '1', trade='t1')])), ('6003', ok([fill('6003', '1', trade='t1')]))])
    v = reconcile(view(intents=[a, b], hints=HINT), s, now_ms=T)
    assert not resolved(v), kinds(v)
    assert len([d for d in v.of(K.HOLD) if d.detail == 'shared_venue_evidence']) == 2


def test_X1_duplicate_client_id_never_resolves_either_intent():
    a = fact(1, 'entry', state=IntentState.UNKNOWN)
    b = dataclasses.replace(fact(3, 'entry', state=IntentState.UNKNOWN), client_id=a.client_id)
    s = snap(positions=[pos('1')], queries=[(a.client_id, final(1, '1', eoid='6001'))],
             fills=[('6001', ok([fill('6001', '1', trade='t1')]))])
    v = reconcile(view(intents=[a, b], hints=HINT), s, now_ms=T)
    assert not resolved(v), kinds(v)
    assert ('hold', 'R10', 'duplicate_client_id') in kinds(v)


def test_X1_one_cancel_record_cannot_resolve_two_intents_either():
    """Same exchange order id with nothing executed (no trade rows): the ORDER claim alone makes it shared."""
    a = fact(1, 'entry', state=IntentState.UNKNOWN)
    b = fact(3, 'entry', state=IntentState.UNKNOWN)
    s = snap(positions=[], queries=[(a.client_id, final(1, '0', eoid='6001')),
                                    (b.client_id, final(3, '0', eoid='6001'))])
    v = reconcile(view(intents=[a, b]), s, now_ms=T)
    assert not resolved(v), kinds(v)
    assert len([d for d in v.of(K.HOLD) if d.detail == 'shared_venue_evidence']) == 2


def test_X1_duplicate_client_id_lost_entries_are_never_corroborated():
    """No order record at all (NOT_FOUND): the duplicate client id alone blocks both."""
    from rec_helpers import pos as p_
    from newcore.domain import Lookup
    from newcore.domain.orders import PositionRead
    a = fact(1, 'entry', state=IntentState.UNKNOWN, lookup=Lookup.NOT_FOUND, sent=T - 60_000)
    b = dataclasses.replace(fact(3, 'entry', state=IntentState.UNKNOWN, lookup=Lookup.NOT_FOUND, sent=T - 60_000),
                            client_id=a.client_id)
    reads = [(f.intent_id, (PositionRead(at_ms=T - 30_000, qty=D(0)),)) for f in (a, b)]
    v = reconcile(view(intents=[a, b], corroboration=reads), snap(queries=[(a.client_id, not_found(1))]), now_ms=T)
    assert not resolved(v), kinds(v)
    assert len([d for d in v.of(K.HOLD) if d.detail == 'shared_venue_evidence']) == 2


def test_X1_one_claimant_still_resolves():
    a = fact(1, 'entry', state=IntentState.UNKNOWN)
    s = snap(positions=[pos('1')], queries=[(a.client_id, final(1, '1', eoid='6001'))],
             fills=[('6001', ok([fill('6001', '1', trade='t1')]))])
    v = reconcile(view(intents=[a], hints=HINT), s, now_ms=T)
    assert [d.intent_id for d in v.of(K.RESOLVE_FILLED)] == [a.intent_id]


# ------------------------------------------------------------------------------------------------ X2
def surplus_case():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    return vw, snap(positions=[pos('2')], orders=[order(stop.client_id)])


POLICY_FLIPS = {
    'freshness_ms': 60_000, 'visibility_ms': 1_000, 'corroboration_reads': 3, 'max_attempts': 5,
    'settle_ms': 5_000, 'reopen_window_ms': 1, 'quarantine_per_side': False, 'adopt_external_change': False,
    'protect_surplus': False, 'auto_clear_hold': False, 'auto_clearable': frozenset(),
}


def test_X2_every_policy_field_is_in_the_reconciliation_id():
    vw, s = surplus_case()
    base = reconcile(vw, s, now_ms=T)
    assert {f.name for f in dataclasses.fields(RecPolicy)} == set(POLICY_FLIPS)
    for name, value in POLICY_FLIPS.items():
        other = reconcile(vw, s, now_ms=T, policy=dataclasses.replace(RecPolicy(), **{name: value}))
        assert other.reconciliation_id != base.reconciliation_id, name
    off = reconcile(vw, s, now_ms=T, policy=RecPolicy(protect_surplus=False))
    assert off.decisions != base.decisions                       # the flip changes the actions: the id must differ


def test_X2_swapping_role_fields_changes_the_id():
    vw, s = surplus_case()
    a = dataclasses.replace(vw, binding_confirmed=True, has_history=False)
    b = dataclasses.replace(vw, binding_confirmed=False, has_history=True)
    assert reconcile(a, s, now_ms=T).reconciliation_id != reconcile(b, s, now_ms=T).reconciliation_id
    held = dataclasses.replace(vw, mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL, hold_reasons=())
    assert reconcile(held, s, now_ms=T).reconciliation_id != reconcile(vw, s, now_ms=T).reconciliation_id
    for trig in Trigger:
        if trig is not Trigger.CYCLE:
            assert reconcile(vw, s, now_ms=T, trigger=trig).reconciliation_id != \
                reconcile(vw, s, now_ms=T).reconciliation_id


def test_X2_snapshot_role_fields_are_tagged():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    long_ = snap(positions=[pos('1'), pos('0', side='SHORT')], orders=[order(stop.client_id)])
    swapped = snap(positions=[pos('0'), pos('1', side='SHORT')], orders=[order(stop.client_id)])
    assert reconcile(vw, long_, now_ms=T).reconciliation_id != reconcile(vw, swapped, now_ms=T).reconciliation_id


def test_X2_same_id_implies_same_verdict_over_input_permutations():
    vw, s = surplus_case()
    ids = {}
    for pol in (RecPolicy(), RecPolicy(protect_surplus=False), RecPolicy(settle_ms=1)):
        for trig in (Trigger.CYCLE, Trigger.TICK):
            v = reconcile(vw, s, now_ms=T, trigger=trig, policy=pol)
            assert ids.setdefault(v.reconciliation_id, v) == v


# ------------------------------------------------------------------------------------------------ X3
def test_X3_conflicting_duplicate_query_answers_are_a_hold_in_any_order():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    answers = [(stop.client_id, final(2, '0', eoid='5001')), (stop.client_id, not_found(2))]
    a = reconcile(vw, snap(positions=[pos('1')], queries=answers), now_ms=T)
    b = reconcile(vw, snap(positions=[pos('1')], queries=answers[::-1]), now_ms=T)
    assert a == b
    assert ('hold', 'R15', 'conflicting_duplicate_evidence') in kinds(a) and not resolved(a)


def test_X3_identical_duplicates_collapse_once():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    one = reconcile(vw, snap(positions=[pos('1')], queries=[(stop.client_id, final(2, '0', eoid='5001'))]), now_ms=T)
    two = reconcile(vw, snap(positions=[pos('1')], queries=[(stop.client_id, final(2, '0', eoid='5001'))] * 2),
                    now_ms=T)
    assert one.decisions == two.decisions and one.outcome is two.outcome
    assert one.reconciliation_id == two.reconciliation_id


def test_X3_conflicting_duplicate_fill_reads_are_a_hold_in_any_order():
    e = fact(1, 'entry', state=IntentState.UNKNOWN)
    reads = [('6001', ok([fill('6001', '1', trade='t1')])), ('6001', ok([fill('6001', '0.5', trade='t9')]))]
    base = dict(positions=[pos('1')], queries=[(e.client_id, final(1, '1', eoid='6001'))])
    a = reconcile(view(intents=[e], hints=HINT), snap(fills=reads, **base), now_ms=T)
    b = reconcile(view(intents=[e], hints=HINT), snap(fills=reads[::-1], **base), now_ms=T)
    assert a == b and ('hold', 'R15', 'conflicting_duplicate_evidence') in kinds(a) and not resolved(a)


def test_X3_conflicting_duplicate_trade_windows_are_a_hold_in_any_order():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, qty='2', stop_intent=stop.intent_id)], intents=[stop])
    w1 = TradeWindow(from_ms=0, read=ok([fill('990', '1', trade='m1')]))
    w2 = TradeWindow(from_ms=0, read=ok([]))
    tw = [((SYM, 'LONG'), w1), ((SYM, 'LONG'), w2)]
    base = dict(positions=[pos('1')], orders=[order(stop.client_id)])
    a = reconcile(vw, snap(trades=tw, **base), now_ms=T)
    b = reconcile(vw, snap(trades=tw[::-1], **base), now_ms=T)
    assert a == b and ('hold', 'R15', 'conflicting_duplicate_evidence') in kinds(a) and not resolved(a)


def test_X3_snapshot_lookups_never_last_win():
    from newcore.reconcile.model import DuplicateEvidence
    s = snap(queries=[(cid(2), final(2, '0', eoid='5001')), (cid(2), not_found(2))])
    with pytest.raises(DuplicateEvidence):
        s.query(cid(2))
    same = snap(queries=[(cid(2), not_found(2))] * 2)
    assert same.query(cid(2)) == not_found(2)
