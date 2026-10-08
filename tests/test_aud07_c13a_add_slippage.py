"""AUD-07 C13a: DCA safety orders are taker market orders and pay the entry slippage in the backtester as in the engine
(the replay's exchange charges SLIP on every open). Cowork measured the slip-free gap on the short mirror (+0.0012 R) and
with scale 2 / n = 2 and two fills (+0.0022 R). Each case runs through both models; the backtest R must equal the
engine's within 0.0003 R (the engine adds at the replay's path step, one tick past the level - far below the effect).
Pyramid adds: tests/test_bt_intrabar_path.py pins the slipped add price on paper."""
import pytest

import backtest as B
from _aud07_twin import market, run_bt, run_engine, shape

TOL = 0.0003


def _mirror(bars, side):
    if side == 1: return bars
    return {k: (200 - o, 200 - l, 200 - h, 200 - c) for k, (o, h, l, c) in bars.items()}


# one safety order: the red candle 280 ends exactly at the first level 99.02 (a replay path step lands there)
ONE = {280: (100, 100, 99.0199, 99.03)}
# two safety orders (n = 2, scale 2): 99.02 in candle 280, 98.02 in candle 281
TWO = {280: (100, 100, 99.0199, 99.03), 281: (99.03, 99.03, 98.0199, 98.03)}


@pytest.mark.parametrize('side', [1, -1])
@pytest.mark.parametrize('name,bars,dca,exit_bar', [
    ('one safety order', ONE, dict(n=3, step_atr=1, scale=1.5, tp_atr=1, stop_atr=2), 281),
    ('two safety orders, scale 2', TWO, dict(n=2, step_atr=1, scale=2, tp_atr=1, stop_atr=2), 282),
])
def test_safety_orders_pay_slippage_like_the_engine(name, bars, dca, exit_bar, side):
    raw = market({'SOLUSDT': (100, 0.5, _mirror(bars, side))})
    slot = dict(key='dca_dip', risk=0.02, max_pos=1, symbols=['SOLUSDT'], sides='long' if side == 1 else 'short',
                mgmt={'dca': dca, 'max_bars': 10 ** 6})
    sigs = [(279, 'SOLUSDT', 'le' if side == 1 else 'se'), (exit_bar, 'SOLUSDT', 'lx' if side == 1 else 'sx')]
    bt, _ = run_bt(raw, slot, sigs)
    eng, _ = run_engine(raw, slot, sigs)
    want = [('SOLUSDT', side, 280, exit_bar, 'signal')]
    assert shape(bt) == want and shape(eng) == want, (bt, eng)
    assert bt[0][5] == pytest.approx(eng[0][5], abs=TOL), f'backtest R {bt[0][5]:.5f} vs engine {eng[0][5]:.5f}'


def test_slipped_safety_order_price_on_paper():
    """Long, one safety order: avg = (q0 x 100.02 + 1.5 q0 x 99.02 x (1 + SLIP)) / 2.5 q0; signal exit at 99.03 x (1 - SLIP)."""
    raw = market({'SOLUSDT': (100, 0.5, ONE)})
    slot = dict(key='dca_dip', risk=0.02, max_pos=1, symbols=['SOLUSDT'], sides='long',
                mgmt={'dca': dict(n=3, step_atr=1, scale=1.5, tp_atr=1, stop_atr=2), 'max_bars': 10 ** 6})
    bt, _ = run_bt(raw, slot, [(279, 'SOLUSDT', 'le'), (281, 'SOLUSDT', 'lx')])
    q0 = 10.0 / (1 * 5 + 1.5 * 4 + 2.25 * 3 + 3.375 * 2)
    e0, add, xp = 100 * (1 + B.SLIP), 99.02 * (1 + B.SLIP), 99.03 * (1 - B.SLIP)
    q = 2.5 * q0; avg = (q0 * e0 + 1.5 * q0 * add) / q
    pnl = (xp - avg) * q - (q0 * e0 + 1.5 * q0 * add + q * xp) * B.FEE
    assert bt[0][5] == pytest.approx(pnl / 10.0, abs=1e-6)
