"""Synthetic check of composition versus queue size and timestamp availability."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v167 as M

PLANTED_MUTANTS = [
    ('allow_queue_size_surges', ' & (total / previous_total).between(.80, 1.20)', '', 'test_queue_size_can_not_explain_shift'),
    ('allow_impossible_fee_share', ' & high.le(total)', '', 'test_high_priority_size_cannot_exceed_total'),
    ('remove_full_day_buffer', '.shift(1, fill_value=False)', '.shift(0, fill_value=False)', 'test_signal_is_delayed_and_one_shot'),
]


def sample():
    return pd.Series(100., index=range(60)), pd.Series([20.] * 50 + [35.] * 10)


def test_signal_is_delayed_and_one_shot():
    total, high = sample()
    flag = M.priority_shock(total, high)
    assert flag[flag].index.tolist() == [51]
    pd.testing.assert_series_equal(M.priority_shock(total.iloc[:51], high.iloc[:51]), flag.iloc[:51])


def test_queue_size_can_not_explain_shift():
    total, high = sample()
    total.iloc[50:] = 160.
    high.iloc[50:] = 65.
    assert not M.priority_shock(total, high).any()


def test_high_priority_size_cannot_exceed_total():
    total, high = sample()
    high.iloc[50:] = 110.
    assert not M.priority_shock(total, high).any()
