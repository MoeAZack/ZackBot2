"""Synthetic checks of same-token rate convergence and adjacent dates."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v186 as M

PLANTED_MUTANTS = [
    ('allow_lower_venue_shock', ' & former_low_change.abs().le(.25)', '', 'test_lower_rate_must_be_stable'),
    ('treat_aave_as_always_higher', 'spread.shift().gt(0)', 'spread.shift().notna()', 'test_either_venue_can_normalize'),
    ('same_day_use', 'return pulse.shift(1, fill_value=False)', 'return pulse', 'test_one_day_buffer'),
]


def sample():
    return pd.DataFrame({'AaveV3': [4.2, 3.3, 3.3], 'CompoundV3': [3., 3., 3.]})


def test_lower_rate_must_be_stable():
    quotes = sample()
    quotes.loc[1:, 'CompoundV3'] = 3.5
    assert not M.venue_gap_closes(quotes).any()


def test_either_venue_can_normalize():
    quotes = sample()
    reverse = quotes[['CompoundV3', 'AaveV3']].set_axis(M.FIELDS, axis=1)
    assert M.venue_gap_closes(reverse).tolist() == [False, False, True]


def test_one_day_buffer():
    quotes = sample()
    assert M.venue_gap_closes(quotes).tolist() == [False, False, True]
    assert not M.venue_gap_closes(quotes.iloc[:2]).any()
