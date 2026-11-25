"""Synthetic availability and issuance checks for the ETH supply signal."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v149 as M

PLANTED_MUTANTS = [
    ('ignore_arrival_cutoff', 'onset & available.le(decision)', 'onset', 'test_late_source_row_blocks_entry'),
    ('accept_issuance_slump', ' & stable_issuance', '', 'test_issuance_cannot_be_falling_materially'),
    ('remove_previous_supply_growth', ' & change.shift(21).gt(0)', '', 'test_prior_interval_must_be_positive'),
]


def fixture():
    days = pd.date_range('2024-01-01', periods=100)
    supply = pd.Series(1000. + np.r_[np.arange(53), 52 - 3 * np.arange(1, 48)], index=days)
    issuance = pd.Series(10., index=days)
    completion = pd.Series([(x + pd.Timedelta(hours=30)).value // 10**9 for x in days], index=days)
    return days, supply, issuance, completion


def test_late_source_row_blocks_entry():
    days, supply, issuance, completed = fixture()
    signals = M.supply_contraction(supply, issuance, completed)
    assert signals[signals].index.tolist() == [days[60]]
    for n in (55, 59, 61):
        pd.testing.assert_series_equal(M.supply_contraction(supply.iloc[:n], issuance.iloc[:n], completed.iloc[:n]),
                                       signals.iloc[:n])
    completed.iloc[16] = (days[61] + pd.Timedelta(seconds=1)).value // 10**9
    assert not M.supply_contraction(supply, issuance, completed).any()


def test_issuance_cannot_be_falling_materially():
    _, supply, issuance, completed = fixture()
    issuance.iloc[45:] = 1.
    assert not M.supply_contraction(supply, issuance, completed).any()


def test_prior_interval_must_be_positive():
    days, supply, issuance, completed = fixture()
    supply.iloc[:33] = 1000. - 2 * np.arange(33)
    supply.iloc[33:53] = 936. + .5 * np.arange(1, 21)
    supply.iloc[53:] = 946. - 3 * np.arange(1, 48)
    assert not M.supply_contraction(supply, issuance, completed).loc[days[58]]
