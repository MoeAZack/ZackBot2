"""ROUND-STEP-01 (#187, Codex 6385078314). feasibility.round_step - the engine's _rd - floored on-grid quantities one
step short: its absolute +1e-9 guard is below float spacing at ~1e7+ steps (254.37124 at step 1e-5 is
25437123.999999996 steps). Float sums/differences of on-grid lot quantities had the same defect one level up
(2345.61989 - 1172.80994 = 1172.8099499999998). Market exits, TP remainders and adds then left a real one-step position
that the replay exchange keeps, and the execution packet saw two engine trades as one (SUI/range, XRP/exp p3diag1).
The fix floors on DECIMAL values and never rounds up."""
import math
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tests')]
import feasibility as F                                   # noqa: E402

STEPS = [1e-8, 1e-6, 1e-5, 1e-4, 1e-3, 0.01, 0.1, 1.0, 10.0, 0.5, 0.005, 0.25, 2.5]


def _dec(step):
    return max(0, -int(math.floor(math.log10(step)))) + 2 if step < 1 else 1


def _grid(k, step):
    return round(k * step, _dec(step))


# ------------------------------------------------------------------ the real artifacts
@pytest.mark.parametrize('x', [254.37124, 196.44617, 227.87239, 188.69615, 299.27826])      # SUI/range p3diag1
def test_the_real_sui_quantities_round_to_themselves(x):
    assert F.round_step(x, 1e-5) == x


def test_the_real_xrp_tp_remainder_is_exact():                  # XRP/exp p3diag1: open, TP1 banks the floored half
    total = 2345.61989
    half = F.round_step(total / 2, 1e-5)
    assert half == 1172.80994
    assert F.step_add(total, -half, 1e-5) == 1172.80995           # float difference is 1172.8099499999998
    assert F.step_add(half, 1172.80995, 1e-5) == total


# ------------------------------------------------------------------ properties
@pytest.mark.parametrize('step', STEPS)
def test_decimal_grid_values_stay_exact_including_large_tick_counts(step):
    rnd = random.Random(187)
    top = min(10 ** 12, int(1e14 * step) if step < 1 else 10 ** 12)
    for _ in range(5000):
        x = _grid(rnd.randint(1, max(2, top)), step)
        assert F.round_step(x, step) == x, (x, step)


@pytest.mark.parametrize('step', STEPS)
def test_below_grid_values_floor_and_never_exceed_the_input(step):
    rnd = random.Random(7)
    for _ in range(5000):
        k = rnd.randint(1, 10 ** 6)
        for frac in (0.5, 0.999, 0.001):
            x = (k - frac) * step                      # a fraction of a step below the grid point k
            r = F.round_step(x, step)
            assert r <= x and r == pytest.approx((k - 1) * step, rel=1e-12, abs=step * 1e-9), (x, step)


@pytest.mark.parametrize('step', STEPS)
def test_a_float_just_below_the_grid_floors_to_the_previous_step(step):
    # representation policy: the decimal value of nextafter(grid, -inf) is below the grid, so it floors - never up
    for k in (3, 1234, 25437124):
        g = _grid(k, step)
        r = F.round_step(math.nextafter(g, -math.inf), step)
        assert r < g and r == pytest.approx(g - step, rel=1e-12)


@pytest.mark.parametrize('step', STEPS)
def test_monotone_and_idempotent(step):
    rnd = random.Random(11)
    xs = sorted(rnd.uniform(0, 10 ** 4) * step for _ in range(3000))
    rs = [F.round_step(x, step) for x in xs]
    assert rs == sorted(rs)
    assert all(F.round_step(r, step) == r for r in rs)


@pytest.mark.parametrize('step', [1e-5, 1e-3, 0.1, 1.0, 0.005])
def test_step_add_of_on_grid_quantities_is_exact(step):
    rnd = random.Random(3)
    for _ in range(20000):
        a, b = rnd.randint(0, 10 ** 9), rnd.randint(0, 10 ** 9)
        lo, hi = min(a, b), max(a, b)
        assert F.step_add(_grid(hi, step), -_grid(lo, step), step) == _grid(hi - lo, step)
        assert F.step_add(_grid(a, step), _grid(b, step), step) == _grid(a + b, step)


def test_step_add_never_rounds_a_genuinely_below_grid_result_up():
    assert F.step_add(254.37124, -0.000001, 1e-5) == 254.37123
    assert F.step_add(1.0, -0.5000001, 0.5) == 0.0


# ------------------------------------------------------------------ engine lifecycle on a 1e-5-step fake exchange
@pytest.fixture
def eng():
    import engine as E
    import test_safety as TS

    class Fine(TS.FakeX):                                  # positions held as exact decimals, as Binance does
        def open(self, s, ps, q):
            r = super().open(s, ps, q); self.pos[(s, ps)] = F.qty_sum(self.pos[(s, ps)]); return r

        def close(self, s, ps, q):
            self._f('close')
            self.pos[(s, ps)] = F.qty_sum(self.pos.get((s, ps), 0), -float(q)); return {'avgPrice': str(self.mark[s])}

        def exchange_info(self):
            return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
                {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.00001', 'minQty': '0.00001'},
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.0001'},
                {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]} for s in self.mark]}
    saved = TS.FakeX
    TS.FakeX = Fine
    try:
        e, _ = TS.mk_engine()
    finally:
        TS.FakeX = saved
    k = TS.opened(e)
    return E, e, k


def _hold(e, k, qty):
    lot = e.state['lots'][k]
    lot['qty'] = lot['qty_max'] = qty
    e.trade.pos[(lot['symbol'], lot['side'])] = qty
    return lot


def test_a_market_close_sends_the_whole_on_grid_lot(eng):
    _, e, k = eng
    lot = _hold(e, k, 254.37124)
    e.close_lot(k, 'exit_signal')
    assert abs(e.trade.pos[(lot['symbol'], lot['side'])]) < 1e-9          # no one-step remnant on the exchange
    assert k not in e.state['lots']


def test_a_tp1_remainder_then_its_stop_close_leave_nothing(eng):
    _, e, k = eng
    lot = _hold(e, k, 2345.61989)
    half = F.round_step(lot['qty'] / 2, 1e-5)
    e._market_close(lot, half, 'take_profit_1')
    assert lot['qty'] == 1172.80995 and e.trade.pos[(lot['symbol'], lot['side'])] == pytest.approx(1172.80995, abs=1e-9)
    e.close_lot(k, 'stop')
    assert abs(e.trade.pos[(lot['symbol'], lot['side'])]) < 1e-9


@pytest.mark.parametrize('step', STEPS)
def test_ceil_step_covers_the_input_and_is_idempotent_on_the_grid(step):
    rnd = random.Random(5)
    for _ in range(3000):
        x = rnd.uniform(0, 10 ** 4) * step
        c = F.ceil_step(x, step)
        assert c >= x - 1e-12 and F.ceil_step(c, step) == c and c - x < step * (1 + 1e-9)
    for k in (1, 77, 25437124):
        assert F.ceil_step(_grid(k, step), step) == _grid(k, step)


def test_replay_exchange_positions_stay_exact_after_partial_closes():
    assert F.qty_sum(2345.61989, -1172.80994) == 1172.80995
    assert F.qty_sum(1172.80995, -1172.80995) == 0.0


# ------------------------------------------------------------------ callers of "one more step" (Codex 6385148287 item 2)
# Evidence: with the exact floor, round_step(q + step, step) returns q itself for 3-21% of on-grid q (the float sum
# lands a hair below the grid), so every "add one step" caller must use the exact step_add.
@pytest.mark.parametrize('q,step', [(17.02773, 1e-5), (382.986, 0.001), (75183.73, 0.01), (0.08312022, 1e-8),
                                    (8620.64, 0.005)])
def test_one_more_step_always_advances_by_exactly_one_step(q, step):
    assert F.step_add(q, step, step) == _grid(round(q / step) + 1, step)


def test_short_tp_price_is_on_the_grid_at_or_above_its_level():
    import engine as E
    from types import SimpleNamespace
    me = SimpleNamespace(rules={'X': dict(tick=0.01)}, _rd=E.Engine._rd)
    lot = dict(symbol='X', side='SHORT', e0=75183.735 + 2.0, R=1.0, mgmt=dict(tp_r=2.0))   # level 75183.735
    px = E.Engine._tp_price(me, lot)
    assert px >= 75183.735 and px == 75183.74
