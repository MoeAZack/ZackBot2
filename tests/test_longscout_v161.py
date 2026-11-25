"""Synthetic tests for relative ratios and balanced cohort coverage."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v161 as M

PLANTED_MUTANTS = [
    ('only_use_absolute_majority', 'ratios.gt(reference)', 'ratios.gt(1)', 'test_market_specific_baselines'),
    ('count_missing_market_as_flat', 'ratios.notna().all(axis=1)', 'ratios.notna().any(axis=1)',
     'test_one_missing_market_blocks_signal'),
    ('drop_preceding_five_day_gate', ' & prior.rolling(5, min_periods=5).mean().le(.4)', '',
     'test_prior_improvement_must_be_broad_based'),
]


def sample():
    ratio = pd.DataFrame([[1.2, 1.4, .7, .8, 1.1]] * 64, columns=M.MARKETS)
    ratio.iloc[60:, :4] *= 1.1
    return ratio


def test_market_specific_baselines():
    ratio = sample()
    hit = M.broadening(ratio)
    assert hit[hit].index.tolist() == [60]
    ratio.iloc[:, 2] *= 7
    pd.testing.assert_series_equal(M.broadening(ratio), hit)


def test_one_missing_market_blocks_signal():
    ratio = sample()
    ratio.iloc[60, 4] = float('nan')
    assert not M.broadening(ratio).any()


def test_prior_improvement_must_be_broad_based():
    ratio = sample()
    ratio.iloc[55:59, :4] *= 1.3
    assert not M.broadening(ratio).iloc[60]
