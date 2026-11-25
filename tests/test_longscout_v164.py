"""Synthetic Lightning coverage, timing and aggregate-capacity checks."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v164 as M

PLANTED_MUTANTS = [
    ('accept_unchanged_capacity', ' & capacity.pct_change(14, fill_method=None).ge(.02)', '',
     'test_capacity_must_increase_in_aggregate'),
    ('allow_missing_aligned_date', 'rolling(15, min_periods=15).sum().eq(15)',
     'rolling(15, min_periods=15).sum().ge(14)', 'test_missing_date_breaks_complete_window'),
    ('ignore_data_delay', '.shift(2, fill_value=False)', '.shift(0, fill_value=False)',
     'test_first_qualified_observation_is_delayed'),
]


def sample():
    return pd.Series([1000.] * 25 + [850.] * 8), pd.Series([100.] * 25 + [106.] * 8)


def test_first_qualified_observation_is_delayed():
    count, capacity = sample()
    signal = M.channels_deepen(count, capacity)
    assert signal[signal].index.tolist() == [27]
    pd.testing.assert_series_equal(M.channels_deepen(count.iloc[:27], capacity.iloc[:27]), signal.iloc[:27])


def test_capacity_must_increase_in_aggregate():
    count, capacity = sample()
    capacity.iloc[25:] = 100.
    assert not M.channels_deepen(count, capacity).any()


def test_missing_date_breaks_complete_window():
    count, capacity = sample()
    capacity.iloc[20] = float('nan')
    assert not M.channels_deepen(count, capacity).any()
