"""Frozen NC-01 (#38, Codex P1): OrderIntent.replaces_intent_id is the explicit cancel-replace link, allowed only on a lot
REDUCE / CLOSE. The driver's `to_order_intent` sets it from the draft's `replaces` for those purposes and None for
everything else; a stop replacement keeps its predecessor inside the driver (Binding.replaces), never on the intent."""
import dataclasses
from decimal import Decimal as D

import pytest

from mg_factories import LONG, SHORT, T0, ZERO_COSTS, plan
from newcore.domain import DomainError, OrderIntent, Purpose, ReasonCode, make_id
from newcore.management import driver as DR
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind

ACCT, LOT, DEC = make_id('acct', 71), make_id('lot', 72), make_id('dec', 73)
SIDES = (LONG, SHORT)


def known(p, d, xid):
    return OrderOutcome(kind=OutcomeKind.KNOWN, ref=OrderRef(symbol=p.symbol, client_id=d.client_id, route=d.route),
                        observed_at_ms=T0, status='NEW', exchange_order_id=xid)


@pytest.mark.parametrize('side', SIDES)
def test_a_stop_replacement_intent_has_no_link_but_the_driver_keeps_its_predecessor(side):
    p = plan(side, tp1_frac='0.5', tp1_off='1', tp2_off='2', costs=ZERO_COSTS)
    drv = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0))
    (s0,) = drv.submits
    ds = DR.on_outcome(drv.state, known(p, s0, 'x0'), submit=True).state
    # the core shrinks the stop after a TP1 fill: a replacement PROTECT is drafted while s0 keeps working
    from newcore.ports.venue import VenueFill
    r = DR.on_mark(ds, ds.pos.tp1.price)
    tp = r.submits[0]
    fin = OrderOutcome(kind=OutcomeKind.FINAL, ref=OrderRef(symbol=p.symbol, client_id=tp.client_id, route=tp.route),
                       observed_at_ms=T0, status='FILLED', exchange_order_id='x1', executed_qty=tp.qty,
                       avg_price=ds.pos.tp1.price)
    r = DR.on_outcome(r.state, fin, submit=True)
    r = DR.on_fills(r.state, (VenueFill(trade_id='t1', exchange_order_id='x1', symbol=p.symbol,
                                        position_side=p.side.value, qty=tp.qty, price=ds.pos.tp1.price, fee=D(0),
                                        fee_asset='USDT', realized_pnl=D(0), maker=False, at_ms=T0),))
    (s1,) = [d for d in r.submits if d.purpose is Purpose.PROTECT]
    b1 = next(b for b in r.state.bindings if b.intent_id == s1.intent_id)
    assert b1.replaces == s0.intent_id                                        # the driver's own link
    it = DR.to_order_intent(s1, decision_id=DEC, at_ms=T0)
    assert it.replaces_intent_id is None                                      # never on a PROTECT intent
    for d in (s0, tp, s1):
        assert DR.to_order_intent(d, decision_id=DEC, at_ms=T0).replaces_intent_id is None


@pytest.mark.parametrize('purpose', (Purpose.REDUCE, Purpose.CLOSE))
def test_a_reduce_or_close_draft_with_a_predecessor_carries_the_link(purpose):
    p = plan(LONG, costs=ZERO_COSTS)
    drv = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0))
    (s0,) = drv.submits
    pred, succ = make_id('int', 81), make_id('int', 82)
    d = dataclasses.replace(s0, purpose=purpose, stop_price=None, order_type=DR.OrderType.MARKET, intent_id=succ,
                            client_id='zbn1o-test', replaces=pred, reason=ReasonCode.EXIT_TIME)
    it = DR.to_order_intent(d, decision_id=DEC, at_ms=T0)
    assert isinstance(it, OrderIntent) and it.replaces_intent_id == pred


def test_a_protect_draft_never_carries_a_link_even_if_one_is_set():
    p = plan(LONG, costs=ZERO_COSTS)
    (s0,) = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0)).submits
    d = dataclasses.replace(s0, replaces=make_id('int', 91))
    assert DR.to_order_intent(d, decision_id=DEC, at_ms=T0).replaces_intent_id is None
    with pytest.raises(DomainError):                                          # NC-01 itself refuses such a link
        dataclasses.replace(DR.to_order_intent(s0, decision_id=DEC, at_ms=T0), replaces_intent_id=make_id('int', 91))
