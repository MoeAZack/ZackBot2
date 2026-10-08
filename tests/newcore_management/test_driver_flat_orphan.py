"""TNET-01 T08 (cancel/replace race, fill wins): the break-even replacement is in flight when the OLD stop fills the
rest of the position. The core books the old stop's fill as its stop leg and is DONE - it has no stop left to cancel,
so the replacement would stay live at the venue on a flat position (an orphan that could open a new position on a
reversal). The driver cancels every stop it still has working once the lot is flat, whichever way the race lands."""
import itertools
from decimal import Decimal as D

import pytest

from mg_factories import LONG, SHORT, T0, ZERO_COSTS, plan, px
from newcore.domain import Purpose, ReasonCode as R, make_id
from newcore.management import Leg, Stage
from newcore.management import driver as DR
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill

ACCT, LOT = make_id('acct', 51), make_id('lot', 52)
SIDES = (LONG, SHORT)
_n = itertools.count(1)


def ref(p, d):
    return OrderRef(symbol=p.symbol, client_id=d.client_id, route=d.route)


def out(p, d, kind, **kw):
    return OrderOutcome(kind=kind, ref=ref(p, d), observed_at_ms=T0 + next(_n), **kw)


def rows(p, xid, q, price):
    return (VenueFill(trade_id=f't{next(_n)}', exchange_order_id=xid, symbol=p.symbol, position_side=p.side.value,
                      qty=D(q), price=price, fee=D(0), fee_asset='USDT', realized_pnl=D(0), maker=False,
                      at_ms=T0 + next(_n)),)


def protects(drv):
    return [d for d in drv.submits if d.purpose is Purpose.PROTECT]


def live_stops(ds):
    return [b for b in ds.bindings if b.leg is Leg.STOP and b.state in (DR.BindState.SENT, DR.BindState.WORKING)]


def to_breakeven(side):
    """Entry 5, stop S0 confirmed, TP1 (2.5) filled; the break-even replacement S1 is sent (not confirmed)."""
    p = plan(side, tp1_frac='0.5', tp1_off='1', tp2_off='2', be=True, costs=ZERO_COSTS)
    drv = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0))
    s0 = protects(drv)[0]
    drv = DR.on_outcome(drv.state, out(p, s0, OutcomeKind.KNOWN, status='NEW', exchange_order_id='x-s0'),
                        submit=True)
    drv = DR.on_mark(drv.state, px(side, '101.1'))
    tp1 = drv.submits[0]
    assert tp1.leg is Leg.TP1
    drv = DR.on_outcome(drv.state, out(p, tp1, OutcomeKind.FINAL, status='FILLED', exchange_order_id='x-tp1',
                                       executed_qty=D('2.5'), avg_price=px(side, '101.1')), submit=True)
    drv = DR.on_fills(drv.state, rows(p, 'x-tp1', '2.5', px(side, '101.1')))
    s1 = protects(drv)[0]
    assert s1.qty == D('2.5') and s1.client_id != s0.client_id
    return p, drv, s0, s1


def old_stop_fills(p, ds, s0, side):
    drv = DR.on_outcome(ds, out(p, s0, OutcomeKind.FINAL, status='FILLED', exchange_order_id='x-s0',
                                executed_qty=D('2.5'), avg_price=px(side, '98.02')), submit=False)
    return DR.on_fills(drv.state, rows(p, 'x-s0', '2.5', px(side, '98.02')))


@pytest.mark.parametrize('side', SIDES)
def test_old_stop_fills_then_the_replacement_is_confirmed_and_cancelled(side):
    p, drv, s0, s1 = to_breakeven(side)
    drv = old_stop_fills(p, drv.state, s0, side)
    assert drv.state.pos.stage is Stage.DONE and drv.state.pos.qty == 0
    drv = DR.on_outcome(drv.state, out(p, s1, OutcomeKind.KNOWN, status='NEW', exchange_order_id='x-s1'),
                        submit=True)
    assert [c.client_id for c in drv.cancels] == [s1.client_id]
    assert drv.cancels[0].reason is R.LIFECYCLE_ORPHAN_CANCEL and not drv.submits
    drv = DR.on_outcome(drv.state, out(p, s1, OutcomeKind.FINAL, status='CANCELED', exchange_order_id='x-s1',
                                       executed_qty=D(0)), submit=False)
    assert not live_stops(drv.state) and not drv.cancels and not drv.submits


@pytest.mark.parametrize('side', SIDES)
def test_replacement_confirmed_old_cancel_refused_then_old_fill_cancels_the_replacement(side):
    """The T08 order on the venue: S1 confirmed -> cancel S0 -> refused (-2011, it already filled) -> S0's FINAL and
    fills arrive -> flat -> S1 cancelled."""
    p, drv, s0, s1 = to_breakeven(side)
    drv = DR.on_outcome(drv.state, out(p, s1, OutcomeKind.KNOWN, status='NEW', exchange_order_id='x-s1'),
                        submit=True)
    assert [c.client_id for c in drv.cancels] == [s0.client_id]
    drv = DR.on_outcome(drv.state, out(p, s0, OutcomeKind.REJECTED, error_code=-2011), submit=False)
    drv = old_stop_fills(p, drv.state, s0, side)
    assert drv.state.pos.stage is Stage.DONE and drv.state.pos.qty == 0
    assert [c.client_id for c in drv.cancels] == [s1.client_id]
    drv = DR.on_outcome(drv.state, out(p, s1, OutcomeKind.FINAL, status='CANCELED', exchange_order_id='x-s1',
                                       executed_qty=D(0)), submit=False)
    assert not live_stops(drv.state)


@pytest.mark.parametrize('side', SIDES)
def test_flat_cancel_is_sent_once_and_not_while_the_position_is_open(side):
    p, drv, s0, s1 = to_breakeven(side)
    drv = DR.on_outcome(drv.state, out(p, s1, OutcomeKind.KNOWN, status='NEW', exchange_order_id='x-s1'),
                        submit=True)
    assert s1.client_id not in [c.client_id for c in drv.cancels]          # open: the confirmed stop protects 2.5
    drv = old_stop_fills(p, drv.state, s0, side)
    assert [c.client_id for c in drv.cancels] == [s1.client_id]
    again = DR.on_mark(drv.state, px(side, '100'))
    assert not again.cancels                                               # CANCELLING: never a second cancel
