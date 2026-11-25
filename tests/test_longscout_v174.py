"""Synthetic checks of cross-maturity volatility and data availability."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v174 as M

PLANTED_MUTANTS = [
    ('allow_month_vol_rise', ' & usable.VIX.le(usable.VIX.shift())', '', 'test_denominator_cannot_drive_recovery'),
    ('ignore_extra_utc_day', 'pd.Timedelta(days=1)', 'pd.Timedelta(days=0)', 'test_uses_one_full_extra_day'),
    ('allow_week_old_inversion', '.dt.days.between(1, 4)', '.dt.days.ge(1)', 'test_rejects_old_observation'),
]


def sample():
    return pd.DataFrame({'VIX9D': [24., 17., 16.], 'VIX': [20., 19., 18.]},
                        index=pd.date_range('2026-01-05', periods=3, freq='B'))


def test_denominator_cannot_drive_recovery():
    values = sample().iloc[:2].copy()
    values.VIX = [20., 21.]
    assert not M.inversion_ends(values).any()


def test_uses_one_full_extra_day():
    values = sample()
    events = M.inversion_ends(values)
    assert events[events].index.tolist() == [values.index[1] + pd.Timedelta(days=1)]
    assert not M.inversion_ends(values.iloc[:1]).any()


def test_rejects_old_observation():
    values = sample().iloc[:2].copy()
    values.index = pd.to_datetime(['2026-01-05', '2026-01-12'])
    assert not M.inversion_ends(values).any()
