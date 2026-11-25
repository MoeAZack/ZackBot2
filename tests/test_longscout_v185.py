"""Synthetic checks of volatility isolation, publication and calendar gaps."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v185 as M

PLANTED_MUTANTS = [
    ('allow_vix_shock', ' & vix_change.le(.04)', '', 'test_vix_must_stay_stable'),
    ('ignore_old_session', '.dt.days.between(1, 4)', '.dt.days.ge(1)', 'test_rejects_stale_session'),
    ('ignore_utc_day_buffer', 'pd.Timedelta(days=1)', 'pd.Timedelta(days=0)', 'test_delay_and_prefix'),
]


def sample():
    return pd.DataFrame({'VVIX': [100.] * 30 + [114., 103.], 'VIX': 20.},
                        index=pd.date_range('2026-01-05', periods=32, freq='B'))


def test_vix_must_stay_stable():
    quotes = sample()
    quotes.iloc[-1, 1] = 23.
    assert not M.volatility_uncertainty_settles(quotes).any()


def test_rejects_stale_session():
    quotes = sample()
    quotes.index = quotes.index[:-1].append(quotes.index[-1:] + pd.Timedelta(days=9))
    assert not M.volatility_uncertainty_settles(quotes).any()


def test_delay_and_prefix():
    quotes = sample()
    result = M.volatility_uncertainty_settles(quotes)
    assert result[result].index.to_list() == [quotes.index[-1] + pd.Timedelta(days=1)]
    assert not M.volatility_uncertainty_settles(quotes.iloc[:-1]).any()
