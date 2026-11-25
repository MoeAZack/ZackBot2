"""Synthetic checks of issuer premium, spot-fix direction and timing."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v206 as M

PLANTED_MUTANTS = [
    ('ignore_fix_drift', ' & opposes_fix_drift', '', 'test_requires_opposing_fix_drift'),
    ('ignore_actual_premium', ' & frame.ibit_nav_premium_pct.ge(.15)', '', 'test_requires_positive_premium'),
    ('use_close_session_date', 'pulse.set_axis(pulse.index + pd.Timedelta(days=2))', 'pulse', 'test_waits_two_utc_days'),
]


def sample(premium=.15, close=99.9, vwap=100.):
    return pd.DataFrame([[premium, close, vwap]], columns=M.FIELDS,
                        index=pd.to_datetime(['2026-03-06']))


def test_requires_opposing_fix_drift():
    assert not M.premium_disagrees(sample(close=100.1)).any()


def test_requires_positive_premium():
    assert not M.premium_disagrees(sample(premium=.14)).any()
    assert not M.premium_disagrees(sample(premium=-.40)).any()


def test_waits_two_utc_days():
    flags = M.premium_disagrees(sample())
    assert flags[flags].index.tolist() == [pd.Timestamp('2026-03-08')]


def test_rejects_invalid_spot_price():
    assert not M.premium_disagrees(sample(close=0.)).any()
    assert not M.premium_disagrees(sample(vwap=float('inf'))).any()
