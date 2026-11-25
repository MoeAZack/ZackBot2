"""Synthetic parity, causal order and futures eligibility checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v132 as M

PLANTED_MUTANTS = [
    ('ignore_negative_residuals', 'distance = residual.abs()', 'distance = residual',
     'test_convergence_uses_both_residual_signs'),
    ('allow_no_contraction', 'distance.le(.5 * previous)', 'distance.le(previous)',
     'test_convergence_uses_both_residual_signs'),
    ('ignore_btc_price_confirmation', ' & price.diff().gt(0)', '',
     'test_price_direction_and_cohort_eligibility'),
]


def test_convergence_uses_both_residual_signs():
    values = pd.Series([0, .002, .0006, -.003, -.001, .003, .002, .0015])
    assert np.flatnonzero(M.convergence(values)).tolist() == [2, 4]
    for n in (1, 2, 3, 5, 7):
        pd.testing.assert_series_equal(M.convergence(values.iloc[:n]), M.convergence(values).iloc[:n])


def test_missing_previous_observation_is_unavailable():
    values = pd.Series([0, .001, .0005, .003, np.nan, .0001])
    assert np.flatnonzero(M.convergence(values)).tolist() == [2]


def test_price_direction_and_cohort_eligibility(monkeypatch):
    dates = pd.date_range('2024-01-01', periods=6, freq='4h')
    btc = pd.DataFrame(dict(t=dates, c=[100., 101, 102, 104, 103, 105]))
    coin = btc.assign(c=[90., 91, 92, 93, 94, 95])
    residual = pd.Series([0, -.002, -.001, .003, .001, .002], index=dates)
    monkeypatch.setattr(M, 'load_residuals', lambda: residual)
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.zeros(len(frame)))
    M.set_btc(btc)
    assert np.flatnonzero(M.sig_triangular_close(coin, {}, {})['le']).tolist() == [2]
    monkeypatch.setattr(M.L1, 'coin_regime', lambda frame: np.full(len(frame), -1.))
    assert not M.sig_triangular_close(coin, {}, {})['le'].any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
