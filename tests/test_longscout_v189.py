"""Synthetic checks of two-window realized capitalization and availability."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v189 as M

PLANTED_MUTANTS = [
    ('allow_market_cap_breakout', ' & market.abs().le(.05)', '', 'test_market_cap_must_be_stable'),
    ('allow_missing_day', 'rolling(61, min_periods=61).sum().eq(61)', 'rolling(61, min_periods=61).sum().ge(60)',
     'test_requires_contiguous_observations'),
    ('ignore_buffer', 'return event.shift(2, fill_value=False)', 'return event', 'test_uses_two_day_buffer')
]


def sample():
    return pd.DataFrame(dict(realized_cap_usd=[100.] * 65 + [101.] * 5, market_cap_usd=200.))


def test_market_cap_must_be_stable():
    caps = sample()
    caps.loc[65:, 'market_cap_usd'] = 218.
    assert not M.books_reprice(caps).any()


def test_requires_contiguous_observations():
    caps = sample()
    caps.iloc[10, 1] = float('nan')
    assert not M.books_reprice(caps).any()


def test_uses_two_day_buffer():
    caps = sample()
    flag = M.books_reprice(caps)
    assert flag[flag].index.tolist() == [67]
    assert not M.books_reprice(caps.iloc[:67]).any()
