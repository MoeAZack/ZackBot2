"""Synthetic checks of Treasury coupon inventory, aggregation and timing."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v192 as M

PLANTED_MUTANTS = [
    ('allow_incomplete_maturities', 'min_count=len(FIELDS)', 'min_count=len(FIELDS) - 1',
     'test_all_coupon_buckets_required'),
    ('allow_net_position_to_flip_short', ' & net.gt(0)', '', 'test_current_inventory_must_remain_long'),
    ('ignore_release_lag', 'pd.Timedelta(days=15)', 'pd.Timedelta(days=0)', 'test_release_buffer_and_prefix'),
]


def sample():
    week = pd.date_range('2025-01-08', periods=14, freq='W-WED')
    balances = pd.DataFrame([[100.] * 7] * 12 + [[180.] * 7, [110.] * 7], index=week, columns=M.FIELDS)
    return balances, pd.Series('SBN2024', index=week)


def test_all_coupon_buckets_required():
    balances, period = sample()
    balances.iloc[-1, 0] = float('nan')
    assert not M.dealer_inventory_reduces(balances, period).any()


def test_current_inventory_must_remain_long():
    balances, period = sample()
    balances.iloc[-1] = -1.
    assert not M.dealer_inventory_reduces(balances, period).any()


def test_release_buffer_and_prefix():
    balances, period = sample()
    signal = M.dealer_inventory_reduces(balances, period)
    assert signal[signal].index.tolist() == [balances.index[-1] + pd.Timedelta(days=15)]
    assert not M.dealer_inventory_reduces(balances.iloc[:-1], period.iloc[:-1]).any()
