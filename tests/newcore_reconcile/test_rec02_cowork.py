"""Cowork's REC-02 findings on nc-rec02 454d8db / 8e51504 (posted on PR #38): one repro per finding, written to FAIL on
8e51504 and pass once fixed.

  C1  one venue fill explains at most one thing: never both resolve_filled (R08) and adopt (R03) from the same trade
  C2  a triggered OWN algo stop's child fills are matched to the owned stop, never adopted as a manual reduce
  C3  an owned PROTECT whose answer is UNKNOWN but which the venue lists counts as cover (no extra stop)
  C4  the same client id twice in one open-orders read is a HOLD item, never last-wins / order-dependent
  C5  two lost ENTRY / ADD intents on one side are attributed only when provable, else HOLD
  C6  cover needs a reduce-only STOP_MARKET on the intent's symbol and side; anything else is an R18 mismatch item
  C7  in hard HOLD (durability) the verdict is advisory: no resolution / adoption / clear, protection only per A24
  C8  adoption only against current venue exposure: a flat venue plus a foreign fill is explained history (HOLD)
  C9  reconciliation_id is derived from the inputs (snapshot digest + attempt), not only the clock
  C10 extreme values never raise: out-of-range quantities / prices are a HOLD item (extreme fuzz included)
"""
import random
from dataclasses import replace
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, HoldKind, IntentState, Lookup
from newcore.domain.orders import PositionRead
from newcore.ports.venue import ReadKind, ReadOutcome, VenueOrder, VenuePosition
from newcore.reconcile import DecisionKind as K
from newcore.reconcile import Outcome, RecPolicy, TradeWindow, reconcile
from rec_helpers import (SYM, T, cid, fact, fill, final, kinds, known, lot, not_found, ok, order, pos, snap, view)

LOT1 = 'lot_' + f'{1:032x}'


def trade_ids(d):
    return {e.split(':')[1] for e in d.evidence if e.startswith('trade:')}


def explanations(v):
    return [d for d in v.decisions if d.kind in (K.RESOLVE_FILLED, K.ADOPT)]


# ------------------------------------------------------------------------------------------------ C1
def test_C1_one_fill_is_never_both_a_late_fill_and_an_external_reduce():
    e = fact(1, 'entry', state=IntentState.UNKNOWN)
    tw = TradeWindow(from_ms=0, read=ok([fill('6001', '1', trade='t1')]))
    s = snap(positions=[], queries=[(e.client_id, final(1, '1', eoid='6001'))],
             fills=[('6001', ok([fill('6001', '1', trade='t1')]))], trades=[((SYM, 'LONG'), tw)])
    v = reconcile(view(intents=[e]), s, now_ms=T)
    seen = [t for d in explanations(v) for t in trade_ids(d)]
    assert len(seen) == len(set(seen)), kinds(v)
    assert not v.of(K.ADOPT)
    assert v.outcome is not Outcome.FLAT


# ------------------------------------------------------------------------------------------------ C2
def test_C2_own_algo_stop_child_fill_is_never_a_manual_reduce():
    stop = fact(2, 'protect', stop='90', owner=LOT1, route='algo', eoid='9001')
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    tw = TradeWindow(from_ms=0, read=ok([fill('777', '1', '90', trade='c1')]))
    v = reconcile(vw, snap(positions=[], trades=[((SYM, 'LONG'), tw)]), now_ms=T)       # the algo query is not in
    assert not v.of(K.ADOPT), kinds(v)
    assert stop.client_id in v.needs.queries and v.outcome is Outcome.PENDING
    v2 = reconcile(vw, snap(positions=[], trades=[((SYM, 'LONG'), tw)],
                            queries=[(stop.client_id, known(2, status='FINISHED', eoid='777', detail='algo_triggered',
                                                            route='algo'))],
                            fills=[('777', ok([fill('777', '1', '90', trade='c1')]))]), now_ms=T)
    assert [d.detail for d in explanations(v2)] == ['algo_stop_filled']


# ------------------------------------------------------------------------------------------------ C3
def test_C3_listed_owned_stop_with_an_unknown_answer_is_cover_not_a_gap():
    stop = fact(2, 'protect', stop='90', owner=LOT1, state=IntentState.UNKNOWN)
    vw = view(lots=[lot(1, stop_intent=None)], intents=[stop])
    v = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id)]), now_ms=T)
    assert not v.of(K.PROTECT_ONLY), kinds(v)
    assert stop.client_id in v.needs.queries                   # confirm it (the runner books KNOWN), no new stop


# ------------------------------------------------------------------------------------------------ C4
def test_C4_duplicate_client_id_in_one_read_is_a_hold_item_and_order_independent():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    a = order(stop.client_id, eoid='5001')
    b = order(stop.client_id, eoid='5002', type_='LIMIT', stop=None, reduce=False)
    v1 = reconcile(vw, snap(positions=[pos('1')], orders=[a, b]), now_ms=T)
    v2 = reconcile(vw, snap(positions=[pos('1')], orders=[b, a]), now_ms=T)
    assert v1 == v2
    assert ('hold', 'R10', 'duplicate_listed_client_id') in kinds(v1) and v1.outcome is Outcome.HOLD


# ------------------------------------------------------------------------------------------------ C5
def lost(n, qty='1'):
    return fact(n, 'entry', qty=qty, state=IntentState.UNKNOWN, lookup=Lookup.NOT_FOUND, sent=T - 60_000)


def corr(*fs, qty):
    return [(f.intent_id, (PositionRead(at_ms=T - 30_000, qty=D(qty)),)) for f in fs]


def test_C5_two_lost_entries_are_never_attributed_arbitrarily():
    a, b = lost(1), lost(3)
    vw = view(intents=[a, b], corroboration=corr(a, b, qty='1'), hints=[((SYM, 'LONG'), D('90'))])
    s = snap(positions=[pos('1')], queries=[(a.client_id, not_found(1)), (b.client_id, not_found(3))])
    v = reconcile(vw, s, now_ms=T)
    assert not explanations(v) and not v.of(K.RESOLVE_NOT_EXECUTED), kinds(v)
    assert ('hold', 'R01', 'unattributable_multiple') in kinds(v)


@pytest.mark.parametrize('qty,expect', [('0', 'resolve_not_executed'), ('2', 'resolve_filled')])
def test_C5_two_lost_entries_resolve_only_when_provable(qty, expect):
    a, b = lost(1), lost(3)
    vw = view(intents=[a, b], corroboration=corr(a, b, qty=qty), hints=[((SYM, 'LONG'), D('90'))])
    s = snap(positions=[pos(qty)] if qty != '0' else [],
             queries=[(a.client_id, not_found(1)), (b.client_id, not_found(3))])
    v = reconcile(vw, s, now_ms=T)
    got = [d for d in v.decisions if d.kind in (K.RESOLVE_FILLED, K.RESOLVE_NOT_EXECUTED)]
    assert sorted(d.intent_id for d in got) == sorted([a.intent_id, b.intent_id])
    assert {d.kind.value for d in got} == {expect}


# ------------------------------------------------------------------------------------------------ C6
@pytest.mark.parametrize('bad', [dict(type_='LIMIT'), dict(reduce=False), dict(side='SHORT'), dict(symbol='BTCUSDT')])
def test_C6_cover_needs_a_reduce_only_stop_on_the_right_symbol_and_side(bad):
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    v = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id, **bad)]), now_ms=T)
    assert ('hold', 'R18', 'order_mismatch') in kinds(v), kinds(v)
    assert v.outcome is Outcome.HOLD


# ------------------------------------------------------------------------------------------------ C7
def test_C7_hard_hold_verdict_is_advisory():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    e = fact(3, 'entry', state=IntentState.UNKNOWN)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop, e], mode=EntriesMode.HOLD,
              hold_kind=HoldKind.DURABILITY_UNAVAILABLE, reasons=())
    s = snap(positions=[pos('1')], queries=[(stop.client_id, final(2, '0', eoid='5001')),
                                           (e.client_id, final(3, '1', eoid='6003'))],
             fills=[('6003', ok([fill('6003', '1', trade='x')]))])
    v = reconcile(vw, s, now_ms=T)
    assert not (v.of(K.RESOLVE_FILLED) + v.of(K.RESOLVE_NOT_EXECUTED) + v.of(K.ADOPT) + v.of(K.CLEAR_HOLD)), kinds(v)
    assert all(d.detail == 'a24_emergency' and d.lot_id for d in v.of(K.PROTECT_ONLY))
    assert v.outcome is Outcome.HOLD


# ------------------------------------------------------------------------------------------------ C8
def test_C8_a_foreign_fill_with_a_flat_venue_is_explained_history_not_an_adoption():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    tw = TradeWindow(from_ms=0, read=ok([fill('990', '1', trade='m1')]))
    v = reconcile(vw, snap(positions=[], orders=[order(stop.client_id)], trades=[((SYM, 'LONG'), tw)]), now_ms=T)
    assert not v.of(K.ADOPT), kinds(v)
    h = [d for d in v.of(K.HOLD) if d.detail == 'external_close_explained']
    assert h and 'm1' in trade_ids(h[0]) and v.outcome is Outcome.HOLD


# ------------------------------------------------------------------------------------------------ C9
def test_C9_reconciliation_id_differs_for_different_snapshots_at_the_same_ms():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    a = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id)]), now_ms=T)
    b = reconcile(vw, snap(positions=[pos('2')], orders=[order(stop.client_id)]), now_ms=T)
    assert a.reconciliation_id != b.reconciliation_id
    c = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id)]), now_ms=T, attempt=1)
    assert c.reconciliation_id != a.reconciliation_id
    same = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id)]), now_ms=T)
    assert same.reconciliation_id == a.reconciliation_id


# ------------------------------------------------------------------------------------------------ C10
# The port records (VenuePosition / VenueOrder / VenueFill) refuse |exponent| > 18 themselves; the journal-side view
# (LotFact / IntentFact / hints / reads) is the fold's own input and is NOT validated on construction.
PORT_EXTREMES = ['1E+18', '1E-18', '999999999999999999', '0.000000000000000001', '1', '0.5', '3',
                 '12345678901234567.890123456789012345', '0.12345678901234567890123456789012345678']
VIEW_EXTREMES = PORT_EXTREMES + ['1E+30', '1E-30', '1' * 70, '0.' + '9' * 60]


def extreme_case(rnd):
    q = D(rnd.choice(PORT_EXTREMES))
    lq = D(rnd.choice(VIEW_EXTREMES))
    stop = fact(2, 'protect', stop=rnd.choice(VIEW_EXTREMES), owner=LOT1, qty=str(lq))
    vw = view(lots=[lot(1, qty=str(lq), stop_intent=stop.intent_id, stop=rnd.choice(VIEW_EXTREMES))], intents=[stop],
              hints=[((SYM, 'SHORT'), D(rnd.choice(VIEW_EXTREMES)))])
    orders = [order(stop.client_id, qty=str(lq), stop=rnd.choice(PORT_EXTREMES))] \
        if str(lq) in PORT_EXTREMES and rnd.random() < 0.7 else []
    tw = TradeWindow(from_ms=0, read=ok([fill('990', rnd.choice(PORT_EXTREMES), rnd.choice(PORT_EXTREMES), trade='m'),
                                         fill('991', rnd.choice(PORT_EXTREMES), rnd.choice(PORT_EXTREMES), trade='n')]))
    pr = ReadOutcome(kind=ReadKind.OK, observed_at_ms=T,
                     value=(VenuePosition(symbol=SYM, side='LONG', qty=q, entry_price=D(rnd.choice(PORT_EXTREMES))),))
    return vw, snap(pos_read=pr, orders=orders, trades=[((SYM, 'LONG'), tw)])


def test_C10_extreme_values_never_raise_and_out_of_range_is_a_hold():
    stop = fact(2, 'protect', stop='90', owner=LOT1, qty='1E+30')
    vw = view(lots=[lot(1, qty='1E+30', stop_intent=stop.intent_id)], intents=[stop])
    v = reconcile(vw, snap(positions=[pos('1')]), now_ms=T)
    assert ('hold', 'R15', 'value_out_of_range') in kinds(v) and v.outcome is Outcome.HOLD
    assert not v.of(K.PROTECT_ONLY) and not explanations(v)


@pytest.mark.parametrize('seed', range(300))
def test_C10_extreme_fuzz_never_raises(seed):
    rnd = random.Random(seed)
    vw, s = extreme_case(rnd)
    pol = RecPolicy(settle_ms=rnd.choice([0, 5000]))
    v = reconcile(vw, s, now_ms=T, policy=pol)
    assert v.outcome in Outcome and v == reconcile(vw, s, now_ms=T, policy=pol)
