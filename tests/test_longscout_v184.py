"""Synthetic checks of prior-week isolation, weekly overlap and hard exits."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v184 as M

PLANTED_MUTANTS = [
    ('use_unsettled_sunday', 'weekly.index += pd.Timedelta(days=1)', 'weekly.index += pd.Timedelta(days=0)',
     'test_week_requires_monday_label'),
    ('allow_disjoint_ranges', 'intersection.gt(0) & intersection.ge(.60 * union)', 'intersection.gt(0)',
     'test_weak_overlap_does_not_form_box'),
    ('retain_risk_in_new_week', ' | decision.eq(6)', '', 'test_exits_at_broken_box_and_sunday'),
]


def samples():
    dates = pd.date_range('2026-01-05', periods=28)
    return pd.Series([110.] * 7 + [112.] * 7 + [109.] * 7 + [117.] * 7, index=dates), pd.Series(100., index=dates)


def test_week_requires_monday_label():
    high, low = samples()
    box = M.completed_week_box(high, low)
    assert box.top.first_valid_index() == high.index[21]
    assert box.top.iloc[21] == 109.
    pd.testing.assert_frame_equal(M.completed_week_box(high.iloc[:23], low.iloc[:23]), box.iloc[:23])


def test_weak_overlap_does_not_form_box():
    high, low = samples()
    low.iloc[7:14] = 108.
    assert M.completed_week_box(high, low).isna().all().all()


def test_exits_at_broken_box_and_sunday():
    dates = pd.date_range('2026-01-05', periods=7)
    box = pd.DataFrame(dict(bottom=100., top=110.), index=dates)
    high = pd.Series([104.] * 7, index=dates)
    low = pd.Series([99.] * 7, index=dates)
    close = pd.Series([101.] * 7, index=dates)
    close.iloc[1] = 99.5
    le, se, lx, _ = M.flags_for_box(high, low, close, box, pd.Series(True, index=dates))
    assert le.iloc[0] and not se.any() and not le.iloc[4:6].any()
    assert lx[lx].index.tolist() == [dates[1], dates[6]]
