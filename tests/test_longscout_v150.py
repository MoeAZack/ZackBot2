"""Synthetic tests for gross depth shock, timing and recovery."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v150 as M

PLANTED_MUTANTS = [
    ('skip_depth_shock', 'prior_low.le(.65) & ', '', 'test_recovery_requires_severe_low'),
    ('count_gap_as_known', 'rolling(5, min_periods=5)', 'rolling(5, min_periods=4)',
     'test_recent_depth_gaps_block_recovery'),
    ('continue_entering_after_recovery', ' & previous.lt(.85)', '', 'test_crosses_once_without_future_rows'),
]


def sample():
    return pd.Series([100.] * 50 + [60., 74., 80., 100., 102.])


def test_crosses_once_without_future_rows():
    amounts = sample()
    signal = M.liquidity_recovers(amounts)
    assert signal[signal].index.tolist() == [53]
    pd.testing.assert_series_equal(M.liquidity_recovers(amounts.iloc[:53]), signal.iloc[:53])


def test_recovery_requires_severe_low():
    amounts = sample()
    amounts.iloc[50] = 70.
    assert not M.liquidity_recovers(amounts).any()


def test_recent_depth_gaps_block_recovery():
    amounts = sample()
    amounts.iloc[51] = float('nan')
    assert not M.liquidity_recovers(amounts).any()
