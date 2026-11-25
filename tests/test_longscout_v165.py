"""Synthetic checks for clearance, arrival isolation and known sampling."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v165 as M

PLANTED_MUTANTS = [
    ('accept_abating_arrivals', ' & arrival_is_normal', '', 'test_arrival_decline_does_not_count'),
    ('ignore_prior_backlog', 'stress & ', '', 'test_clearance_requires_prior_backlog'),
    ('read_current_day_aggregation', '.shift(1, fill_value=False)', '.shift(0, fill_value=False)',
     'test_clearance_is_delayed'),
]


def sample():
    return pd.Series([100.] * 70 + [170.] * 5 + [80.] * 5), pd.Series(200., index=range(80))


def test_clearance_is_delayed():
    pending, arrivals = sample()
    signal = M.queue_clears(pending, arrivals)
    assert signal[signal].index.tolist() == [76]
    pd.testing.assert_series_equal(M.queue_clears(pending.iloc[:76], arrivals.iloc[:76]), signal.iloc[:76])


def test_arrival_decline_does_not_count():
    pending, arrivals = sample()
    arrivals.iloc[75] = 100.
    assert not M.queue_clears(pending, arrivals).any()


def test_clearance_requires_prior_backlog():
    pending, arrivals = sample()
    pending.iloc[70:75] = 130.
    assert not M.queue_clears(pending, arrivals).any()
