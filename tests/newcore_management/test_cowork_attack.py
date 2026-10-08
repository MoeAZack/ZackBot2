"""Repros of Cowork's attack on nc-management fbd618b (PR #38 comment) and the Codex rulings that fixed them.

Each test failed on fbd618b / 5e17446 and passes after the fix:
M1 add cap re-checked on the ACTUAL add fill (adverse price + costs); M2 a terminal STOP / CLOSE dominates a racing ADD;
M3 fill ids, dedupe and only outstanding authorised legs fill; L4 venue-feasible targets; L5 canonical zero PnL;
L6 a retained target is re-anchored to the profitable side after an add; R7 break-even arms only on a COMPLETE TP1;
R8 break-even is fee-aware; R9 per-exit-leg R / PnL."""
import itertools
from decimal import Decimal as D

import pytest

from mg_factories import GOLDEN_COSTS, LONG, SHORT, ZERO_COSTS, plan, px, rules
from newcore.domain import DomainError, ReasonCode
from newcore.domain.base import field_spec
from newcore.management import ActionKind as AK, ConfirmedFill, Leg, Rejected, Stage, initial_state, realized_pnl, step
from newcore.management.plan import market_fill, sgn

SIDES = (LONG, SHORT)
_IDS = itertools.count()
HAS_ID = 'fill_id' in {n for n, _, _ in field_spec(ConfirmedFill)}


def F(leg, qty, price, fee='0', fid=None):
    kw = dict(leg=leg, qty=D(qty), price=D(price), fee=D(fee))
    if HAS_ID:
        kw['fill_id'] = fid or f't{next(_IDS)}'
    return ConfirmedFill(**kw)


def start(p, fee='0'):
    return step(p, initial_state(p, D(fee)))


def risk_now(p, st):
    """Loss if the current stop fills now: the position not being closed, adverse exit slippage + exit fee, plus every
    booked opening fee (entry + add)."""
    q = st.qty - st.closing
    if q == 0 or st.stop is None:
        return D(0)
    s = sgn(p.side)
    ex = market_fill(st.stop.price, p.side, p.costs.slip, opening=False)
    avg = st.basket_cost / st.basket_qty
    open_fees = getattr(st, 'open_fees', st.fees)
    return q * (s * (avg - ex) + p.costs.taker_fee * ex) + open_fees


# ------------------------------------------------------------------------------------------------------------- M1
def pyr_plan(side, costs=GOLDEN_COSTS):
    return plan(side, add='101.02', cap='27', costs=costs, tp2_off='5')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fill_px', ('103.02', '104.02'))
def test_m1_pyramid_add_filled_worse_than_planned_is_cut_back_under_the_cap(side, fill_px):
    p = pyr_plan(side)
    assert p.add_is_pyramid and p.risk_cap == D(27)
    st0 = start(p, fee=str(GOLDEN_COSTS.taker_fee * 5 * p.entry_price))
    a = px(side, fill_px)
    r = step(p, st0.state, (F(Leg.ADD, '5', a, fee=str(GOLDEN_COSTS.taker_fee * 5 * a)),))
    assert any(x.kind is AK.REDUCE for x in r.actions)
    assert risk_now(p, r.state) <= p.risk_cap


@pytest.mark.parametrize('side', SIDES)
def test_m1_dca_add_is_safe_by_geometry(side):
    p = plan(side, add='99.02', cap='20', tp2_off='2')
    st0 = start(p)
    a = px(side, '98.5')                                      # an even better (adverse-side) DCA fill
    r = step(p, st0.state, (F(Leg.ADD, '5', a),))
    assert not any(x.kind is AK.REDUCE for x in r.actions)
    assert risk_now(p, r.state) <= p.risk_cap


# ------------------------------------------------------------------------------------------------------------- M2
def full_plan(side, costs=ZERO_COSTS):
    return plan(side, add='99.02', tp1_frac='0.5', tp1_off='1', tp2_off='2', be=True, cap='20', costs=costs)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('order', ('stop_first', 'add_first'))
def test_m2_terminal_stop_dominates_a_racing_add(side, order):
    p = full_plan(side)
    st = start(p).state
    stop, add = F(Leg.STOP, '5', st.stop.price), F(Leg.ADD, '5', px(side, '99.02'))
    batch = (stop, add) if order == 'stop_first' else (add, stop)
    r = step(p, st, batch)
    assert r.state.stage is not Stage.ACTIVE
    assert not any(x.kind in (AK.PLACE_STOP, AK.REPLACE_STOP, AK.PLACE_TARGET, AK.REPLACE_TARGET, AK.PLACE_ADD)
                   for x in r.actions)
    closes = [x for x in r.actions if x.kind in (AK.CLOSE, AK.REDUCE)]
    assert sum(x.qty for x in closes) == r.state.qty == D(5)


@pytest.mark.parametrize('side', SIDES)
def test_m2_late_add_after_a_break_even_stop_fill_never_loosens_the_stop(side):
    p = full_plan(side)
    st = start(p).state
    st = step(p, st, (F(Leg.TP1, '2.5', st.tp1.price),)).state            # TP1 complete: BE, add cancelled
    be = st.stop.price
    assert be == px(side, '100.02')
    r = step(p, st, (F(Leg.STOP, '2.5', be), F(Leg.ADD, '5', px(side, '99.02'))))
    assert r.state.stage is not Stage.ACTIVE
    for x in r.actions:
        if x.kind in (AK.PLACE_STOP, AK.REPLACE_STOP):
            assert sgn(side) * (x.price - be) >= 0                         # never looser than the last stop
    r2 = step(p, r.state, (F(Leg.CLOSE, '5', px(side, '99')),))
    assert r2.state.stage is Stage.DONE


# ------------------------------------------------------------------------------------------------------------- M3
@pytest.mark.skipif(not HAS_ID, reason='fbd618b has no fill id: duplicates cannot even be told apart')
@pytest.mark.parametrize('side', SIDES)
def test_m3_duplicate_fill_id_is_idempotent_and_a_conflicting_repeat_is_refused(side):
    p = full_plan(side)
    st = start(p).state
    f = F(Leg.TP1, '2.5', st.tp1.price, fid='venue-1')
    once = step(p, st, (f,))
    twice = step(p, once.state, (f,))
    assert twice.state.qty == once.state.qty == D('2.5') and twice.actions == ()
    with pytest.raises(DomainError):
        step(p, once.state, (F(Leg.TP1, '1', st.tp1.price, fid='venue-1'),))


@pytest.mark.parametrize('side', SIDES)
def test_m3_fills_on_unrequested_legs_are_refused(side):
    p = full_plan(side)
    s0 = initial_state(p, D(0))
    with pytest.raises(DomainError):
        step(p, s0, (F(Leg.TP1, '2.5', px(side, '101.02')),))             # stage NEW: nothing requested
    st = start(p).state
    refused = step(p, st, (Rejected(leg=Leg.TP1),)).state
    with pytest.raises(DomainError):
        step(p, refused, (F(Leg.TP1, '2.5', px(side, '101.02')),))        # after a refused TP1
    with pytest.raises(DomainError):
        step(p, st, (F(Leg.CLOSE, '1', px(side, '100')),))                # no close requested
    full = step(p, st, (F(Leg.TP1, '2.5', st.tp1.price),)).state
    with pytest.raises(DomainError):
        step(p, full, (F(Leg.TP1, '1', st.tp1.price),))                   # a retired (complete) leg


@pytest.mark.skipif(not HAS_ID, reason='Cancelled(leg) arrives with the fill-id fix')
@pytest.mark.parametrize('side', SIDES)
def test_m3_racing_fill_only_while_the_cancel_is_unconfirmed(side):
    from newcore.management import Cancelled
    p = full_plan(side)
    st = start(p).state
    st = step(p, st, (F(Leg.TP1, '2.5', st.tp1.price),)).state            # emits CANCEL_ADD
    ok = step(p, st, (F(Leg.ADD, '1', px(side, '99.02')),))               # racing: accepted and flattened
    assert any(x.kind is AK.REDUCE for x in ok.actions)
    gone = step(p, st, (Cancelled(leg=Leg.ADD),)).state
    with pytest.raises(DomainError):
        step(p, gone, (F(Leg.ADD, '1', px(side, '99.02')),))


# ------------------------------------------------------------------------------------------------------------- L4
@pytest.mark.parametrize('side', SIDES)
def test_l4_every_target_and_the_remainder_are_venue_feasible(side):
    p = plan(side, r=rules(step='0.001', min_notional='5'), qty='0.08', tp1_frac='0.5', tp1_off='1', tp2_off='2',
             cap='12')
    st = start(p).state
    for o in (st.tp1, st.tp2):
        if o is not None and o.qty < st.qty:
            assert o.qty * o.price >= p.rules.min_notional and o.qty >= p.rules.min_qty
    assert st.tp1 is None and st.tp2.qty == D('0.08')                     # the partial is skipped: one single exit


# ------------------------------------------------------------------------------------------------------------- L5
@pytest.mark.parametrize('side', SIDES)
def test_l5_zero_pnl_is_canonical(side):
    p = plan(side, costs=ZERO_COSTS, cap='12')
    st = start(p).state
    r = step(p, st, (), None, close_request=ReasonCode.EXIT_SIGNAL)
    st = step(p, r.state, (F(Leg.CLOSE, '5', p.entry_price),)).state
    z = realized_pnl(p, st)
    assert z == 0 and not z.is_signed() and str(z) == '0'


# ------------------------------------------------------------------------------------------------------------- L6
@pytest.mark.parametrize('side', SIDES)
def test_l6_retained_tp1_is_on_the_profitable_side_after_a_same_batch_add(side):
    p = plan(side, stop='99.02', add='100.52', tp1_frac='0.5', tp1_off='0.3', tp2_off='2', cap='20', costs=ZERO_COSTS)
    st = start(p).state
    assert p.add_is_pyramid
    r = step(p, st, (F(Leg.TP1, '1', st.tp1.price), F(Leg.ADD, '5', px(side, '101'))))
    avg = r.state.basket_cost / r.state.basket_qty
    for o in (r.state.tp1, r.state.tp2):
        if o is not None:
            assert sgn(side) * (o.price - avg) > 0


# ---------------------------------------------------------------------------------------------- Codex rulings 7-9
@pytest.mark.parametrize('side', SIDES)
def test_r7_break_even_only_when_the_tp1_leg_is_complete(side):
    p = full_plan(side)
    st = start(p).state
    part = step(p, st, (F(Leg.TP1, '0.001', st.tp1.price),)).state       # dust: no break-even
    assert part.stop.price == px(side, '98.02')
    done = step(p, part, (F(Leg.TP1, '2.499', st.tp1.price),)).state
    assert done.stop.price == px(side, '100.02')


@pytest.mark.parametrize('side', SIDES)
def test_r8_break_even_is_net_of_fees_and_slippage(side):
    p = full_plan(side, costs=GOLDEN_COSTS)
    fee0 = GOLDEN_COSTS.taker_fee * 5 * p.entry_price
    st = start(p, fee=str(fee0)).state
    tp = st.tp1.price
    st = step(p, st, (F(Leg.TP1, '2.5', tp, fee=str(GOLDEN_COSTS.taker_fee * D('2.5') * tp)),)).state
    be = st.stop.price
    q = D('2.5')
    ex = market_fill(be, side, GOLDEN_COSTS.slip, opening=False)
    net_of_remainder = sgn(side) * q * (ex - p.entry_price) - GOLDEN_COSTS.taker_fee * q * ex - fee0
    assert net_of_remainder >= 0                                         # covers entry fees + exit fee + slippage
    assert sgn(side) * (be - p.entry_price) > 0                          # strictly beyond the raw average


@pytest.mark.parametrize('side', SIDES)
def test_r9_exit_ledger_reports_r_and_pnl_per_exit_leg_and_running(side):
    from newcore.management import exit_ledger
    p = full_plan(side, costs=GOLDEN_COSTS)
    st = start(p, fee=str(GOLDEN_COSTS.taker_fee * 5 * p.entry_price)).state
    st = step(p, st, (F(Leg.TP1, '2.5', st.tp1.price, fee='0.13'),)).state
    led = exit_ledger(p, st)
    assert [x.leg for x in led.legs] == [Leg.TP1] and led.legs[0].net > 0
    assert led.realized_r == led.realized_net / p.risk_cap
    st = step(p, st, (F(Leg.STOP, '2.5', st.stop.price, fee='0.12'),)).state
    led = exit_ledger(p, st)
    assert [x.leg for x in led.legs] == [Leg.TP1, Leg.STOP]
    assert led.realized_net == realized_pnl(p, st) and led.legs[-1].running_net == led.realized_net
