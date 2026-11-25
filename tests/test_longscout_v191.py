"""Synthetic checks of Treasury delivery fails and delayed weekly release."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v191 as M

PLANTED_MUTANTS = [
    ('mix_two_report_structures', ' & same_series', '', 'test_requires_one_reporting_series'),
    ('allow_a_week_to_go_missing', 'adjacent & same_series', 'same_series', 'test_requires_adjacent_reports'),
    ('use_report_before_publication', 'pd.Timedelta(days=15)', 'pd.Timedelta(days=0)', 'test_uses_conservative_lag'),
]


def sample():
    week = pd.date_range('2025-01-08', periods=14, freq='W-WED')
    frame = pd.DataFrame([[80., 20.]] * 12 + [[200., 30.], [70., 25.]], index=week, columns=M.FIELDS)
    return frame, pd.Series('SBN2024', index=week)


def test_requires_one_reporting_series():
    frame, period = sample()
    period.iloc[:3] = 'SBN2022'
    assert not M.delivery_fails_ease(frame, period).any()


def test_requires_adjacent_reports():
    frame, period = sample()
    frame.index = frame.index[:-1].append(frame.index[-1:] + pd.Timedelta(days=7))
    period.index = frame.index
    assert not M.delivery_fails_ease(frame, period).any()


def test_uses_conservative_lag():
    frame, period = sample()
    event = M.delivery_fails_ease(frame, period)
    assert event[event].index.tolist() == [frame.index[-1] + pd.Timedelta(days=15)]
    assert not M.delivery_fails_ease(frame.iloc[:-1], period.iloc[:-1]).any()
