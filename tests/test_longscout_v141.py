"""Synthetic tests for older-output spending shares and timing."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v141 as M

PLANTED_MUTANTS = [
    ('remove_age_share_spike', ' & prior_peak.ge(1.5)', '', 'test_small_spike_cannot_signal'),
    ('remove_confirmation_buffer', '.shift(7, fill_value=False)', '.shift(0, fill_value=False)',
     'test_recovery_entry_has_seven_day_buffer'),
    ('count_impossible_share', ' & older.le(total)', '', 'test_invalid_age_value_breaks_lookback'),
]


def setup_series(spike):
    days = pd.date_range('2024-01-01', periods=77)
    spent = pd.Series(100., index=days)
    older = pd.Series([10.] * 48 + [spike] * 7 + [2.] * 22, index=days)
    return days, older, spent


def test_recovery_entry_has_seven_day_buffer():
    days, older, spent = setup_series(20.)
    signal = M.older_spending_relief(older, spent)
    assert signal[signal].index.tolist() == [days[65]]
    for n in (53, 65, 66):
        pd.testing.assert_series_equal(M.older_spending_relief(older.iloc[:n], spent.iloc[:n]), signal.iloc[:n])


def test_small_spike_cannot_signal():
    _, older, spent = setup_series(12.)
    assert not M.older_spending_relief(older, spent).any()


def test_invalid_age_value_breaks_lookback():
    _, older, spent = setup_series(20.)
    older.iloc[33] = 101.
    assert not M.older_spending_relief(older, spent).any()
