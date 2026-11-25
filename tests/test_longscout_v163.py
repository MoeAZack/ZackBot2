"""Synthetic checks for bid resilience and displayed ask control."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v163 as M

PLANTED_MUTANTS = [
    ('count_two_sided_book_growth', ' & ask_change.le(.05)', '', 'test_ask_growth_blocks_the_bid_signal'),
    ('count_one_sell_day', ' & sells.shift(fill_value=False)', '', 'test_selling_must_persist'),
    ('reenter_persistent_conditions', ' & ~state.shift(fill_value=False)', '', 'test_bid_growth_signals_only_once'),
]


def sample():
    return pd.Series([100., 100., 121., 148.]), pd.Series([100., 100., 101., 103.]), pd.Series([0., -.2, -.2, -.2])


def test_bid_growth_signals_only_once():
    bid, ask, imbalance = sample()
    event = M.resilient_bid(bid, ask, imbalance)
    assert event[event].index.tolist() == [2]
    pd.testing.assert_series_equal(M.resilient_bid(bid.iloc[:3], ask.iloc[:3], imbalance.iloc[:3]), event.iloc[:3])


def test_ask_growth_blocks_the_bid_signal():
    bid, _, imbalance = sample()
    assert not M.resilient_bid(bid, bid, imbalance).any()


def test_selling_must_persist():
    bid, ask, imbalance = sample()
    imbalance.iloc[1] = 0.
    assert not M.resilient_bid(bid.iloc[:3], ask.iloc[:3], imbalance.iloc[:3]).any()
