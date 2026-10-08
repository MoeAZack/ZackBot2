"""Cowork's re-check of nc-rec02 e3c0e2f (all ten earlier findings FIXED): four new LOW items, one repro group each,
written to FAIL on bd76399 and pass once fixed.

  L1 a mismatched OWNED stop is eventually cancelled: at once when it can ADD exposure (not reduce-only: a trigger
     would open risk), else only after a correct replacement is confirmed; never in hard HOLD unless it can add exposure
  L2 hard HOLD protects the FULL venue exposure of a side the account owns (the conservative A24 reading), whatever
     the Q3 surplus policy says
  L3 a side carrying a duplicate-client-id HOLD gets no extra stop while ANY stop is listed on it (no stacking)
  L4 every HOLD / QUARANTINE item carries a deterministic incident id (inc_ + 32 hex) from the account, the item and
     the evidence it rests on - not the clock or attempt - so a restart re-derives the same id
"""
from decimal import Decimal as D

from newcore.domain import EntriesMode, HoldKind, IntentState
from newcore.domain.base import check_id
from newcore.reconcile import DecisionKind as K
from newcore.reconcile import Outcome, RecPolicy, Trigger, reconcile
from rec_helpers import SYM, T, fact, kinds, lot, order, pos, snap, view

LOT1 = 'lot_' + f'{1:032x}'
HARD = dict(mode=EntriesMode.HOLD, hold_kind=HoldKind.DURABILITY_UNAVAILABLE, reasons=())


def cancels(v):
    return [d for d in v.decisions if d.kind is K.CANCEL_MISMATCHED_PROTECT]


# ------------------------------------------------------------------------------------------------ L1
def test_L1_an_owned_stop_that_can_add_exposure_is_cancelled_first():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    s = snap(positions=[pos('1')], orders=[order(stop.client_id, reduce=False)])
    v = reconcile(vw, s, now_ms=T)
    c, = cancels(v)
    assert (c.client_id, c.intent_id, c.detail) == (stop.client_id, stop.intent_id, 'exposure_risk')
    p, = v.of(K.PROTECT_ONLY)                                     # the replacement goes out FIRST (Cowork, 5bfe423),
    i, j = v.decisions.index(p), v.decisions.index(c)             # then the cancel right after it, same verdict, without
    assert j == i + 1                                             # waiting for the replacement's confirmation
    hard = reconcile(view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop], **HARD), s, now_ms=T)
    assert [d.detail for d in cancels(hard)] == ['exposure_risk']     # even in hard HOLD: it could add exposure


def test_L1_a_reduce_only_mismatched_stop_waits_for_a_confirmed_replacement():
    bad = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=bad.intent_id)], intents=[bad])
    s = snap(positions=[pos('1')], orders=[order(bad.client_id, type_='LIMIT')])
    v = reconcile(vw, s, now_ms=T)
    assert not cancels(v) and v.of(K.PROTECT_ONLY) and ('hold', 'R18', 'order_mismatch') in kinds(v)
    good = fact(4, 'protect', stop='90', owner=LOT1)                  # the replacement, confirmed and listed
    vw2 = view(lots=[lot(1, stop_intent=good.intent_id)], intents=[bad, good])
    s2 = snap(positions=[pos('1')], orders=[order(bad.client_id, type_='LIMIT'), order(good.client_id, eoid='5002')])
    v2 = reconcile(vw2, s2, now_ms=T)
    c, = cancels(v2)
    assert (c.intent_id, c.detail) == (bad.intent_id, 'replacement_confirmed')
    assert not v2.of(K.PROTECT_ONLY)
    hard = reconcile(view(lots=[lot(1, stop_intent=good.intent_id)], intents=[bad, good], **HARD), s2, now_ms=T)
    assert not cancels(hard)                                          # never removes protection in hard HOLD


def test_L1_hard_hold_flags_an_oversized_reduce_only_stop_and_does_not_cancel_it():
    """Decided per A24 (Cowork on 5bfe423): an over-sized REDUCE-ONLY stop (qty 5 vs position 1) cannot add exposure -
    the venue caps a reduce-only order at the position - so in hard HOLD it is an R18 item only, never cancelled (no
    protection is removed while nothing can be journaled); the side's exposure is still protected (A24 full side)."""
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop], **HARD)
    v = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id, qty='5')]), now_ms=T)
    assert not cancels(v)
    assert ('hold', 'R18', 'order_mismatch') in kinds(v)
    assert [d.detail for d in v.of(K.PROTECT_ONLY)] == ['a24_emergency']


# ------------------------------------------------------------------------------------------------ L2
def test_L2_hard_hold_protects_the_full_venue_exposure_of_an_owned_side():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    s = snap(positions=[pos('3')], orders=[order(stop.client_id)])
    for pol in (RecPolicy(), RecPolicy(protect_surplus=False)):
        v = reconcile(view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop], **HARD), s, now_ms=T, policy=pol)
        p, = v.of(K.PROTECT_ONLY)
        assert (p.qty, p.detail, p.lot_id) == (D('2'), 'a24_emergency', LOT1)
        assert 'a24:full_side_exposure' in p.evidence
    foreign = reconcile(view(intents=[], **HARD), snap(positions=[pos('3')]), now_ms=T)
    assert not foreign.of(K.PROTECT_ONLY)                             # a side the account never owned: A22


# ------------------------------------------------------------------------------------------------ L3
def test_L3_a_duplicate_client_id_side_with_a_listed_stop_gets_no_extra_stop():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    a = order(stop.client_id, eoid='5001')
    b = order(stop.client_id, eoid='5002')
    v = reconcile(vw, snap(positions=[pos('1')], orders=[a, b]), now_ms=T)
    assert ('hold', 'R10', 'duplicate_listed_client_id') in kinds(v)
    assert not v.of(K.PROTECT_ONLY), kinds(v)
    none_stop = [order(stop.client_id, eoid='5001', type_='LIMIT', stop=None),
                 order(stop.client_id, eoid='5002', type_='LIMIT', stop=None)]
    v2 = reconcile(vw, snap(positions=[pos('1')], orders=none_stop), now_ms=T)
    assert v2.of(K.PROTECT_ONLY)                                      # no stop at all on the side: protect it


# ------------------------------------------------------------------------------------------------ L4
def test_L4b_an_outage_is_one_incident_not_one_per_read():
    """Cowork on 5bfe423: the read TIME was in the incident id, so every unreadable / stale read was a new incident."""
    from rec_helpers import ok, unknown
    from newcore.domain.orders import PositionRead
    from newcore.domain import Lookup
    vw = view()
    ids = {reconcile(vw, snap(pos_read=unknown(T + i * 1000), ord_read=ok([], T + i * 1000)), now_ms=T + i * 1000
                     ).of(K.HOLD)[0].incident_id for i in range(5)}
    assert len(ids) == 1                                             # 5 unreadable reads -> one incident
    stale = {reconcile(vw, snap(at=T + i * 1000 - 60_000), now_ms=T + i * 1000, attempt=9).of(K.HOLD)[0].incident_id
             for i in range(5)}
    assert len(stale) == 1                                           # 5 stale reads at the last attempt -> one
    e = fact(1, 'entry', state=IntentState.UNKNOWN, lookup=Lookup.NOT_FOUND, sent=T - 60_000)
    waits = set()
    for i in range(3):                                               # corroboration reads move on, the item does not
        vw2 = view(intents=[e], corroboration=[(e.intent_id, (PositionRead(at_ms=T - 30_000 + i, qty=D(1)),))])
        v = reconcile(vw2, snap(at=T + i, queries=[]), now_ms=T + i)
        waits |= {d.incident_id for d in v.of(K.HOLD)}
    assert len(waits) == 1


def test_L4_incident_ids_are_deterministic_and_clock_free():
    stop = fact(2, 'protect', stop='90', owner=LOT1)
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    s = snap(positions=[pos('1')], orders=[order(stop.client_id, type_='LIMIT')])
    a = reconcile(vw, s, now_ms=T)
    b = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id, type_='LIMIT')], at=T + 5000),
                  now_ms=T + 5000, attempt=1, trigger=Trigger.STARTUP)      # "after a restart"
    ha = [d for d in a.decisions if d.kind in (K.HOLD, K.QUARANTINE)]
    hb = [d for d in b.decisions if d.kind in (K.HOLD, K.QUARANTINE)]
    assert ha and [d.incident_id for d in ha] == [d.incident_id for d in hb]
    for d in ha:
        check_id(d.incident_id, 'incident_id', 'inc')
    other = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id, type_='LIMIT', qty='2')]),
                      now_ms=T)
    assert {d.incident_id for d in other.of(K.HOLD)} != {d.incident_id for d in a.of(K.HOLD)}
    assert all(d.incident_id == '' for d in a.decisions if d.kind not in (K.HOLD, K.QUARANTINE))
    assert a.outcome is Outcome.HOLD
