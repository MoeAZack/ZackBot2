"""Synthetic tests for joint issuer supply and data completion."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v140 as M

PLANTED_MUTANTS = [
    ('ignore_usdt_expansion', ' & usdt.gt(usdt.shift(7))', '', 'test_both_issuers_must_expand'),
    ('ignore_late_snapshot', 'crossing & available.le(decision)', 'crossing', 'test_one_late_snapshot_blocks_entry'),
    ('reenter_while_expanding', ' & ~expanding.shift(fill_value=False)', '', 'test_entry_occurs_once_after_delay'),
]


def setup_series():
    days = pd.date_range('2024-01-01', periods=20)
    usdc = pd.Series([100.] * 10 + [102.] * 10, index=days)
    usdt = pd.Series([100.] * 10 + [101.] * 10, index=days)
    completion = pd.Series([(day + pd.Timedelta(hours=29)).value // 10**9 for day in days], index=days)
    return days, usdc, usdt, completion, completion.copy()


def test_entry_occurs_once_after_delay():
    days, usdc, usdt, done_a, done_b = setup_series()
    inspected = M.joint_expansion(usdc, usdt, done_a, done_b)
    assert inspected[inspected].index.tolist() == [days[12]]
    prefix = M.joint_expansion(usdc.iloc[:12], usdt.iloc[:12], done_a.iloc[:12], done_b.iloc[:12])
    pd.testing.assert_series_equal(prefix, inspected.iloc[:12])


def test_both_issuers_must_expand():
    _, usdc, usdt, done_a, done_b = setup_series()
    usdt.iloc[10:] = 100.
    assert not M.joint_expansion(usdc, usdt, done_a, done_b).any()


def test_one_late_snapshot_blocks_entry():
    days, usdc, usdt, done_a, done_b = setup_series()
    done_b.iloc[2] = (days[13] + pd.Timedelta(seconds=1)).value // 10**9
    assert not M.joint_expansion(usdc, usdt, done_a, done_b).any()
    done_b.iloc[2] -= 1
    assert M.joint_expansion(usdc, usdt, done_a, done_b).loc[days[12]]
