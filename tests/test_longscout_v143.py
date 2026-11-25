"""Checks timestamp causality, synchronized coverage and two-sided carry exits."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v143 as M

PLANTED_MUTANTS = [
    ('read_unavailable_settlement', "side='left'", "side='right'", 'test_funding_is_strictly_observed_and_normalized'),
    ('allow_thin_peer_panel', '.ge(60)', '.ge(59)', 'test_rank_requires_synchronized_panel'),
    ('enter_again_while_inside', ' & previous.le(.10)', '', 'test_fade_only_after_leaving_a_tail'),
]


def test_funding_is_strictly_observed_and_normalized():
    h = M.L117.HOUR_MS
    funding = pd.DataFrame(dict(calc_time=[0, 4*h, 12*h], funding_interval_hours=[4, 4, 8],
                                last_funding_rate=[.01, .02, .03]))
    values = M.observed_funding(np.array([4*h, 4*h + 1, 8*h, 10*h, 12*h, 12*h + 1]), funding)
    np.testing.assert_allclose(values, [np.nan, .04, .04, np.nan, np.nan, .03])


def test_rank_requires_synchronized_panel():
    panel = pd.DataFrame([range(60), range(60)])
    panel.iloc[1, 0] = float('nan')
    rank = M.funding_rank(panel)
    assert rank.iloc[0].notna().all() and rank.iloc[1].isna().all()
    assert rank.iloc[0, -1] == 1.0


def test_fade_only_after_leaving_a_tail():
    rank = pd.Series([.05, .2, .3, .5, .95, .8, .7, .45, np.nan])
    long, short, exit_long, exit_short = M.range_flags(rank, rank.shift(), rank.notna())
    assert np.flatnonzero(long).tolist() == [1]
    assert np.flatnonzero(short).tolist() == [5]
    assert exit_long.iloc[3] and exit_short.iloc[7]
    assert exit_long.iloc[8] and exit_short.iloc[8]
