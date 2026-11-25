"""Synthetic checks of causal repo tail relief and release delay."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v175 as M

PLANTED_MUTANTS = [
    ('allow_typical_rate_to_move', ' & median.diff().abs().le(2)', '', 'test_typical_rate_must_be_stable'),
    ('allow_missing_historical_session', 'rolling(20, min_periods=20)', 'rolling(20, min_periods=19)',
     'test_full_baseline_required'),
    ('allow_unreleased_rate', 'pd.Timedelta(days=4)', 'pd.Timedelta(days=0)', 'test_delays_event'),
]


def sample():
    return pd.DataFrame({'median': 4., 'upper': [4.04] * 20 + [4.08, 4.04]},
                        index=pd.date_range('2026-01-05', periods=22, freq='B'))


def test_typical_rate_must_be_stable():
    rates = sample()
    rates.iloc[-1] = [3.90, 3.94]
    assert not M.tail_eases(rates).any()


def test_full_baseline_required():
    rates = sample()
    rates.iloc[7] = [float('nan'), float('nan')]
    assert not M.tail_eases(rates).any()


def test_delays_event():
    rates = sample()
    events = M.tail_eases(rates)
    assert events[events].index.tolist() == [rates.index[-1] + pd.Timedelta(days=4)]
    assert not M.tail_eases(rates.iloc[:-1]).any()
