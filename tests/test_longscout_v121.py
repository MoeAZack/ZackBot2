"""Synthetic threshold and gap checks for the relative stablecoin input."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v121 as M

PLANTED_MUTANTS = [
    ('consider_only_premium_side', '(spot - 1).abs()', '(spot - 1)',
     'test_positive_and_negative_relative_moves'),
    ('misalign_previous_observation', 'previous = distance_bp.shift()', 'previous = distance_bp.shift(-1)',
     'test_positive_and_negative_relative_moves'),
    ('ignore_coin_price_filter', ' & d.c.diff().gt(0).to_numpy()', '',
     'test_coin_direction_and_btc_eligibility'),
]


def test_positive_and_negative_relative_moves():
    rates = pd.Series([1., 1.002, 1.001, 1., .998, .9995, 1.])
    assert np.flatnonzero(M.normalization(rates)).tolist() == [2, 5]
    for n in range(1, len(rates) + 1):
        pd.testing.assert_series_equal(M.normalization(rates.iloc[:n]), M.normalization(rates).iloc[:n])


def test_gap_never_bridges_and_threshold_is_inclusive():
    rates = pd.Series([1.0015, 1.0010, 1.002, np.nan, 1., .9985, .9990])
    assert np.flatnonzero(M.normalization(rates)).tolist() == [1, 6]


def test_coin_direction_and_btc_eligibility(monkeypatch):
    t = pd.date_range('2026-01-01', periods=7, freq='4h')
    btc = pd.DataFrame(dict(t=t, c=[100., 101, 102, 103, 104, 105, 106]))
    spot = pd.Series([1., 1.002, 1.001, 1., .998, .9995, 1.], index=t)
    monkeypatch.setattr(M, 'load_spot', lambda: spot)
    monkeypatch.setattr(M.L1, 'coin_regime', lambda d: np.zeros(len(d)))
    M.set_btc(btc)
    coin = btc.copy()
    coin.loc[5, 'c'] = 99.
    assert np.flatnonzero(M.sig_stable_recovery(coin, {}, {})['le']).tolist() == [2]
    monkeypatch.setattr(M.L1, 'coin_regime', lambda d: np.full(len(d), -1.))
    M.set_btc(btc)
    assert not M.sig_stable_recovery(coin, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
