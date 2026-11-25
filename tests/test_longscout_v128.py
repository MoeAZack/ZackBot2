"""Synthetic tests of paired reference alignment and trade-only extremes."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v128 as M

PLANTED_MUTANTS = [
    ('ignore_index_low', 'np.minimum(mark.low, index.low)', 'mark.low',
     'test_both_references_must_disagree_with_trade_low'),
    ('allow_unresolved_close', 'quality & aligned & downside.ge', 'quality & downside.ge',
     'test_close_and_opposite_extremes_must_be_consistent'),
    ('ignore_balanced_upward_wick', ' & upside.le(.5 * downside)', '',
     'test_close_and_opposite_extremes_must_be_consistent'),
]


def fixture():
    t = pd.date_range('2024-01-01', periods=8, freq='4h')
    ref = pd.DataFrame(dict(high=101., low=99., close=100.), index=t)
    trade = ref.copy()
    trade.iloc[4, 1] = 98.3
    return trade, ref, ref.copy()


def test_both_references_must_disagree_with_trade_low():
    trade, mark, idx = fixture()
    assert np.flatnonzero(M.traded_excess(trade, mark, idx)).tolist() == [4]
    idx.iloc[4, 1] = 98.31
    assert not M.traded_excess(trade, mark, idx).any()


def test_close_and_opposite_extremes_must_be_consistent():
    trade, mark, idx = fixture()
    trade.iloc[4, 2] = 99.7
    assert not M.traded_excess(trade, mark, idx).any()
    trade.iloc[4, 2] = 100
    trade.iloc[4, 0] = 101.8
    assert not M.traded_excess(trade, mark, idx).any()


def test_neither_missing_reference_nor_future_creates_a_signal():
    trade, mark, idx = fixture()
    whole = M.traded_excess(trade, mark, idx)
    for cut in (3, 4, 5, 7):
        pd.testing.assert_series_equal(whole.iloc[:cut], M.traded_excess(trade.iloc[:cut], mark.iloc[:cut], idx.iloc[:cut]))
    assert not M.traded_excess(trade, mark.drop(mark.index[4]), idx).any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
