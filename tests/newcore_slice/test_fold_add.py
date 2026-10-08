"""Fold.lots(): an ADD fill (an opening intent owned by the lot) INCREASES the lot (qty, blended average, peak); it is
never booked as a closing (gap map M4: the S1 fold booked every owned FINAL fill as a closing)."""
from decimal import Context, Decimal as D

from newcore.domain import Action, Authority, Decision, DecisionRecorded, IntentState, Purpose, ReasonCode, Side
from newcore.ports.venue import MarketOrder
from newcore.runner import InjectedSignals
from newcore.runner import ids
from newcore.runner.records import planned_intent
from slice_helpers import H4, World, flat_bars

SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms


def _world(side='LONG'):
    sig = InjectedSignals({(SYM, T0 + 6 * H4): (('enter', side),)}, stop_atr=D('2'))
    w = World(flat_bars(20, overrides={7: ('100', '100.5', '99', '99')}), sig)
    w.run(6)
    return w


def _add(w, qty):
    """Record + send one lineage ADD of the open lot (written straight to the journal: this is a fold test)."""
    r = w.runner
    lot = r.fold.open_lots()[0]
    iid = r.journal.gate().grammar.next_child_intent_id(lot.lot_id, Purpose.ADD)
    did = ids.child_decision_id(iid)
    planned = planned_intent(intent_id=iid, account_id=r.acct, decision_id=did, purpose='add', symbol=SYM,
                             side=lot.side, qty=qty, reason=ReasonCode.ENTRY_DCA_LEVEL, at_ms=r.now,
                             owner_id=lot.lot_id)
    d = Decision(decision_id=did, account_id=r.acct, at_ms=r.now, action=Action.ADD, reason=ReasonCode.ENTRY_DCA_LEVEL,
                 authority=Authority.STRATEGY, key=None, evidence=(), symbol=SYM, side=Side(lot.side),
                 subject_id=lot.lot_id, detail='add', intents=(planned,), policy_version='nc-s1')
    r._emit(DecisionRecorded, reason=d.reason, decision=d)
    iv = r._record_durable(planned)
    r._state(iv, IntentState.SUBMITTED)
    out = w.venue.submit_market(MarketOrder(ref=r._ref(iv), position_side=lot.side, qty=qty, reduce=False))
    r._apply(iv, out, submit=True)
    return iv


def test_an_add_fill_increases_the_lot_and_is_not_a_closing():
    w = _world()
    w.venue.advance_to(w.close_ms(7))
    w.runner.now = w.close_ms(7)
    lot0 = w.runner.fold.open_lots()[0]
    q0, p0 = lot0.qty, lot0.avg_price
    iv = _add(w, q0)
    assert iv.final is not None and iv.executed == q0
    lot = w.runner.fold.open_lots()[0]
    assert lot.lot_id == lot0.lot_id
    assert lot.closings == []                                   # the S1 fold booked it here: qty 0, lot "closed"
    assert lot.qty == 2 * q0 and lot.initial_qty == q0 and lot.max_qty == 2 * q0
    assert [a.intent_id for a in lot.add_fills] == [iv.intent_id]
    pa = iv.final.avg_price
    assert lot.entry_price == p0
    assert lot.avg_price == Context(prec=34).divide(q0 * p0 + q0 * pa, 2 * q0)
    assert lot.closes == [] and lot.adds == [iv]                # an ADD is not a close / reduce intent
    pos = next(p for p in w.venue.positions().value if p.side == 'LONG')
    assert pos.qty == lot.qty                                   # the fold agrees with the venue position


def test_a_later_partial_close_reduces_the_added_lot():
    w = _world()
    w.venue.advance_to(w.close_ms(7))
    w.runner.now = w.close_ms(7)
    q0 = w.runner.fold.open_lots()[0].qty
    _add(w, q0)
    r = w.runner
    lot = r.fold.open_lots()[0]
    # a market reduce of the entry quantity only (a TP1-like partial close)
    iid = r.journal.gate().grammar.next_child_intent_id(lot.lot_id, Purpose.REDUCE)
    did = ids.child_decision_id(iid)
    planned = planned_intent(intent_id=iid, account_id=r.acct, decision_id=did, purpose='reduce', symbol=SYM,
                             side='LONG', qty=q0, reason=ReasonCode.EXIT_TP1, at_ms=r.now, owner_id=lot.lot_id)
    d = Decision(decision_id=did, account_id=r.acct, at_ms=r.now, action=Action.REDUCE, reason=ReasonCode.EXIT_TP1,
                 authority=Authority.STRATEGY, key=None, evidence=(), symbol=SYM, side=Side.LONG, subject_id=lot.lot_id,
                 detail='tp1', intents=(planned,), policy_version='nc-s1')
    r._emit(DecisionRecorded, reason=d.reason, decision=d)
    iv = r._record_durable(planned)
    r._state(iv, IntentState.SUBMITTED)
    r._apply(iv, w.venue.submit_market(MarketOrder(ref=r._ref(iv), position_side='LONG', qty=q0, reduce=True)),
             submit=True)
    lot = r.fold.open_lots()[0]
    assert lot.qty == q0 and lot.max_qty == 2 * q0 and len(lot.closings) == 1 and lot.open
