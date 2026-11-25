"""Synthetic point-and-figure column and market-data checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v111 as M

PLANTED_MUTANTS = [
    ('ignore_higher_base', 'extreme > last_bottom', 'extreme >= last_bottom',
     'test_equal_falling_column_low_is_rejected'),
    ('accept_two_box_reversal', 'lower <= extreme - 3', 'lower <= extreme - 2',
     'test_reversal_count_and_exact_once'),
    ('bridge_missing_calendar_day', ' or date - previous_date != day', '',
     'test_prefix_and_missing_day'),
]


def fixture(levels=(0, 6, 2, 6, 3, 6, 7, 8)):
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=len(levels), freq='D'),
                             c=100 * np.power(1.025, np.array(levels))))


def events(d):
    return np.flatnonzero(M.point_figure_breakout(pd.DatetimeIndex(d.t), d.c)).tolist()


def test_reversal_count_and_exact_once():
    assert events(fixture()) == [6]
    assert events(fixture((0, 6, 2, 6, 4, 6, 7, 8))) == []
    assert events(fixture((0, 6, 2, 6, 3, 8, 9, 10))) == [5]


def test_equal_falling_column_low_is_rejected():
    assert events(fixture((0, 6, 2, 6, 2, 6, 7, 8))) == []


def test_prefix_and_missing_day(monkeypatch):
    d = fixture()
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0., index=c.index))
    signals = M.sig_point_figure(d, {}, {})
    assert np.flatnonzero(signals['le']).tolist() == [6]
    for stop in range(1, len(d) + 1):
        part = M.sig_point_figure(d.iloc[:stop], {}, {})
        for key, values in signals.items():
            pd.testing.assert_series_equal(part[key], values.iloc[:stop])
    missing_day = d.copy()
    missing_day.loc[4:, 't'] += pd.Timedelta(days=1)
    assert events(missing_day) == []
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(-1., index=c.index))
    assert not M.sig_point_figure(d, {}, {})['le'].any()
