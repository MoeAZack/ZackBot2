"""Independent regression and point-in-time controls for BTC convexity."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v114 as M

PLANTED_MUTANTS = [
    ('include_signal_day_in_fit', 'coin_ret.shift(), btc_ret.shift()', 'coin_ret, btc_ret',
     'test_prior_fit_matches_linear_residuals'),
    ('omit_linear_control', '(yz - xy * xz / vx) / denominator', 'yz / denominator',
     'test_prior_fit_matches_linear_residuals'),
    ('one_sided_btc_shock', 'absolute = x.abs()', 'absolute = x',
     'test_signals_can_follow_negative_btc_shock'),
]


def fixture():
    n = 138
    date = pd.date_range('2024-01-01', periods=n, freq='D')
    x = np.resize([-3., -1., .2, .3, .5, 1., 1.5, 2.], n).copy()
    x[128] = -8.
    y = .2*x + .08*x*x + np.sin(np.arange(n))*.1
    return date, x, y


def test_prior_fit_matches_linear_residuals():
    date, x, y = fixture()
    t = 129
    shifted_x, shifted_y = x[t-90:t], y[t-90:t]
    design = np.column_stack([np.ones(90), shifted_x])
    residues = lambda v: v - design @ np.linalg.lstsq(design, v, rcond=None)[0]
    expected = np.corrcoef(residues(shifted_y), residues(shifted_x**2))[0, 1]
    result = M.convexity(pd.Series(y, date), pd.Series(x, date))
    assert result.iloc[t] == pytest.approx(expected, abs=1e-10)
    assert np.isnan(result.iloc[89])
    assert np.isnan(M.convexity(pd.Series(x, date), pd.Series(x, date))).all()


def test_signals_can_follow_negative_btc_shock(monkeypatch):
    t, x, y = fixture()
    frame = lambda ret: pd.DataFrame(dict(t=t, c=100 * np.exp(np.cumsum(ret)/100)))
    coin, btc = frame(y), frame(x)
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    M.set_btc(btc)
    full = M.sig_convexity(coin, {}, {})
    assert full['le'].iloc[128] and not full['le'].iloc[127]
    assert not full['se'].any()
    for stop in (121, 128, 129):
        M.set_btc(btc.iloc[:stop])
        partial = M.sig_convexity(coin.iloc[:stop], {}, {})
        for key, value in full.items():
            pd.testing.assert_series_equal(value.iloc[:stop], partial[key])
    M.set_btc(btc)
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(-1., index=c.index))
    assert not M.sig_convexity(coin, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
