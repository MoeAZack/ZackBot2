"""Synthetic tests of leave-one-coin-out response and temporal gating."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v160 as M

PLANTED_MUTANTS = [
    ('include_target_coin_in_training', 'past.rsub(past.sum(axis=1), axis=0)',
     'past.mul(0).add(past.sum(axis=1), axis=0)', 'test_target_own_labels_do_not_enter_its_fit'),
    ('use_unmatured_response', 'values.shift().rolling(60, min_periods=60)',
     'values.rolling(60, min_periods=60)', 'test_same_row_label_is_not_available'),
    ('ignore_required_reversal_slope', ' & slope.le(-.15)', '', 'test_position_must_have_learned_reversal'),
]


def panel():
    rng = np.random.default_rng(161)
    x = pd.DataFrame(rng.uniform(-.04, .04, (100, 15)))
    return x, -.4 * x, x.notna()


def test_target_own_labels_do_not_enter_its_fit():
    x, y, mask = panel()
    before = M.peer_response(x, y, mask)[0]
    y[0] = x[0] * 3.
    after = M.peer_response(x, y, mask)[0]
    np.testing.assert_allclose(before[0], after[0], atol=1e-12)
    assert not np.isclose(before[1].iloc[-1], after[1].iloc[-1])


def test_same_row_label_is_not_available():
    x, y, mask = panel()
    before = M.peer_response(x, y, mask)[0]
    y.iloc[95, :] = .18
    after = M.peer_response(x, y, mask)[0]
    pd.testing.assert_frame_equal(before.iloc[:96], after.iloc[:96])
    assert not np.isclose(before.iloc[96, 0], after.iloc[96, 0])


def test_position_must_have_learned_reversal():
    x = pd.DataFrame([[-.04, -.04, .04, .04]])
    slope = pd.DataFrame([[-.4, -.01, -.4, -.01]])
    intercept = pd.DataFrame([[0., .02, 0., -.02]])
    buy, sell = M.range_decisions(x, slope, intercept, x.notna())
    assert buy.iloc[0].tolist() == [True, False, False, False]
    assert sell.iloc[0].tolist() == [False, False, True, False]
