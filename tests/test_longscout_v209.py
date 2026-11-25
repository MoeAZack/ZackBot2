"""Synthetic tests for paired flow, timing and sequence."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v209 as M

PLANTED_MUTANTS = [
    ('let_futures_lead_initially', 'spot.ge(.10) & perpetual.le(-.05)', 'spot.ge(.10)', 'test_requires_spot_to_lead'),
    ('accept_no_derivatives_confirmation', 'spot.ge(.02) & perpetual.ge(.05)', 'spot.ge(.02)', 'test_requires_perpetual_confirmation'),
    ('omit_initial_aggressor_state', 'both_soft.shift(2, fill_value=False) & ', '', 'test_requires_identified_onset'),
]


def signal(spot=(-.04, .11, .03), perp=(-.03, -.10, .06)):
    return M.spot_lead_confirmed(pd.Series(spot), pd.Series(perp))


def test_waits_for_sequential_confirmation():
    assert signal().tolist() == [False, False, True]


def test_requires_spot_to_lead():
    assert not signal(perp=(-.03, .07, .06)).any()


def test_requires_perpetual_confirmation():
    assert not signal(perp=(-.03, -.10, .01)).any()


def test_requires_identified_onset():
    assert not signal(spot=(.13, .11, .03)).any()


def test_incomplete_paired_trade_volume():
    bad = M.imbalance(pd.Series([0., 100., float('nan')]), pd.Series([0., 150., 25.]))
    assert bad.isna().all()
    assert not signal(spot=(-.04, float('nan'), .03)).any()
