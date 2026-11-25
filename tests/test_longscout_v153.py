"""Synthetic coverage for Bitcoin output count consolidation."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v153 as M

PLANTED_MUTANTS = [
    ('count_output_set_expansion', ' & shrinking', '', 'test_outstanding_count_must_contract'),
    ('ignore_spent_intensity', ' & flow.ge(1.25 * baseline)', '', 'test_output_activity_must_rise'),
    ('read_unconfirmed_outputs', 'onset.shift(7, fill_value=False)', 'onset',
     'test_delayed_transition_does_not_repeat'),
]


def sample():
    stock = pd.Series([1000.] * 80 + [990.] * 20)
    spent = pd.Series([100.] * 80 + [200.] * 20)
    return stock, spent


def test_delayed_transition_does_not_repeat():
    stock, spent = sample()
    signal = M.output_consolidation(stock, spent)
    assert signal[signal].index.tolist() == [88]
    pd.testing.assert_series_equal(M.output_consolidation(stock.iloc[:88], spent.iloc[:88]), signal.iloc[:88])


def test_outstanding_count_must_contract():
    stock, spent = sample()
    stock.iloc[80:] = 1010.
    assert not M.output_consolidation(stock, spent).any()


def test_output_activity_must_rise():
    stock, spent = sample()
    spent.iloc[80:] = 100.
    assert not M.output_consolidation(stock, spent).any()
