"""Synthetic tests for mutual-correlation admission and causal fit."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v213 as M

PLANTED_MUTANTS = [
    ('ignore_peer_pair_edge', ' & (values[ranked] >= .65)', '', 'test_requires_mutual_peer_correlation'),
    ('ignore_agreement_when_buying', 'return agree & (deviation <= -.02)', 'return (deviation <= -.02)',
     'test_requires_agreeing_peer_returns'),
    ('include_outlier_in_fit', 'training = market[i - WINDOW:i]', 'training = market[i - WINDOW + 1:i + 1]',
     'test_monday_signal_uses_only_prior_fit'),
]


def test_requires_mutual_peer_correlation():
    corr = np.array([[1., .9, .8], [.9, 1., .3], [.8, .3, 1.]])
    first, second = M.choose_neighbors(corr)
    assert first.tolist() == second.tolist() == [-1, -1, -1]


def test_uses_best_valid_second_peer():
    corr = np.array([[1., .91, .85, .72], [.91, 1., .50, .68], [.85, .50, 1., .9], [.72, .68, .9, 1.]])
    first, second = M.choose_neighbors(corr)
    assert (first[0], second[0]) == (1, 3)


def test_requires_agreeing_peer_returns():
    buy, sell = M.disagreement_flags(np.array([-.05]), np.array([.005]), np.array([-.005]))
    assert not (buy | sell).any()


def test_monday_signal_uses_only_prior_fit():
    dates = pd.date_range('2024-01-01', periods=51, freq='D')
    base = np.sin(np.arange(len(dates))) * .002
    returns = pd.DataFrame({'A': base.copy(), 'B': base.copy(), 'C': base.copy()}, index=dates)
    returns.iloc[49] = [-.09, .01, .011]
    liquid = pd.DataFrame(True, index=dates, columns=returns.columns)
    buy, sell = M.paired_dislocations(returns, liquid)
    assert buy.index[buy.A].tolist() == [pd.Timestamp('2024-02-19')]
    assert not sell.any(axis=None)


def test_both_sides():
    buy, sell = M.disagreement_flags(np.array([-.04, .05]), np.array([.001, .001]), np.array([.002, .002]))
    assert buy.tolist() == [True, False]
    assert sell.tolist() == [False, True]

def test_selects_runner_with_symbol_context():
    assert M.RUNNER == 'longscout_flow_run.py'
