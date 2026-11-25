"""Synthetic checks of pool-share changes and aggregate-work controls."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v169 as M

PLANTED_MUTANTS = [
    ('allow_aggregate_mining_shock', ' & change.le(.075)', '', 'test_aggregate_work_must_be_stable'),
    ('allow_missing_prior_weeks', 'rolling(8, min_periods=8)', 'rolling(8, min_periods=7)',
     'test_prior_week_must_be_observed'),
    ('repeat_persistent_diffusion', ' & ~event.shift(fill_value=False)', '', 'test_use_first_qualifying_week'),
]


def sample():
    return pd.Series([.64] * 13 + [.58] * 3), pd.Series(100., index=range(16))


def test_use_first_qualifying_week():
    share, work = sample()
    event = M.pool_share_broadens(share, work)
    assert event[event].index.tolist() == [13]
    pd.testing.assert_series_equal(M.pool_share_broadens(share.iloc[:13], work.iloc[:13]), event.iloc[:13])


def test_aggregate_work_must_be_stable():
    share, work = sample()
    work.iloc[13:] = 110.
    assert not M.pool_share_broadens(share, work).iloc[13]


def test_prior_week_must_be_observed():
    share, work = sample()
    share.iloc[8] = float('nan')
    assert not M.pool_share_broadens(share, work).any()
