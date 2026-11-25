"""Synthetic weekly range, gap, regime, and causal entry/exit checks."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v107 as M

PLANTED_MUTANTS = [
    ('use_current_week_range', 'previous_week = week - pd.Timedelta(days=7)', 'previous_week = week',
     'test_both_sides_use_prior_range_and_current_open'),
    ('ignore_incomplete_week', 'complete_to_date & width.gt(0)', 'width.gt(0)',
     'test_incomplete_previous_or_current_week_suppresses_entries'),
    ('remove_lower_box_break', 'close.le(anchor - .70 * width)', 'close.le(anchor - 7 * width)',
     'test_box_break_and_week_end_exits'),
]


def fixture():
    dates = pd.date_range('2024-01-01', periods=21, freq='D')
    close = [100] * 7 + [91, 96, 108, 104, 83, 100, 100] + [100] * 7
    return pd.DataFrame(dict(t=dates, o=100.0, h=110.0, l=np.minimum(90.0, close), c=close))


def sideways(monkeypatch):
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(0.0, index=c.index))


def test_both_sides_use_prior_range_and_current_open(monkeypatch):
    sideways(monkeypatch)
    d = fixture()
    anchor, width, _ = M.weekly_box(d)
    assert anchor.iloc[8] == 100 and width.iloc[8] == 20
    result = M.sig_weekly_box(d, {}, {})
    assert np.flatnonzero(result['le']).tolist() == [8]
    assert np.flatnonzero(result['se']).tolist() == [10]
    monkeypatch.setattr(M.L1, 'regime_daily', lambda c: pd.Series(1.0, index=c.index))
    trend = M.sig_weekly_box(d, {}, {})
    assert not trend['le'].any() and not trend['se'].any()


def test_incomplete_previous_or_current_week_suppresses_entries(monkeypatch):
    sideways(monkeypatch)
    d = fixture()
    missing_previous = M.sig_weekly_box(d.drop(index=2), {}, {})
    assert not missing_previous['le'].any() and not missing_previous['se'].any()
    missing_current = M.sig_weekly_box(d.drop(index=8), {}, {})
    assert not missing_current['se'].loc[10]


def test_box_break_and_week_end_exits(monkeypatch):
    sideways(monkeypatch)
    result = M.sig_weekly_box(fixture(), {}, {})
    assert not result['lx'].iloc[8]
    assert result['lx'].iloc[9]
    assert result['lx'].iloc[11]
    assert result['lx'].iloc[13] and result['sx'].iloc[13]
    assert not result['le'].iloc[13] and not result['se'].iloc[13]


def test_prefix_causality_and_default_off(monkeypatch):
    sideways(monkeypatch)
    d = fixture()
    expected = M.sig_weekly_box(d, {}, {})
    for cut in (7, 8, 9, 11, 15):
        partial = M.sig_weekly_box(d.iloc[:cut], {}, {})
        for key in expected:
            pd.testing.assert_series_equal(partial[key], expected[key].iloc[:cut])
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'


def test_bad_timestamps_are_rejected():
    d = fixture()
    d.loc[2, 't'] += pd.Timedelta(hours=4)
    with pytest.raises(ValueError, match='midnight'):
        M.weekly_box(d)
