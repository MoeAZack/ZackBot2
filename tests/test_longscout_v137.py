"""Synthetic checks for a matched BTC futures calendar spread."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v137 as M

PLANTED_MUTANTS = [
    ('allow_one_inverted_day', 'min_periods=3).max()', 'min_periods=3).min()',
     'test_three_prior_days_must_all_be_inverted'),
    ('permit_incomplete_pair', 'min_periods=3', 'min_periods=2', 'test_missing_leg_blocks_signal'),
    ('ignore_selected_front', 'active & prior.lt(0)', 'prior.lt(0)', 'test_unavailable_front_cannot_roll_early'),
]


def sample():
    days = pd.date_range('2025-02-01', periods=5)
    expiries = {'front': pd.Timestamp('2025-03-28'), 'back': pd.Timestamp('2025-06-27')}
    contracts = {'front': pd.Series(100., index=days), 'back': pd.Series([101, 99, 99, 99, 100.], index=days)}
    return days, contracts, expiries


def test_three_prior_days_must_all_be_inverted():
    days, contracts, expiries = sample()
    events = M.spread_signal(days, contracts, expiries)
    assert events[events].index.tolist() == [days[-1]]
    contracts['back'].iloc[2] = 100.
    assert not M.spread_signal(days, contracts, expiries).any()


def test_missing_leg_blocks_signal():
    days, contracts, expiries = sample()
    full = M.spread_signal(days, contracts, expiries)
    pd.testing.assert_series_equal(M.spread_signal(days[:-1], contracts, expiries), full.iloc[:-1])
    contracts['front'] = contracts['front'].drop(days[2])
    assert not M.spread_signal(days, contracts, expiries).any()


def test_unavailable_front_cannot_roll_early():
    days, contracts, expiries = sample()
    expiries['nearer'] = pd.Timestamp('2025-02-28')
    assert not M.spread_signal(days, contracts, expiries).any()
