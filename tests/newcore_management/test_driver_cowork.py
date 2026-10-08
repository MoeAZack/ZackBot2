"""Repros of Cowork's attack on the ManagementDriver at ad0406a (posted on #38: mdrv/a1..a6, fz), one per finding, each
pinned to its fix in newcore/management/driver.py (HIGH 1-3, MED 4-7, LOW 8-10, INFO 11)."""
import itertools
from decimal import Decimal as D

import pytest

from mg_factories import LONG, SHORT, T0, ZERO_COSTS, candle, plan, px
from newcore.domain import DomainError, EntriesMode, HoldKind, OrderType, Purpose, ReasonCode as R, make_id
from newcore.management import ActionKind as AK, Leg, ManagementAction, Stage
from newcore.management import driver as DR
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill

ACCT, LOT = make_id('acct', 21), make_id('lot', 22)
SIDES = (LONG, SHORT)
_n = itertools.count(1)


def boot(side, **kw):
    kw.setdefault('costs', ZERO_COSTS)
    p = plan(side, **kw)
    drv = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0))
    return p, drv


def ref(p, cid):
    return OrderRef(symbol=p.symbol, client_id=cid)


def known(p, ds, d, xid):
    return DR.on_outcome(ds, OrderOutcome(kind=OutcomeKind.KNOWN, ref=ref(p, d.client_id), observed_at_ms=T0,
                                          status='NEW', exchange_order_id=xid), submit=True)


def final(p, ds, cid, xid, status, executed, price=None, submit=False):
    return DR.on_outcome(ds, OrderOutcome(kind=OutcomeKind.FINAL, ref=ref(p, cid), observed_at_ms=T0, status=status,
                                          exchange_order_id=xid, executed_qty=D(executed),
                                          avg_price=None if D(executed) == 0 else D(price)), submit=submit)


def vfill(p, xid, qty, price, fee='0', asset='USDT'):
    return VenueFill(trade_id=f't{next(_n)}', exchange_order_id=xid, symbol=p.symbol, position_side=p.side.value,
                     qty=D(qty), price=D(price), fee=D(fee), fee_asset=asset, realized_pnl=D(0), maker=False,
                     at_ms=T0)


def stops(drv):
    return [d for d in drv.submits if d.purpose is Purpose.PROTECT]


# ------------------------------------------------------------------------------------------------------- HIGH
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('status', ('CANCELED', 'EXPIRED'))
def test_high1_a_stop_ended_outside_the_bot_is_re_placed_at_once(side, status):
    p, drv = boot(side)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    assert DR.protected(ds)
    r = final(p, ds, s0.client_id, 'x1', status, '0')
    again = stops(r)
    assert len(again) == 1 and again[0].reason is R.PROTECT_RESTORING
    assert (again[0].qty, again[0].stop_price) == (p.entry_qty, px(side, '98.02'))
    assert again[0].intent_id != s0.intent_id
    assert any(x[0] == 'stop_lost' for x in r.reconcile) and any(x[0] == 'stop_ended_outside' for x in r.reconcile)
    assert DR.protected(known(p, r.state, again[0], 'x2').state)


@pytest.mark.parametrize('side', SIDES)
def test_high2_a_partly_filled_stop_re_protects_the_remainder(side):
    p, drv = boot(side)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    ds = final(p, ds, s0.client_id, 'x1', 'EXPIRED', '3', px(side, '98.02')).state
    r = DR.on_fills(ds, (vfill(p, 'x1', '3', px(side, '98.02')),))
    assert r.state.pos.qty == D(2)
    again = stops(r)
    assert len(again) == 1 and again[0].qty == D(2) and again[0].reason is R.PROTECT_RESTORING


@pytest.mark.parametrize('side', SIDES)
def test_high3_a_late_stop_refusal_after_flat_never_reaches_the_core_and_the_fold_is_total(side):
    p, drv = boot(side)
    s0 = stops(drv)[0]                                    # never confirmed
    log = []
    r = DR.on_candle(drv.state, candle(0, '100', '100.01', '99.99', '100', side), close_request=R.EXIT_SIGNAL)
    log.append(('candle', candle(0, '100', '100.01', '99.99', '100', side), R.EXIT_SIGNAL, None))
    close = next(d for d in r.submits if d.purpose is Purpose.CLOSE)
    o = OrderOutcome(kind=OutcomeKind.FINAL, ref=ref(p, close.client_id), observed_at_ms=T0, status='FILLED',
                     exchange_order_id='x9', executed_qty=p.entry_qty, avg_price=px(side, '100'))
    r = DR.on_outcome(r.state, o, submit=True)
    log.append(('outcome', o, True))
    f = (vfill(p, 'x9', p.entry_qty, px(side, '100')),)
    r = DR.on_fills(r.state, f)
    log.append(('fills', f))
    assert r.state.pos.stage is Stage.DONE
    late = OrderOutcome(kind=OutcomeKind.REJECTED, ref=ref(p, s0.client_id), observed_at_ms=T0, error_code=-2021)
    r2 = DR.on_outcome(r.state, late, submit=True)                 # no InvalidRecord from the core
    assert r2.state.pos.qty == 0 and r2.submits == () and r2.state.pos.stage is Stage.DONE
    log.append(('outcome', late, True))
    replay = DR.fold(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0), events=log)
    assert replay[-1].state == r2.state                            # every restart folds the same journal


def test_high3_any_input_the_core_refuses_is_reported_not_raised():
    p, drv = boot(LONG)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    # a FINAL CANCELED for an order the core cancelled already, then a stale REJECTED: both are no-ops
    r = DR.on_outcome(ds, OrderOutcome(kind=OutcomeKind.REJECTED, ref=ref(p, s0.client_id), observed_at_ms=T0,
                                       error_code=-1), submit=False)
    assert r.state == ds


# ------------------------------------------------------------------------------------------------------- MED
@pytest.mark.parametrize('side', SIDES)
def test_med4_a_held_close_on_a_lot_that_went_flat_is_dropped_on_release(side):
    p, drv = boot(side, time_exit=1)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    ds = DR.set_mode(ds, EntriesMode.HOLD, HoldKind.NORMAL).state
    r = DR.on_candle(ds, candle(0, '100', '100.01', '99.99', '100', side))       # time exit: management, held
    assert r.submits == () and r.state.held
    ds = final(p, r.state, s0.client_id, 'x1', 'FILLED', p.entry_qty, px(side, '98.02')).state
    ds = DR.on_fills(ds, (vfill(p, 'x1', p.entry_qty, px(side, '98.02')),)).state
    assert ds.pos.stage is Stage.DONE
    r = DR.set_mode(ds, EntriesMode.ACTIVE)
    assert not any(d.purpose in (Purpose.CLOSE, Purpose.REDUCE) for d in r.submits)
    assert any(x[0] == 'held_dropped' for x in r.reconcile)


@pytest.mark.parametrize('side', SIDES)
def test_med5_a_fee_in_another_asset_is_converted_or_estimated_never_booked_one_to_one(side):
    p, drv = boot(side)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    ds = final(p, ds, s0.client_id, 'x1', 'FILLED', p.entry_qty, px(side, '98.02')).state
    f = vfill(p, 'x1', p.entry_qty, px(side, '98.02'), fee='0.001', asset='BNB')
    r = DR.on_fills(ds, (f,))
    booked = r.state.pos.fills[-1].fee
    assert booked != D('0.001') and booked == p.costs.taker_fee * f.qty * f.price
    assert ('fee_asset_estimated', f.trade_id, 'BNB') in r.reconcile
    p2, drv2 = boot(side)
    ds2 = DR.start(p2, account_id=ACCT, lot_id=LOT, entry_fee=D(0), fee_rates=(('BNB', D(600)),)).state
    ds2 = known(p2, ds2, stops(drv2)[0], 'x1').state
    ds2 = final(p2, ds2, stops(drv2)[0].client_id, 'x1', 'FILLED', p2.entry_qty, px(side, '98.02')).state
    r2 = DR.on_fills(ds2, (f,))
    assert r2.state.pos.fills[-1].fee == D('0.6')


@pytest.mark.parametrize('side', SIDES)
def test_med6_a_close_never_races_an_in_flight_target(side):
    p, drv = boot(side, tp1_frac='0.5', tp1_off='1', tp2_off='2')
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    tp_px = ds.pos.tp1.price
    r = DR.on_mark(ds, tp_px)                                              # TP1 fires: a market REDUCE of 2.5 in flight
    tp = next(d for d in r.submits if d.purpose is Purpose.REDUCE)
    r = DR.on_candle(r.state, candle(0, '100', '100.01', '99.99', '100', side), close_request=R.EXIT_SIGNAL)
    assert not any(d.purpose is Purpose.CLOSE for d in r.submits) and r.state.waiting   # waits for the target
    ds = final(p, r.state, tp.client_id, 'x7', 'FILLED', tp.qty, tp_px, submit=True).state
    r = DR.on_fills(ds, (vfill(p, 'x7', tp.qty, tp_px),))
    closes = [d for d in r.submits if d.purpose in (Purpose.CLOSE, Purpose.REDUCE)]
    assert len(closes) == 1 and closes[0].qty == p.entry_qty - tp.qty     # sized to what is left


@pytest.mark.parametrize('side', SIDES)
def test_med7_hold_lets_the_stop_cancel_through_once_flat(side):
    p, drv = boot(side)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    ds = DR.set_mode(ds, EntriesMode.HOLD, HoldKind.NORMAL).state
    r = DR.on_candle(ds, candle(0, '100', '100.01', '99.99', '100', side), close_request=R.EXIT_STOP_FAILED)
    close = next(d for d in r.submits if d.purpose is Purpose.CLOSE)       # a risk-reducing close passes HOLD
    ds = final(p, r.state, close.client_id, 'x8', 'FILLED', p.entry_qty, px(side, '100'), submit=True).state
    r = DR.on_fills(ds, (vfill(p, 'x8', p.entry_qty, px(side, '100')),))
    assert r.state.pos.stage is Stage.DONE
    assert [c.intent_id for c in r.cancels] == [s0.intent_id] and not r.state.held


# ------------------------------------------------------------------------------------------------------- LOW / INFO
def test_low8_basket_and_ladder_exits_are_management():
    assert {R.EXIT_BASKET_TP, R.EXIT_LADDER, R.EXIT_BASKET_TP_PART} <= DR.MANAGE_REASONS


@pytest.mark.parametrize('side', SIDES)
def test_low9_an_unbooked_execution_is_reported_and_explained(side):
    p, drv = boot(side)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    r = final(p, ds, s0.client_id, 'x1', 'FILLED', '2', px(side, '98.02'))
    assert ('fill_gap', s0.intent_id, D(2)) in r.reconcile
    rep = DR.lot_vs_venue(r.state, D(3))
    assert rep['diff'] == D(-2) and rep['unbooked'] == D(-2) and rep['explained']


@pytest.mark.parametrize('lineage', [((make_id('lot', 99), 'protect', 0),), ((LOT, 'protect', -1),),
                                     ((LOT, 'protect', 1), (LOT, 'protect', 2)), ((LOT, 'bogus', 0),),
                                     ((LOT, 'protect', True),)])
def test_low10_a_seeded_lineage_is_validated(lineage):
    p = plan(LONG)
    with pytest.raises(DomainError):
        DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0), lineage=lineage)


def test_low10_a_valid_lineage_continues_the_journal_ordinals():
    from newcore.ports.keys import derive_child_intent_id
    p = plan(LONG)
    drv = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0), lineage=((LOT, 'protect', 3),))
    assert stops(drv)[0].intent_id == derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 3)


@pytest.mark.parametrize('side', SIDES)
def test_info11_a_stop_already_bound_at_that_price_and_qty_is_never_submitted_twice(side):
    p, drv = boot(side)
    s0 = stops(drv)[0]
    ds = known(p, drv.state, s0, 'x1').state
    w = DR._W(ds)
    DR._apply_actions(w, (ManagementAction(kind=AK.REPLACE_STOP, leg=Leg.STOP, qty=s0.qty, price=s0.stop_price,
                                           reason=R.PROTECT_REPLACE),))
    assert w.submits == [] and [b.current for b in w.bindings() if b.leg is Leg.STOP] == [True]
    _ = OrderType
