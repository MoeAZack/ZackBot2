"""Synthetic formula, scale and activity controls for inferred spread."""
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v131 as M

PLANTED_MUTANTS = [
    ('omit_two_day_extent', ' - np.sqrt(gamma / denom)', '',
     'test_two_day_spread_matches_scalar_equation'),
    ('let_range_contraction_count', ' & price_range.ge(prior_range)', '',
     'test_spread_drop_with_stable_range_only'),
    ('include_shock_in_baseline', 'normal = spread.shift(2).rolling', 'normal = spread.shift(-4).rolling',
     'test_baseline_is_prior_to_shock'),
]


def test_two_day_spread_matches_scalar_equation():
    h, l = pd.Series([120., 121., 145.]), pd.Series([100., 101., 132.])
    result, _ = M.spread_proxy(h, l)
    for i in (1, 2):
        beta = sum(math.log(h.iloc[j] / l.iloc[j])**2 for j in (i-1, i))
        gamma = math.log(max(h.iloc[i-1:i+1]) / min(l.iloc[i-1:i+1]))**2
        constant = 3 - 2*math.sqrt(2)
        alpha = max(0, (math.sqrt(2*beta) - math.sqrt(beta))/constant - math.sqrt(gamma/constant))
        expected = 2*(math.exp(alpha)-1)/(math.exp(alpha)+1)
        assert result.iloc[i] == pytest.approx(expected)
    assert np.isnan(result.iloc[0])
    assert M.spread_proxy(h.mul(5), l.mul(5))[0].iloc[1] == pytest.approx(result.iloc[1])


def fixture():
    price = pd.Series(100. + np.arange(38))
    spread = pd.Series(.01, index=price.index)
    spread.iloc[25:27] = [.05, .006]
    price_range = pd.Series(.04, index=price.index)
    return price, spread, price_range


def test_spread_drop_with_stable_range_only():
    price, spread, price_range = fixture()
    assert np.flatnonzero(M.spread_recovery(price, spread, price_range)).tolist() == [26]
    price_range.iloc[26] = .02
    assert not M.spread_recovery(price, spread, price_range).any()


def test_baseline_is_prior_to_shock():
    price, spread, price_range = fixture()
    full = M.spread_recovery(price, spread, price_range)
    for n in (20, 25, 26, 27, 34):
        actual = M.spread_recovery(price.iloc[:n], spread.iloc[:n], price_range.iloc[:n])
        pd.testing.assert_series_equal(actual, full.iloc[:n])
    spread.iloc[23] = np.nan
    assert not M.spread_recovery(price, spread, price_range).any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
