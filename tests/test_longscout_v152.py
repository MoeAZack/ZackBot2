"""Synthetic retarget reward, halving and confirmation tests."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v152 as M

PLANTED_MUTANTS = [
    ('allow_changed_subsidy', ' & subsidy.eq(subsidy.shift())', '', 'test_current_reward_must_be_same_subsidy'),
    ('remove_reconstruction_buffer', '.shift(7, fill_value=False)', '.shift(0, fill_value=False)',
     'test_retarg_signal_is_delayed_and_does_not_repeat'),
    ('treat_old_tip_as_new_block', ' & height.diff().gt(0)', '', 'test_height_must_advance'),
]


def sample():
    height = pd.Series([500_000 + 150 * day for day in range(12)])
    difficulty = pd.Series([100.] * 3 + [96.] * 9)
    return height, difficulty


def test_retarg_signal_is_delayed_and_does_not_repeat():
    height, difficulty = sample()
    event = M.work_reward_improves(height, difficulty)
    assert event[event].index.tolist() == [10]
    pd.testing.assert_series_equal(M.work_reward_improves(height.iloc[:10], difficulty.iloc[:10]), event.iloc[:10])


def test_current_reward_must_be_same_subsidy():
    height, difficulty = sample()
    height += 129_550
    difficulty.iloc[3:] = 45.
    assert not M.work_reward_improves(height, difficulty).any()


def test_height_must_advance():
    height, difficulty = sample()
    height.iloc[3] = height.iloc[2]
    assert not M.work_reward_improves(height, difficulty).any()
