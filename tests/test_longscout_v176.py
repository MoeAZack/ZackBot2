"""Synthetic checks of gross short covering and effective publication dates."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v176 as M

PLANTED_MUTANTS = [
    ('ignore_gross_short_covering', ' & short.diff().lt(0)', '', 'test_gross_shorts_must_fall'),
    ('ignore_asset_manager_stability', ' & assets.diff().abs().le(.03)', '', 'test_asset_managers_must_hold_steady'),
    ('trade_before_publication', 'return qualified.set_axis(available)', 'return qualified', 'test_uses_later_release_date'),
]


def sample():
    dates = pd.date_range('2025-09-23', periods=2, freq='W-TUE')
    quotes = pd.DataFrame([[1000, 350, 100, 100, 360], [1000, 360, 100, 100, 300]],
                          index=dates, columns=M.FIELDS)
    return quotes, pd.DatetimeIndex(pd.to_datetime(['2025-10-02', '2025-11-20']))


def test_gross_shorts_must_fall():
    quotes, available = sample()
    quotes.iloc[-1, -2:] = [160, 360]
    assert not M.cover_shorts(quotes, available).any()


def test_asset_managers_must_hold_steady():
    quotes, available = sample()
    quotes.iloc[-1, 1] += 100
    assert not M.cover_shorts(quotes, available).any()


def test_uses_later_release_date():
    quotes, available = sample()
    events = M.cover_shorts(quotes, available)
    assert events[events].index.tolist() == [available[-1]]
    assert not M.cover_shorts(quotes.iloc[:1], available[:1]).any()
