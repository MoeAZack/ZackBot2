"""Synthetic checks for opposing quantity changes and causal coverage."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v144 as M

PLANTED_MUTANTS = [
    ('ignore_sol_expansion', ' & change.SOLUSDT.ge(.02)', '', 'test_both_alt_contracts_must_expand'),
    ('count_missing_intermediate_as_valid', 'rolling(4, min_periods=4).sum().eq(4)',
     'rolling(4, min_periods=4).sum().ge(3)', 'test_no_observation_gap_in_transition'),
    ('repeat_ongoing_rotation', ' & ~state.shift(fill_value=False)', '', 'test_rotation_is_only_first_observed_day'),
]


def quantities():
    return pd.DataFrame({'BTCUSDT': [100., 100, 100, 100, 97, 96, 95],
                         'ETHUSDT': [300., 300, 300, 300, 309, 312, 315],
                         'SOLUSDT': [1000., 1000, 1000, 1000, 1030, 1040, 1050]})


def test_rotation_is_only_first_observed_day():
    data = quantities()
    signal = M.rotation(data)
    assert signal[signal].index.tolist() == [4]
    pd.testing.assert_series_equal(M.rotation(data.iloc[:4]), signal.iloc[:4])


def test_both_alt_contracts_must_expand():
    data = quantities()
    data['SOLUSDT'] = 1000.
    assert not M.rotation(data).any()


def test_no_observation_gap_in_transition():
    data = quantities()
    data.loc[2, 'ETHUSDT'] = float('nan')
    assert not M.rotation(data).any()
