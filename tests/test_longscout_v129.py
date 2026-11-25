"""Independent full-matrix and prefix controls for the state-space fade."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v129 as M

PLANTED_MUTANTS = [
    ('ignore_scale_lag', 'log_price.diff().shift().rolling', 'log_price.diff().rolling',
     'test_scalar_filter_matches_full_matrix_reference'),
    ('ignore_temporary_decay', 'temporary *= DECAY', 'temporary *= 1.',
     'test_scalar_filter_matches_full_matrix_reference'),
    ('let_falling_btc_through', 'L1.regime_daily(close).isin((0., 1.))', 'L1.regime_daily(close).notna()',
     'test_buy_and_sell_turns_require_correct_regimes'),
]


def fixture():
    rng = np.random.default_rng(129)
    t = pd.date_range('2024-01-01', periods=110, freq='D')
    return pd.Series(100*np.exp(np.cumsum(rng.normal(0., .015, len(t)))), index=t)


def reference(price):
    observation = np.log(price)
    transition = np.diag([1., .6])
    obs = np.ones(2)
    estimates = np.full(len(price), np.nan)
    for i in range(31, len(price)):
        sigma = observation.iloc[i-31:i].diff().dropna().std()
        if i == 31:
            state = np.array([observation.iloc[i], 0.])
            covariance = np.eye(2) * sigma**2
            continue
        state = transition @ state
        covariance = transition @ covariance @ transition.T + np.diag([(.25*sigma)**2, (.45*sigma)**2])
        weight = covariance @ obs / (obs @ covariance @ obs + (.15*sigma)**2)
        state += weight * (observation.iloc[i] - obs @ state)
        covariance = (np.eye(2) - np.outer(weight, obs)) @ covariance
        if i >= 61:
            estimates[i] = state[1] / np.sqrt(covariance[1, 1])
    return estimates


def test_scalar_filter_matches_full_matrix_reference():
    price = fixture()
    np.testing.assert_allclose(M.distortion_z(price), reference(price), atol=1e-11, rtol=1e-11)


def test_prefix_and_input_gap():
    price = fixture()
    full = M.distortion_z(price)
    for n in (31, 61, 62, 90):
        pd.testing.assert_series_equal(M.distortion_z(price.iloc[:n]), full.iloc[:n])
    price.iloc[67] = np.nan
    assert M.distortion_z(price).iloc[67:].isna().all()


def test_buy_and_sell_turns_require_correct_regimes(monkeypatch):
    price = fixture().iloc[:8].copy()
    price.iloc[:] = [100, 97, 98, 99, 102, 101, 100, 101]
    frame = pd.DataFrame(dict(t=price.index, c=price.to_numpy(), qv=2e7))
    outcomes = pd.Series([0, -2, -1, -.5, 2, 1, .5, 0], index=price.index)
    monkeypatch.setattr(M, 'distortion_z', lambda values: outcomes)
    monkeypatch.setattr(M.L1, 'regime_daily', lambda close: pd.Series(0., index=close.index))
    M.set_btc(frame)
    flags = M.sig_state_fade(frame, {}, {})
    assert np.flatnonzero(flags['le']).tolist() == [2]
    assert np.flatnonzero(flags['se']).tolist() == [5]
    monkeypatch.setattr(M.L1, 'regime_daily', lambda close: pd.Series(-1., index=close.index))
    M.set_btc(frame)
    monkeypatch.setattr(M.L1, 'regime_daily', lambda close: pd.Series(0., index=close.index))
    assert not M.sig_state_fade(frame, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'
