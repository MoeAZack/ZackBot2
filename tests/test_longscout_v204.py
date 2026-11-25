"""Synthetic checks of weekday exchange closure, reversal and candle alignment."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v204 as M

PLANTED_MUTANTS = [
    ('ignore_calendar', 'full_closure & slot', 'slot', 'test_excludes_open_or_early_close'),
    ('ignore_reversal', ' & retrace.between(.20, .80)', '', 'test_rejects_continuation'),
    ('ignore_slot', ' & slot & contiguous', ' & contiguous', 'test_excludes_other_hours'),
]


def flags(day='2025-01-09', hour='12:00', close=(97., 98.5), gap=4):
    start = pd.Timestamp(f'{day} {hour}')
    times = pd.DatetimeIndex([start, start + pd.Timedelta(hours=gap)])
    return M.closure_fade(pd.Series([100., close[0]], index=times),
                          pd.Series(close, index=times), M.NYSE_FULL_CLOSURES)


def test_full_closure_fades_both_directions():
    buy, sell = flags()
    assert buy.tolist() == [False, True]
    assert not sell.any()
    buy, sell = flags(close=(103., 101.5))
    assert not buy.any()
    assert sell.tolist() == [False, True]


def test_excludes_open_or_early_close():
    for day in ('2025-01-08', '2024-11-29', '2025-01-11'):
        buy, sell = flags(day=day)
        assert not (buy | sell).any()


def test_rejects_continuation():
    buy, sell = flags(close=(97., 96.))
    assert not (buy | sell).any()


def test_excludes_other_hours():
    buy, sell = flags(hour='08:00')
    assert not (buy | sell).any()


def test_requires_contiguous_candles():
    buy, sell = flags(hour='08:00', gap=8)
    assert not (buy | sell).any()


def test_full_closure_table_is_unique_and_weekday():
    dates = M.NYSE_FULL_CLOSURES
    assert dates.is_monotonic_increasing and dates.is_unique and (dates.dayofweek < 5).all()
    assert dates.to_series().groupby(dates.year).size().tolist() == [9, 10, 10, 11, 10]
