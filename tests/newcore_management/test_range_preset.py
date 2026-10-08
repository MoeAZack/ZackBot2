"""RANGE-BB-MR.v1 (M4) as the S2 mechanics fixture: disabled by default, mechanics only, 4h, long and short."""
from decimal import Decimal as D

import pytest

from mg_factories import GOLDEN_COSTS, LONG, SHORT, T0, path, px, rules
from newcore.management import Leg, Stage, admit_entry, average, planned_risk, range_bb_mr_v1, realized_pnl, run
from newcore.domain.instrument import Rounding
from newcore.management.plan import market_fill
from newcore.management.presets import CANDLE_SECONDS, TIME_EXIT_CANDLES

SIDES = (LONG, SHORT)


def preset(side, qty='3', cap='12', atr0='1'):
    return range_bb_mr_v1(rules=rules(), side=side, entry_price=px(side, '100.02'), entry_qty=D(qty),
                          entry_candle_open_ms=T0, atr0=D(atr0), risk_cap=D(cap), costs=GOLDEN_COSTS)


@pytest.mark.parametrize('side', SIDES)
def test_preset_levels_and_flags(side):
    b = preset(side)
    p = b.plan
    assert b.notes == () and p.disabled_by_default and p.mechanics_only
    assert p.candle_seconds == CANDLE_SECONDS == 14400 and p.time_exit_candles == TIME_EXIT_CANDLES == 12
    assert p.stop_price == px(side, '98.02') and p.add_price == px(side, '99.02') and p.add_qty == D(3)
    assert (p.tp1_frac, p.tp1_offset, p.tp2_offset, p.be_after_tp1) == (D('0.5'), D(1), D(2), True)
    assert planned_risk(p) <= p.risk_cap


@pytest.mark.parametrize('side', SIDES)
def test_preset_drops_an_add_that_breaks_the_cap(side):
    b = preset(side, qty='3', cap='7')
    assert b.plan.add_price is None and b.notes[0].value == 'add_over_cap'


@pytest.mark.parametrize('side', SIDES)
def test_preset_full_mechanics_add_tp1_be_tp2(side):
    p = preset(side).plan
    bars = {1: ('100', '100.1', '98.9', '99.0'),                 # the add fills at 99.02
            2: ('99', '100.7', '98.95', '100.6'),                # green: TP1 at the re-anchored avg + 1 ATR, then BE
            3: ('100.6', '101.7', '100.5', '101.6')}             # TP2 at avg + 2 ATR
    res = run(p, path(bars, 6, side))
    legs = [f.leg for f in res.fills]
    assert legs == [Leg.ADD, Leg.TP1, Leg.TP2] and res.exit_candle == 3 and res.state.stage is Stage.DONE
    assert [f.qty for f in res.fills] == [D(3), D(3), D(3)]
    assert realized_pnl(p, res.state) > 0


@pytest.mark.parametrize('side', SIDES)
def test_preset_time_exit_at_the_12th_candle(side):
    p = preset(side).plan
    res = run(p, path({}, 20, side))
    assert res.exit_candle == 11 and [f.leg for f in res.fills] == [Leg.CLOSE]


@pytest.mark.parametrize('side', SIDES)
def test_preset_after_add_be_is_the_basket_average(side):
    p = preset(side).plan
    bars = {1: ('100', '100.1', '98.9', '99.0'), 2: ('99', '100.7', '98.95', '100.6'), 3: ('100.6', '100.65', '99.4', '99.5')}
    res = run(p, path(bars, 6, side))
    assert [f.leg for f in res.fills] == [Leg.ADD, Leg.TP1, Leg.STOP]
    be = p.rules.quantize_price(average(res.state), Rounding.UP if side is LONG else Rounding.DOWN)
    assert be == px(side, '99.53') if side is LONG else be == D('100.46')     # avg 99.529902 / 100.469902
    assert res.fills[-1].qty == D(3)
    assert res.fills[-1].price == market_fill(be, side, GOLDEN_COSTS.slip, opening=False)


def test_cost_veto_refuses_1h_like_stops_and_admits_4h_like_ones():
    e = D('100')
    # 1h: ATR0 0.3% -> 2-ATR stop 0.6 away: (0.001 + 0.0004) x 100 / 0.6 = 0.233R > 0.15R
    assert not admit_entry(side=LONG, entry_price=e, stop_price=e - D('0.6'), costs=GOLDEN_COSTS).admitted
    # 4h median ATR0 1.76% -> 3.52 away: 0.04R
    a = admit_entry(side=LONG, entry_price=e, stop_price=e - D('3.52'), costs=GOLDEN_COSTS)
    assert a.admitted and a.cost_r < D('0.05')
