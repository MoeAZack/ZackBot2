"""Synthetic tests for wrapped-supply reversals relative to native issuance."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v157 as M

PLANTED_MUTANTS = [
    ('ignore_native_growth_scale', 'mint.ge(.50 * growth)', 'mint.gt(0)', 'test_requires_material_wrapped_growth'),
    ('require_no_previous_contraction', ' & mint.shift(7).lt(0)', '', 'test_unchanged_wrapped_supply_can_not_arm_growth'),
    ('ignore_late_snapshot', 'signal & matched.le(decision)', 'signal', 'test_publication_is_a_hard_boundary'),
]


def samples():
    days = pd.date_range('2024-01-01', periods=48)
    native = pd.Series(10000. + 10. * np.arange(48), index=days)
    wrapped = pd.Series(np.r_[[100.] * 21, np.arange(98., 84., -2.), 86. + 10. * np.arange(1, 21)], index=days)
    done = pd.Series([(day + pd.Timedelta(hours=29)).value // 10**9 for day in days], index=days)
    return days, native, wrapped, done, done.copy()


def test_publication_is_a_hard_boundary():
    days, native, wrapped, done_a, done_b = samples()
    result = M.wrapping_resumes(native, wrapped, done_a, done_b)
    assert result[result].index.tolist() == [days[34]]
    for n in (32, 34, 35):
        prefix = M.wrapping_resumes(native.iloc[:n], wrapped.iloc[:n], done_a.iloc[:n], done_b.iloc[:n])
        pd.testing.assert_series_equal(prefix, result.iloc[:n])
    done_b.iloc[20] = (days[35] + pd.Timedelta(seconds=1)).value // 10**9
    assert not M.wrapping_resumes(native, wrapped, done_a, done_b).any()


def test_requires_material_wrapped_growth():
    _, native, wrapped, done_a, done_b = samples()
    wrapped.iloc[28:] = 86. + 2. * np.arange(1, 21)
    assert not M.wrapping_resumes(native, wrapped, done_a, done_b).any()


def test_unchanged_wrapped_supply_can_not_arm_growth():
    _, native, wrapped, done_a, done_b = samples()
    wrapped.iloc[:28] = 86.
    assert not M.wrapping_resumes(native, wrapped, done_a, done_b).any()
