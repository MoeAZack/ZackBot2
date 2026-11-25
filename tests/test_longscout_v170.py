"""Synthetic checks of matched alt basis recovery and causal breadth."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v170 as M

PLANTED_MUTANTS = [
    ('allow_one_fewer_market', 'recoveries.sum(axis=1).ge(3)', 'recoveries.sum(axis=1).ge(2)',
     'test_needs_three_markets_to_recover'),
    ('allow_missing_quote', 'return complete & recoveries', 'return recoveries',
     'test_needs_paired_quotes_in_all_markets'),
    ('allow_future_quote', 'prior = bases.shift()', 'prior = bases.shift(-1)',
     'test_uses_adjacent_completed_close'),
]


def sample():
    return pd.DataFrame([[-.0004, -.0004, -.0004, 0., 0.], [0., 0., -.0001, 0., 0.]], columns=M.MARKETS)


def test_needs_three_markets_to_recover():
    bases = sample()
    bases.iloc[1, 2] = -.0002
    assert not M.simultaneous_recovery(bases).any()


def test_needs_paired_quotes_in_all_markets():
    bases = sample()
    bases.iloc[0, 4] = float('nan')
    assert not M.simultaneous_recovery(bases).any()


def test_uses_adjacent_completed_close():
    bases = sample()
    events = M.simultaneous_recovery(bases)
    assert events.tolist() == [False, True]
    pd.testing.assert_series_equal(M.simultaneous_recovery(bases.iloc[:1]), events.iloc[:1])
