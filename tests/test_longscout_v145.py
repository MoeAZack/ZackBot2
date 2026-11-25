"""Synthetic checks for factor composition, timestamps and turn conditions."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v145 as M

PLANTED_MUTANTS = [
    ('fit_with_current_outcome', 'covariance.div(variance, axis=0).shift()', 'covariance.div(variance, axis=0)',
     'test_beta_excludes_current_day'),
    ('count_mechanical_btc_move', 'returns - beta.mul(btc, axis=0)', 'returns',
     'test_spread_removes_mechanical_btc_response'),
    ('no_negative_history', 'previous.rolling(5, min_periods=5).mean().lt(0) & ', '',
     'test_turn_requires_negative_preceding_week'),
]


def test_beta_excludes_current_day():
    btc = pd.Series(np.linspace(-.02, .02, 66))
    coins = pd.DataFrame({'x': 2 * btc, 'y': .5 * btc})
    before = M.prior_betas(coins, btc)
    coins.at[62, 'x'] = 1.
    after = M.prior_betas(coins, btc)
    np.testing.assert_allclose(before.loc[62], [2., .5])
    pd.testing.assert_series_equal(before.loc[62], after.loc[62])
    assert not np.isclose(before.loc[63, 'x'], after.loc[63, 'x'])


def test_spread_removes_mechanical_btc_response():
    beta = pd.DataFrame([np.linspace(.5, 2., 60)] * 2)
    btc = pd.Series([.02, .03])
    returns = beta.mul(btc, axis=0)
    returns.iloc[1, -15:] += .008
    liquid = beta.notna()
    spread, top = M.risk_spread(returns, btc, beta, liquid)
    np.testing.assert_allclose(spread, [0., .008], atol=1e-15)
    assert top.sum(axis=1).eq(15).all()
    liquid.iloc[1, 0] = False
    assert pd.isna(M.risk_spread(returns, btc, beta, liquid)[0].iloc[1])


def test_turn_requires_negative_preceding_week():
    confirmed = pd.Series([-.004] * 5 + [.005, .006])
    assert np.flatnonzero(M.factor_resumes(confirmed)).tolist() == [5]
    confirmed.iloc[:4] = .004
    assert not M.factor_resumes(confirmed).any()
