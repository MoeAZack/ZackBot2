"""Checks signed divergence, observable prior state and one-shot entry."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v156 as M

PLANTED_MUTANTS = [
    ('let_aligned_premium_count', 'premium.le(-.0002)', 'premium.ge(.0002)', 'test_opposing_legs_are_required'),
    ('ignore_missing_prior', 'long & before &', 'long &', 'test_missing_prior_is_not_a_clean_transition'),
    ('repeat_persistent_long', ' & ~long.shift(fill_value=False)', '', 'test_persistent_cases_emit_one_entry'),
]


def test_opposing_legs_are_required():
    paid = pd.Series([0., .0003, .0003, 0., -.0003])
    premium = pd.Series([0., .0003, -.0003, 0., .0003])
    state = M.divergence(paid, premium)
    assert state.index[state['long']].tolist() == [2]
    assert state.index[state['short']].tolist() == [4]


def test_missing_prior_is_not_a_clean_transition():
    paid = pd.Series([0., float('nan'), .0003, .0003])
    premium = pd.Series([0., 0., -.0003, -.0003])
    assert not M.divergence(paid, premium).any().any()


def test_persistent_cases_emit_one_entry():
    paid = pd.Series([0., .0003, .0004, .0005])
    premium = pd.Series([0., -.0003, -.0003, -.0003])
    state = M.divergence(paid, premium)
    assert state.index[state['long']].tolist() == [1]
    pd.testing.assert_frame_equal(M.divergence(paid.iloc[:2], premium.iloc[:2]), state.iloc[:2])
