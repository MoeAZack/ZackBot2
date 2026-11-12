"""C-BRKPROT-v1 ('prot' management key, default OFF): nothing moves before +at_r R; then the stop is the tighter of the
fee-adjusted breakeven and the ATR trail from the best price; both sides mirror; one shared rule for backtest and engine."""
import numpy as np
import pandas as pd
import pytest

import app
import backtest as BT
import strategies as S
import strategy_shadow as SSH

P = dict(at_r=1.0, trail_atr=2.0)


@pytest.mark.parametrize('side', [1, -1])
def test_nothing_before_activation(side):
    e0, R, atr = 100.0, 4.0, 2.0
    best = e0 + side * 0.999 * R                             # just short of +1R: no breakeven and no trail
    assert S.prot_level(P, side, e0, e0, R, best, atr) is None


@pytest.mark.parametrize('side', [1, -1])
def test_fee_adjusted_breakeven_at_activation(side):
    e0, R, atr = 100.0, 4.0, 2.0
    best = e0 + side * R                                     # exactly +1R; trail = best - side*4 = e0 -> BE (fees) is tighter
    lvl = S.prot_level(P, side, e0, e0, R, best, atr)
    assert lvl == pytest.approx(e0 * (1 + side * S.PROT_BUF))
    assert side * (lvl - e0) > 0                             # beyond entry on the winning side: fees are covered


@pytest.mark.parametrize('side', [1, -1])
def test_trail_takes_over_when_tighter(side):
    e0, R, atr = 100.0, 4.0, 2.0
    best = e0 + side * 3 * R                                 # +3R: trail = best - side*4 is far past breakeven
    assert S.prot_level(P, side, e0, e0, R, best, atr) == pytest.approx(best - side * 2.0 * atr)


def test_buf_override_and_default_off():
    assert S.prot_level(None, 1, 100, 100, 4, 120, 2) is None
    assert S.prot_level({}, 1, 100, 100, 4, 120, 2) is None
    assert S.prot_level(dict(at_r=1.0, buf=0.0), 1, 100, 100, 4, 104, None) == pytest.approx(100.0)
    assert S.PROT_BUF == pytest.approx(2 * BT.FEE + BT.SLIP)  # backtest costs and the shared default stay pinned


def test_settings_accept_prot_and_shadow_flags_it():
    assert app.clean_mgmt({'prot': {'at_r': 1, 'trail_atr': 2}}) == {'prot': {'at_r': 1.0, 'trail_atr': 2.0}}
    with pytest.raises(ValueError):
        app.clean_mgmt({'prot': {'at_r': 0}})
    assert 'prot' in SSH.UNSUPPORTED                          # observer v1 does not model it: INCOMPLETE, never wrong


def _raw(n=900, seed=7):
    rng = np.random.default_rng(seed)
    t = pd.date_range('2024-01-01', periods=n, freq='4h')
    out = {}
    for j, s in enumerate(('BTCUSDT', 'ETHUSDT', 'SOLUSDT')):
        c = 100 * (1 + j) * np.exp(np.cumsum(rng.normal(0, .02, n)))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, .004, n))
        w = np.abs(rng.normal(0, .012, n))
        out[s] = pd.DataFrame({'t': t, 'o': o, 'h': np.maximum(o, c) * (1 + w), 'l': np.minimum(o, c) * (1 - w),
                               'c': c, 'v': 1e3, 'qv': 1e6})
    return out


def _run(mgmt, pess='path'):
    cfg = [dict(key='exp_breakout', share=1.0, risk=0.02, max_pos=3, sides='both', mgmt=mgmt,
                symbols=['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])]
    tr, cv = BT.run(BT.Book(_raw()), cfg, start=1000.0, max_lev=20.0, daily_halt=1.0, pessimistic=pess)
    return tr, cv


@pytest.mark.parametrize('pess', ['path', 'worst'])
def test_unreachable_activation_is_byte_identical_to_off(pess):
    """Mutation guard: if the +at_r gate were dropped, the 0.5 ATR trail would fire and change the run."""
    tr0, cv0 = _run({}, pess)
    tr1, cv1 = _run({'prot': dict(at_r=1e6, trail_atr=0.5)}, pess)
    pd.testing.assert_frame_equal(tr0, tr1)
    pd.testing.assert_series_equal(cv0, cv1)


def test_protection_changes_exits_on_both_sides_and_never_widens_risk():
    tr0, _ = _run({})
    tr1, _ = _run({'prot': dict(P)})
    assert len(tr0) and len(tr1)
    assert not tr0.equals(tr1)                               # it does something on this tape
    assert set(tr1['side']) == {1, -1}                       # both longs and shorts traded under the rule


# ---------------------------------------------------------------- live engine path (mock exchange from test_safety)
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_engine_moves_nothing_before_1R_then_fee_breakeven_then_trail(side):
    import test_safety as TS
    e, _ = TS.mk_engine()
    sl = dict(TS.SL, sides='both', mgmt={'stop_atr': 2.0, 'prot': dict(P)}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', side, TS.SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    sd, e0, R, stop0 = (1 if side == 'LONG' else -1), lot['e0'], lot['R'], lot['stop']
    e.trade.mark['BTCUSDT'] = e0 + sd * 0.9 * R; e.manage(e.trade.marks())
    assert lot['stop'] == stop0                                  # +0.9R: nothing moves, not even the trail
    e.trade.mark['BTCUSDT'] = e0 + sd * 1.0 * R; e.manage(e.trade.marks())
    be = lot['avg'] * (1 + sd * S.PROT_BUF)
    assert sd * (lot['stop'] - lot['avg']) > 0 and sd * (lot['stop'] - be) >= -1e-6 * e0   # at least fee-adjusted breakeven
    e.trade.mark['BTCUSDT'] = e0 + sd * 4.0 * R; e.manage(e.trade.marks())
    assert lot['stop'] == pytest.approx(lot['best'] - sd * 2.0 * lot['atr_now'], rel=1e-3)   # trail took over
