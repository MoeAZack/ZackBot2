"""Synthetic implied-volatility units and causal volatility-spread tests."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v127 as M

PLANTED_MUTANTS = [
    ('skip_implied_contraction', ' & implied.diff().le(-.02)', '',
     'test_rising_realized_vol_alone_is_not_relief'),
    ('accept_a_falling_price', ' & ret.gt(0)', ' & ret.lt(0)',
     'test_positive_close_and_implied_contraction_required'),
    ('fill_missing_implied_volatility', 'percent.reindex(price.index) / 100',
     'percent.reindex(price.index).ffill() / 100', 'test_missing_input_and_prefix'),
]


def fixture():
    dates = pd.date_range('2024-01-01', periods=48, freq='D')
    close = pd.Series(100*np.exp(.001*np.arange(len(dates))), index=dates)
    iv = pd.Series(60., index=dates)
    iv.iloc[40:] = 50
    return close, iv


def test_positive_close_and_implied_contraction_required():
    close, iv = fixture()
    assert np.flatnonzero(M.vol_recovery(close, iv)).tolist() == [40]
    close.iloc[40] *= .99
    assert not M.vol_recovery(close, iv).any()


def test_rising_realized_vol_alone_is_not_relief():
    close, iv = fixture()
    close.iloc[40:] *= 1.03
    iv.iloc[:] = 60
    assert not M.vol_recovery(close, iv).any()


def test_missing_input_and_prefix():
    close, iv = fixture()
    flags = M.vol_recovery(close, iv)
    for stop in (30, 40, 41, 44):
        pd.testing.assert_series_equal(M.vol_recovery(close.iloc[:stop], iv.iloc[:stop]), flags.iloc[:stop])
    iv.iloc[39] = np.nan
    assert not M.vol_recovery(close, iv).any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
