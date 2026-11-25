"""Synthetic timing, demand confirmation and missing-data checks."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v138 as M

PLANTED_MUTANTS = [
    ('use_same_day_chain_totals', '.shift(2, fill_value=False)', '.shift(0, fill_value=False)',
     'test_first_pulse_is_delayed_two_days'),
    ('allow_fee_only', ' & count.ge(reference_count)', '', 'test_fee_without_transactions_cannot_signal'),
    ('treat_unknown_prior_day_as_below', 'usable.shift(fill_value=False) & ~elevated.shift(fill_value=False)',
     '~elevated.shift(fill_value=False)', 'test_missing_prior_day_blocks_transition'),
]


def synthetic():
    days = pd.date_range('2024-01-01', periods=40)
    fees = pd.Series([100.] * 33 + [200.] * 7, index=days)
    count = pd.Series(100., index=days)
    return days, fees, count


def test_first_pulse_is_delayed_two_days():
    days, fees, count = synthetic()
    result = M.demand_pulse(fees, count)
    assert result[result].index.tolist() == [days[35]]
    for n in (32, 34, 36):
        pd.testing.assert_series_equal(M.demand_pulse(fees.iloc[:n], count.iloc[:n]), result.iloc[:n])


def test_fee_without_transactions_cannot_signal():
    _, fees, count = synthetic()
    count.iloc[33:] = 50.
    assert not M.demand_pulse(fees, count).any()


def test_missing_prior_day_blocks_transition():
    days, fees, count = synthetic()
    fees.iloc[2] = float('nan')
    assert not M.demand_pulse(fees, count).loc[days[35]]
