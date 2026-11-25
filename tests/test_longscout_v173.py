"""Synthetic tests of Treasury pairing, event selection and publication lag."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v173 as M

PLANTED_MUTANTS = [
    ('allow_parallel_easing', ' & shift.DFII10.abs().le(5)', '', 'test_long_end_must_stay_stable'),
    ('enable_prepublication_event', 'pd.Timedelta(days=3)', 'pd.Timedelta(days=0)', 'test_delays_daily_decision'),
    ('accept_stale_observations', 'elapsed.between(3, 8)', 'elapsed.ge(3)', 'test_rejects_stale_sessions'),
]


def sample():
    return pd.DataFrame({'DFII5': [2., 2., 2., 2., 1.85, 1.84, 1.81], 'DFII10': 2.0},
                        index=pd.date_range('2026-01-05', periods=7, freq='B'))


def test_long_end_must_stay_stable():
    yields = sample()
    yields.loc[yields.index[4:], 'DFII10'] = 1.9
    assert not M.real_curve_steepening(yields).any()


def test_delays_daily_decision():
    yields = sample()
    event = M.real_curve_steepening(yields)
    assert event[event].index.tolist() == [yields.index[4] + pd.Timedelta(days=3)]
    assert not M.real_curve_steepening(yields.iloc[:4]).any()


def test_rejects_stale_sessions():
    yields = sample()
    yields.index = yields.index[:4].append(yields.index[4:] + pd.Timedelta(days=10))
    assert not M.real_curve_steepening(yields).any()
