"""Synthetic tests for price forecast, missing history and data availability."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v202 as M

PLANTED_MUTANTS = [
    ('use_current_change_in_forecast', 'hist_change = paired_change.shift(1)', 'hist_change = paired_change', 'test_excludes_current_change'),
    ('ignore_previous_fear', 'index.shift().le(35) & ', '', 'test_requires_previous_fear'),
    ('ignore_release_delay', 'return pulse.shift(2, fill_value=False)', 'return pulse', 'test_excludes_current_change'),
]


def sample(level=25.):
    index = pd.Series([level] * 70 + [level + 9] * 3)
    price_return = pd.Series([-.01, .01] * 36 + [-.01])
    return index, price_return


def test_excludes_current_change():
    values, price_return = sample()
    signal = M.fear_relief(values, price_return)
    assert signal[signal].index.tolist() == [72]
    assert not M.fear_relief(values.iloc[:72], price_return.iloc[:72]).any()


def test_requires_previous_fear():
    values, price_return = sample(40.)
    assert not M.fear_relief(values, price_return).any()


def test_rejects_price_fit_with_missing_pair():
    values, price_return = sample()
    price_return.iloc[50] = float('nan')
    assert not M.fear_relief(values, price_return).any()


def test_rejects_constant_price_predictor():
    values, price_return = sample()
    price_return[:] = .001
    assert not M.fear_relief(values, price_return).any()
