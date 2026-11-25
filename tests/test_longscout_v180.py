"""Synthetic checks of spread identification and the causal benchmark."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v180 as M

PLANTED_MUTANTS = [
    ('treat_unknown_spread_as_zero', 'spread.le(.75 * benchmark)', 'spread.fillna(0.).le(.75 * benchmark)',
     'test_unidentified_spread_does_not_trade'),
    ('allow_volatility_to_shift', ' & vol_change.between(.80, 1.20)', '', 'test_volatility_must_be_comparable'),
    ('allow_sparse_baseline', 'min_periods=28', 'min_periods=27', 'test_minimum_observations_required'),
]


def sample():
    return pd.DataFrame({'negative_autocov_spread': [.001] * 28 + [.0016, .0007],
                         'realized_volatility': .005})


def test_unidentified_spread_does_not_trade():
    values = sample()
    values.iloc[-1, 0] = float('nan')
    assert not M.spread_compresses(values).any()


def test_volatility_must_be_comparable():
    values = sample()
    values.iloc[-1, 1] = .0065
    assert not M.spread_compresses(values).any()


def test_minimum_observations_required():
    values = sample()
    result = M.spread_compresses(values)
    assert result[result].index.tolist() == [29]
    assert not M.spread_compresses(values.iloc[1:]).any()
