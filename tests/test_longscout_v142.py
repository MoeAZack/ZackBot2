"""Synthetic coverage for agreement across BTC account-count samples."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v142 as M

PLANTED_MUTANTS = [
    ('accept_two_day_disagreement', 'rolling(3, min_periods=3).sum().eq(3)',
     'rolling(2, min_periods=2).sum().eq(2)', 'test_three_consecutive_days_are_required'),
    ('ignore_top_cohort_exit', ' & top_accounts.ge(1.1) & all_accounts.ge(1.)', ' & all_accounts.ge(1.)',
     'test_top_cohort_must_still_be_long'),
    ('allow_nonpositive_ratios', ' & all_accounts.gt(0)', '', 'test_invalid_history_blocks_a_signal'),
]


def example():
    return pd.Series([1.0, 1.1, 1.2, 1.3, 1.1, 1.4]), pd.Series([1.1, .9, .8, .95, 1., 1.1])


def test_three_consecutive_days_are_required():
    top, broad = example()
    hit = M.breadth_catches_up(top, broad)
    assert np.flatnonzero(hit).tolist() == [4]
    pd.testing.assert_series_equal(M.breadth_catches_up(top.iloc[:4], broad.iloc[:4]), hit.iloc[:4])
    broad.iloc[1] = 1.
    assert not M.breadth_catches_up(top, broad).any()


def test_top_cohort_must_still_be_long():
    top, broad = example()
    top.iloc[4] = 1.0
    assert not M.breadth_catches_up(top, broad).any()


def test_invalid_history_blocks_a_signal():
    top, broad = example()
    for invalid in (-1., np.nan, np.inf):
        broad.iloc[2] = invalid
        assert not M.breadth_catches_up(top, broad).any()
