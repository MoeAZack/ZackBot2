"""Cowork finding 11 (INFO, open on 1d61a38): after a TIGHTER stop replacement is refused (break-even after TP1, -2021),
the venue keeps the previous stop. The core reverts to it (locked) and closes the rest (EXIT_STOP_FAILED, Codex ruling);
the driver used to submit a second PROTECT at the SAME price (resized to the smaller position) beside the still-working
old stop. Rule: while the stop is locked, a WORKING stop at the requested price that covers the requested quantity is
rebound as the current stop; nothing new is submitted. The stop is never loosened and the lot ends flat with nothing
live."""
from decimal import Decimal as D

import pytest

from mg_factories import LONG, SHORT, T0, ZERO_COSTS, plan, px
from newcore.domain import Purpose, make_id
from newcore.management import Leg, Stage
from newcore.management import driver as DR
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill

ACCT, LOT = make_id('acct', 91), make_id('lot', 92)
SIDES = (LONG, SHORT)


def out(p, d, kind, **kw):
    return OrderOutcome(kind=kind, ref=OrderRef(symbol=p.symbol, client_id=d.client_id, route=d.route),
                        observed_at_ms=T0, **kw)


def fill(p, xid, tid, q, price):
    return VenueFill(trade_id=tid, exchange_order_id=xid, symbol=p.symbol, position_side=p.side.value, qty=q,
                     price=price, fee=D(0), fee_asset='USDT', realized_pnl=D(0), maker=False, at_ms=T0)


def refused_breakeven(side, confirm_first=True):
    p = plan(side, tp1_frac='0.5', tp1_off='1', tp2_off='2', be=True, costs=ZERO_COSTS)
    r = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0))
    s0 = r.submits[0]
    if confirm_first:
        r = DR.on_outcome(r.state, out(p, s0, OutcomeKind.KNOWN, status='NEW', exchange_order_id='x0'), submit=True)
    r = DR.on_mark(r.state, px(side, '101.1'))
    tp = r.submits[0]
    r = DR.on_outcome(r.state, out(p, tp, OutcomeKind.FINAL, status='FILLED', exchange_order_id='x1',
                                   executed_qty=tp.qty, avg_price=px(side, '101.1')), submit=True)
    r = DR.on_fills(r.state, (fill(p, 'x1', 't1', tp.qty, px(side, '101.1')),))
    (s1,) = [d for d in r.submits if d.purpose is Purpose.PROTECT]
    assert (s1.stop_price, s1.qty) == (px(side, '100.02'), D('2.5'))              # the tighter break-even
    r = DR.on_outcome(r.state, out(p, s1, OutcomeKind.REJECTED, error_code=-2021), submit=True)
    return p, s0, r


@pytest.mark.parametrize('side', SIDES)
def test_refused_tighter_stop_keeps_the_working_old_stop_and_submits_no_duplicate(side):
    p, s0, r = refused_breakeven(side)
    assert not [d for d in r.submits if d.purpose is Purpose.PROTECT]             # no second same-price stop
    (close,) = [d for d in r.submits if d.leg is Leg.CLOSE]                        # the rest is closed (ruling)
    assert close.reason.value == 'exit.stop_failed' and close.qty == D('2.5')
    live = [b for b in r.state.bindings if b.leg is Leg.STOP and b.state is DR.BindState.WORKING]
    assert [(b.client_id, b.current) for b in live] == [(s0.client_id, True)]
    assert r.state.pos.stop.price == px(side, '98.02') and r.state.pos.stop_locked
    assert DR.protected(r.state)                                                   # the old stop still covers
    # nothing more on the next mark / candle while locked
    again = DR.on_mark(r.state, px(side, '100.5'))
    assert not again.submits and not again.cancels
    # the close fills: flat, the old stop is released
    r = DR.on_outcome(r.state, out(p, close, OutcomeKind.FINAL, status='FILLED', exchange_order_id='x2',
                                   executed_qty=close.qty, avg_price=px(side, '100.5')), submit=True)
    r = DR.on_fills(r.state, (fill(p, 'x2', 't2', close.qty, px(side, '100.5')),))
    assert r.state.pos.stage is Stage.DONE and [c.client_id for c in r.cancels] == [s0.client_id]
    r = DR.on_outcome(r.state, out(p, s0, OutcomeKind.FINAL, status='CANCELED', exchange_order_id='x0',
                                   executed_qty=D(0)), submit=False)
    assert not [b for b in r.state.bindings if b.leg is Leg.STOP and b.state in (DR.BindState.SENT,
                                                                                    DR.BindState.WORKING)]


@pytest.mark.parametrize('side', SIDES)
def test_the_old_stop_firing_instead_of_the_close_is_booked_once(side):
    """The kept old stop (qty 5 at the venue, reduce-only) fires on the 2.5 left before the close executes: 2.5 is
    booked once, the close finds nothing (reported), flat, nothing live."""
    p, s0, r = refused_breakeven(side)
    (close,) = [d for d in r.submits if d.leg is Leg.CLOSE]
    r = DR.on_outcome(r.state, out(p, s0, OutcomeKind.FINAL, status='FILLED', exchange_order_id='x0',
                                   executed_qty=D('2.5'), avg_price=px(side, '98.02')), submit=False)
    r = DR.on_fills(r.state, (fill(p, 'x0', 't3', D('2.5'), px(side, '98.02')),))
    r = DR.on_outcome(r.state, out(p, close, OutcomeKind.FINAL, status='EXPIRED', exchange_order_id='x2',
                                   executed_qty=D(0)), submit=True)
    assert r.state.pos.stage is Stage.DONE and r.state.pos.qty == 0
    assert sum(f.qty for f in r.state.pos.fills if f.leg is Leg.STOP) == D('2.5')
    assert not [b for b in r.state.bindings if b.state in (DR.BindState.SENT, DR.BindState.WORKING)]


@pytest.mark.parametrize('answer', ('known', 'unknown_then_known', 'rejected'))
@pytest.mark.parametrize('side', SIDES)
def test_an_unconfirmed_old_stop_is_waited_for_not_duplicated(side, answer):
    """Cowork INFO (fuzz seed 99): the ORIGINAL stop is still in flight (SENT, or its answer lost) when the tighter
    replacement is refused. No second same-price stop is sent beside it: the driver waits for its resolution."""
    p, s0, r = refused_breakeven(side, confirm_first=False)
    assert not [d for d in r.submits if d.purpose is Purpose.PROTECT]
    (cur,) = [b for b in r.state.bindings if b.leg is Leg.STOP and b.current]
    assert cur.client_id == s0.client_id and cur.state is DR.BindState.SENT
    if answer == 'unknown_then_known':
        r = DR.on_outcome(r.state, out(p, s0, OutcomeKind.UNKNOWN), submit=True)
        assert not r.submits
    if answer == 'rejected':        # it never existed: nothing protects the rest -> ONE new stop (not a duplicate: the
        r = DR.on_outcome(r.state, out(p, s0, OutcomeKind.REJECTED, error_code=-2021), submit=True)   # only one live)
        (s2,) = [d for d in r.submits if d.purpose is Purpose.PROTECT]
        assert (s2.stop_price, s2.qty) == (px(side, '98.02'), D('2.5'))
        live = [b for b in r.state.bindings if b.leg is Leg.STOP and b.state in (DR.BindState.SENT,
                                                                                  DR.BindState.WORKING)]
        assert [b.client_id for b in live] == [s2.client_id]
        assert r.state.pos.closing == r.state.pos.qty                     # and the rest is still being closed
    else:
        r = DR.on_outcome(r.state, out(p, s0, OutcomeKind.KNOWN, status='NEW', exchange_order_id='x0'), submit=True)
        assert not r.submits and DR.protected(r.state)
        live = [b for b in r.state.bindings if b.leg is Leg.STOP and b.state is DR.BindState.WORKING]
        assert [b.client_id for b in live] == [s0.client_id]
