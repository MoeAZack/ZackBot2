"""NC-07 golden behaviours through the zb-path/1 simulator and the pure core, long and the short mirror.

Candle 0 is the entry candle (the golden pack's candle 280); the entry fills at its open with slippage (100.02 long /
99.98 short). Exact golden numbers are asserted for the long cases the pack defines; the short mirror is asserted with
the same formula (multiplicative slippage is not symmetric) and, at zero cost, as an exact price mirror."""
from decimal import Decimal as D

import pytest

from mg_factories import (GOLDEN_COSTS, LONG, SHORT, ZERO_COSTS, K, kinds, net_be, path, plan, px, rules)
from newcore.domain import ReasonCode as R
from newcore.domain.instrument import Rounding
from newcore.management import ActionKind as AK, Leg, Stage, average, realized_pnl, run
from newcore.management.plan import market_fill, sgn

SIDES = (LONG, SHORT)
FEE = D('0.0005')


def mfill(side, p, opening):
    return market_fill(D(p), side, D('0.0002'), opening=opening)


def expected_pnl(side, opens, closes, fee=FEE):
    s = sgn(side)
    o = sum(q * p for q, p in opens)
    c = sum(q * p for q, p in closes)
    return s * (c - o) - fee * (o + c)


def fills_of(res, leg=None):
    return [(f.leg, f.qty, f.price) for f in res.fills if leg is None or f.leg is leg]


# ----------------------------------------------------------------------------------------------- existing golden cases
@pytest.mark.parametrize('side', SIDES)
def test_stop_inside_red_candle_G_STOP(side):
    p = plan(side)
    res = run(p, path({2: ('100', '100.2', '97.5', '97.8')}, 4, side))
    assert res.exit_candle == 2 and res.state.stage is Stage.DONE
    exit_px = mfill(side, px(side, '98.02'), False)
    assert fills_of(res) == [(Leg.STOP, D(5), exit_px)]
    pnl = realized_pnl(p, res.state)
    assert pnl == expected_pnl(side, [(D(5), p.entry_price)], [(D(5), exit_px)])
    if side is LONG:
        assert pnl == D('-10.59307099')          # G-STOP-L-01


@pytest.mark.parametrize('side', SIDES)
def test_gap_through_stop_fills_at_the_open_G_GAP_STOP(side):
    p = plan(side)
    res = run(p, path({2: ('95', '95.2', '94.8', '95')}, 4, side))
    exit_px = mfill(side, px(side, '95'), False)
    assert fills_of(res) == [(Leg.STOP, D(5), exit_px)] and res.exit_candle == 2
    if side is LONG:
        assert realized_pnl(p, res.state) == D('-25.6825025')     # G-GAP-STOP-L-01


@pytest.mark.parametrize('side', SIDES)
def test_gap_through_target_fills_at_the_open_G_GAP_TP(side):
    p = plan(side, tp2_off='4')
    res = run(p, path({2: ('106', '106', '105.8', '105.9')}, 4, side))
    exit_px = mfill(side, px(side, '106'), False)
    assert fills_of(res) == [(Leg.TP2, D(5), exit_px)] and res.exit_candle == 2
    if side is LONG:
        assert realized_pnl(p, res.state) == D('29.279003')      # G-GAP-TP-L-01


@pytest.mark.parametrize('side', SIDES)
def test_partial_that_floors_to_zero_is_skipped_G_TP1_ZERO(side):
    """step 5: 0.5 x 5 floors to 0 -> no TP1 order, nothing marked done, no BE; the whole 5 runs to the 2R target."""
    p = plan(side, r=rules(step='5', min_qty='5'), tp1_frac='0.5', tp1_off='2', tp2_off='4', be=True)
    res = run(p, path({1: ('100', '102.02', '100', '102'), 2: ('102', '104.02', '101.9', '104')}, 5, side))
    assert not any(a.leg is Leg.TP1 for a in res.actions)
    assert not res.state.tp1_confirmed
    assert not any(a.kind is AK.REPLACE_STOP for a in res.actions)
    exit_px = mfill(side, px(side, '104.02'), False)
    assert fills_of(res) == [(Leg.TP2, D(5), exit_px)] and res.exit_candle == 2
    if side is LONG:
        assert realized_pnl(p, res.state) == D('19.38593201')    # exact; G-TP1-ZERO-L-01 prints 19.385932 (6 dp)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('n', (1, 4))
def test_time_exit_counted_in_candles_from_the_entry_candle_G_TIME(side, n):
    p = plan(side, qty='0.333', stop='70.02', time_exit=n)
    res = run(p, path({}, 8, side))
    assert res.exit_candle == n - 1                             # the close of candle i_in + N - 1
    te = [a for a in res.actions if a.kind is AK.TIME_EXIT]
    assert len(te) == 1 and te[0].qty == D('0.333') and te[0].reason is R.EXIT_TIME
    exit_px = mfill(side, px(side, '100'), False)
    assert fills_of(res) == [(Leg.CLOSE, D('0.333'), exit_px)]
    r_mult = realized_pnl(p, res.state) / (D('0.333') * 30)
    if side is LONG:
        assert abs(r_mult - D('-0.004666666667')) < D('1e-12')  # G-TIME-L-01 / -02


@pytest.mark.parametrize('side', SIDES)
def test_add_is_a_taker_fill_with_slippage_G_DCA_SLIP(side):
    """G-DCA-SLIP-L-01 with the ONE add the core allows (q0 / q1 as the golden's legacy sizing, at a 1e-6 step)."""
    p = plan(side, r=rules(step='0.000001'), qty='0.408163', stop='95.02', add='99.02', scale='1.5')
    assert p.add_qty == D('0.612244')                         # floor(1.5 x 0.408163) - never rounded up
    res = run(p, path({0: ('100', '100', '99.0199', '99.03')}, 4, side), exits={1: R.EXIT_SIGNAL})
    add_px = mfill(side, px(side, '99.02'), True)
    exit_px = mfill(side, px(side, '99.03'), False)
    q = D('1.020407')
    assert fills_of(res) == [(Leg.ADD, D('0.612244'), add_px), (Leg.CLOSE, q, exit_px)]
    assert res.exit_candle == 1
    pnl = realized_pnl(p, res.state)
    assert pnl == expected_pnl(side, [(D('0.408163'), p.entry_price), (D('0.612244'), add_px)], [(q, exit_px)])
    if side is LONG:
        assert add_px == D('99.039804')
        assert abs(pnl - D('-0.531540243')) <= D('0.0001')    # the golden's tol (legacy float sizing)


@pytest.mark.parametrize('side', SIDES)
def test_gap_through_the_add_fills_at_the_open_G_GAP_DCA(side):
    """G-GAP-DCA-L-01, one add (the core caps adds at one): the add fills at the gapped open, never at its level."""
    p = plan(side, r=rules(step='0.000001'), qty='0.408163', stop='95.02', add='99.02', scale='1.5', cap='12')
    res = run(p, path({2: ('97.6', '97.6', '97.55', '97.55')}, 4, side))
    add_px = mfill(side, px(side, '97.6'), True)
    assert fills_of(res) == [(Leg.ADD, D('0.612244'), add_px)]
    if side is LONG:
        assert add_px == D('97.61952')
    assert res.state.stop.qty == res.state.qty == D('1.020407')


# ---------------------------------------------------------------------------------- behaviours the pack still lacks
def tp1_plan(side, **kw):
    return plan(side, tp1_frac='0.5', tp1_off='2', tp2_off='4', be=True, **kw)


@pytest.mark.parametrize('side', SIDES)
def test_tp1_fill_then_break_even(side):
    p = tp1_plan(side)
    res = run(p, path({1: ('100', '102.1', '99.9', '102.05'), 2: ('102', '102.1', '99.5', '99.6')}, 5, side))
    tp1_px = mfill(side, px(side, '102.02'), False)
    be = net_be(p, p.entry_price, FEE * 5 * p.entry_price, D('2.5'))   # fee-aware: covers the entry fee + exit costs
    assert be == (D('100.2') if side is LONG else D('99.81'))            # never the raw average 100.02 / 99.98
    be_px = mfill(side, be, False)
    assert fills_of(res) == [(Leg.TP1, D('2.5'), tp1_px), (Leg.STOP, D('2.5'), be_px)]
    rep = [a for a in res.actions if a.kind is AK.REPLACE_STOP]
    assert [(a.price, a.qty, a.reason) for a in rep] == [(be, D('2.5'), R.PROTECT_REPLACE)]
    assert res.exit_candle == 2
    assert realized_pnl(p, res.state) == expected_pnl(side, [(D(5), p.entry_price)],
                                                      [(D('2.5'), tp1_px), (D('2.5'), be_px)])


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('bar', (('100', '102.5', '97.9', '98.5'), ('100', '102.5', '97.9', '102.3')))
def test_stop_and_tp1_in_one_candle_no_tp1_no_break_even(side, bar):
    """Stop-first ambiguity: the stop is inside the candle, so no target fills; -1R, never a touch-triggered BE."""
    p = tp1_plan(side)
    res = run(p, path({1: bar}, 4, side))
    assert fills_of(res) == [(Leg.STOP, D(5), mfill(side, px(side, '98.02'), False))]
    assert not res.state.tp1_confirmed and res.state.tp1_filled == 0
    assert not any(a.kind is AK.REPLACE_STOP for a in res.actions)
    assert realized_pnl(p, res.state) < -D(10)


def add_plan(side, costs=GOLDEN_COSTS):
    return plan(side, add='99.02', tp1_frac='0.5', tp1_off='1', tp2_off='2', be=True, cap='20', costs=costs)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('costs', (GOLDEN_COSTS, ZERO_COSTS))
def test_targets_re_anchor_to_the_average_after_the_add(side, costs):
    p = add_plan(side, costs)
    res = run(p, path({1: ('100', '100.1', '98.9', '99.0')}, 3, side))
    st = res.state
    assert fills_of(res)[0][:2] == (Leg.ADD, D(5))
    avg = average(st)
    up = Rounding.UP if side is LONG else Rounding.DOWN
    assert st.tp1.price == p.rules.quantize_price(avg + sgn(side) * 1, up) and st.tp1.qty == D(5)
    assert st.tp2.price == p.rules.quantize_price(avg + sgn(side) * 2, up) and st.tp2.qty == D(5)
    assert st.stop.qty == st.qty == D(10) and st.stop.price == px(side, '98.02')
    i = [k for k, a in enumerate(res.actions) if a.kind is AK.REPLACE_STOP][0]
    assert kinds(res.actions[i:i + 3]) == [('replace_stop', 'stop'), ('replace_target', 'tp1'),
                                          ('replace_target', 'tp2')]
    if costs is ZERO_COSTS:
        mirror = {LONG: (D('100.52'), D('101.52')), SHORT: (K - D('100.52'), K - D('101.52'))}[side]
        assert (st.tp1.price, st.tp2.price) == mirror


@pytest.mark.parametrize('side', SIDES)
def test_add_then_stop_in_the_same_candle(side):
    p = add_plan(side)
    res = run(p, path({1: ('100', '100.1', '97.9', '98.0')}, 3, side))
    add_px = mfill(side, px(side, '99.02'), True)
    stop_px = mfill(side, px(side, '98.02'), False)
    assert fills_of(res) == [(Leg.ADD, D(5), add_px), (Leg.STOP, D(10), stop_px)]
    assert res.exit_candle == 1
    assert realized_pnl(p, res.state) == expected_pnl(side, [(D(5), p.entry_price), (D(5), add_px)],
                                                      [(D(10), stop_px)])
    assert realized_pnl(p, res.state) >= -p.risk_cap


@pytest.mark.parametrize('side', SIDES)
def test_gap_through_add_and_stop_closes_at_the_open_with_no_add(side):
    p = add_plan(side)
    res = run(p, path({1: ('97.5', '97.6', '97.4', '97.5')}, 3, side))
    assert fills_of(res) == [(Leg.STOP, D(5), mfill(side, px(side, '97.5'), False))]
    assert res.state.add_filled == 0


@pytest.mark.parametrize('side', SIDES)
def test_zero_cost_paths_are_exact_mirrors(side):
    """At zero cost the short path is the exact price mirror of the long one: same legs, quantities, mirrored prices."""
    bars = {1: ('100', '100.1', '98.9', '99.0'), 2: ('99', '100.6', '98.95', '100.55'), 3: ('100.5', '101.6', '100.4', '101.5')}
    long_ = run(add_plan(LONG, ZERO_COSTS), path(bars, 6, LONG))
    short = run(add_plan(SHORT, ZERO_COSTS), path(bars, 6, SHORT))
    assert [(l, q, K - p) for l, q, p in fills_of(long_)] == fills_of(short)
    assert kinds(long_.actions) == kinds(short.actions)
    assert realized_pnl(add_plan(LONG, ZERO_COSTS), long_.state) == realized_pnl(add_plan(SHORT, ZERO_COSTS), short.state)
