"""Synthetic checks for same-direction persistence and usable volume."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v158 as M

PLANTED_MUTANTS = [
    ('allow_aligned_pressure', 'inverse.le(-.12)', 'inverse.ge(.12)', 'test_legs_must_oppose'),
    ('ignore_missing_previous_context', ' & observed.shift(3, fill_value=False)', '', 'test_gap_does_not_create_clean_onset'),
    ('accept_impossible_buy_volume', ' & buy.le(total)', '', 'test_volume_is_normalized_within_its_own_leg'),
]


def sample():
    return pd.Series([0., .2, .2, .2, .2]), pd.Series([0., -.2, -.2, -.2, -.2])


def test_legs_must_oppose():
    linear, inverse = sample()
    result = M.disagreement(linear, inverse)
    assert result[result].index.tolist() == [3]
    assert not M.disagreement(linear, linear).any()
    pd.testing.assert_series_equal(M.disagreement(linear.iloc[:3], inverse.iloc[:3]), result.iloc[:3])


def test_gap_does_not_create_clean_onset():
    linear, inverse = sample()
    inverse.iloc[0] = float('nan')
    assert not M.disagreement(linear, inverse).any()


def test_volume_is_normalized_within_its_own_leg():
    total, buy = pd.Series([100., 100., 100.]), pd.Series([60., 40., 101.])
    original = M.signed_imbalance(total, buy)
    pd.testing.assert_series_equal(original, M.signed_imbalance(total * 100, buy * 100))
    assert original.iloc[0] > 0 and original.iloc[1] < 0 and pd.isna(original.iloc[2])
