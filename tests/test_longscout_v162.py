"""Synthetic coverage for the activity mix and publication delay."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v162 as M

PLANTED_MUTANTS = [
    ('count_general_transaction_growth', ' & (tx / base_tx).between(.90, 1.10)', '',
     'test_general_volume_surge_does_not_count'),
    ('skip_historical_completion_check', 'onset & available.le(decision)', 'onset',
     'test_late_baseline_date_blocks_the_signal'),
    ('count_same_day_download', '.shift(2, fill_value=False)', '.shift(0, fill_value=False)',
     'test_signal_is_delayed_and_single'),
]


def sample():
    days = pd.date_range('2024-01-01', periods=82)
    addresses = pd.Series([100.] * 70 + [130.] * 12, index=days)
    tx = pd.Series(150., index=days)
    complete = pd.Series([(day + pd.Timedelta(hours=29)).value // 10**9 for day in days], index=days)
    return days, addresses, tx, complete


def test_signal_is_delayed_and_single():
    days, addresses, tx, complete = sample()
    signal = M.addresses_without_volume(addresses, tx, complete)
    assert signal[signal].index.tolist() == [days[72]]
    pd.testing.assert_series_equal(M.addresses_without_volume(addresses.iloc[:72], tx.iloc[:72], complete.iloc[:72]),
                                   signal.iloc[:72])


def test_general_volume_surge_does_not_count():
    _, addresses, tx, complete = sample()
    tx.iloc[70:] = 175.
    assert not M.addresses_without_volume(addresses, tx, complete).any()


def test_late_baseline_date_blocks_the_signal():
    days, addresses, tx, complete = sample()
    complete.iloc[10] = (days[73] + pd.Timedelta(seconds=1)).value // 10**9
    assert not M.addresses_without_volume(addresses, tx, complete).any()
