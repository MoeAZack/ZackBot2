"""Synthetic causality and distinction from an open-interest contraction."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v146 as M

PLANTED_MUTANTS = [
    ('lose_recent_capacity_stress', 'high & ', '', 'test_no_entry_without_prior_capacity_stress'),
    ('accept_open_interest_liquidation', ' & oi.diff().gt(0)', '', 'test_open_interest_must_rise'),
    ('assume_missing_turnover_is_known', 'rolling(7, min_periods=7)', 'rolling(7, min_periods=6)',
     'test_a_missing_day_invalidates_capacity'),
]


def fixture(stressed=140.):
    data = pd.RangeIndex(55)
    oi = pd.Series([100.] * 47 + [stressed] * 7 + [stressed + 1], index=data)
    volume = pd.Series([100.] * 54 + [430.], index=data)
    return oi, volume


def test_capacity_recovery_crosses_after_observable_turnover():
    oi, volume = fixture()
    expected = M.turnover_catches_up(oi, volume)
    assert expected[expected].index.tolist() == [54]
    pd.testing.assert_series_equal(M.turnover_catches_up(oi.iloc[:-1], volume.iloc[:-1]), expected.iloc[:-1])


def test_no_entry_without_prior_capacity_stress():
    oi, volume = fixture(stressed=110.)
    assert not M.turnover_catches_up(oi, volume).any()


def test_open_interest_must_rise():
    oi, volume = fixture()
    oi.iloc[-1] = 139.
    assert not M.turnover_catches_up(oi, volume).any()


def test_a_missing_day_invalidates_capacity():
    oi, volume = fixture()
    volume.iloc[-2] = float('nan')
    assert not M.turnover_catches_up(oi, volume).any()
