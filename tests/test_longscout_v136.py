"""Synthetic sign, coverage and causality checks for BTC aggressor flow."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v136 as M

PLANTED_MUTANTS = [
    ('discard_prior_sell_run', 'previous.max().lt(0) & ', '',
     'test_every_bar_in_the_run_must_sell'),
    ('reverse_taker_sides', '2 * taker / total - 1', '1 - 2 * taker / total',
     'test_run_ends_on_the_next_observable_bar'),
    ('count_unobservable_flow', ' & taker.le(total)', '',
     'test_bad_or_missing_total_is_unavailable'),
]


def test_run_ends_on_the_next_observable_bar():
    taker = pd.Series([52., 40, 42, 43, 60, 65])
    total = pd.Series(100., index=taker.index)
    expected = M.flow_reversal(total, taker)
    assert np.flatnonzero(expected).tolist() == [4]
    for n in (2, 3, 5):
        pd.testing.assert_series_equal(M.flow_reversal(total.iloc[:n], taker.iloc[:n]), expected.iloc[:n])


def test_every_bar_in_the_run_must_sell():
    taker = pd.Series([25., 52., 25., 70.])
    total = pd.Series(100., index=taker.index)
    assert not M.flow_reversal(total, taker).any()


def test_bad_or_missing_total_is_unavailable():
    taker = pd.Series([40., 40., 40., 110.])
    total = pd.Series(100., index=taker.index)
    assert not M.flow_reversal(total, taker).any()
    taker.iloc[-1] = 60
    assert M.flow_reversal(total, taker).iloc[-1]
    total.iloc[1] = np.nan
    assert not M.flow_reversal(total, taker).any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
