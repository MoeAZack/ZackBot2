"""Synthetic tests of causal regime inference and masked-data reset."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v148 as M

PLANTED_MUTANTS = [
    ('skip_range_emission', ' * emission', '', 'test_filter_uses_observable_direction_and_scale'),
    ('read_future_observation', 'enumerate(observed)', 'enumerate(observed.shift(-1))',
     'test_future_rows_cannot_relabel_filtered_regime'),
    ('carry_state_across_missing_bar', '            posterior = PRIOR.copy()\n            continue',
     '            continue', 'test_missing_observation_resets_regime_prior'),
]


def test_filter_uses_observable_direction_and_scale():
    calm = M.filter_states(pd.Series([0.] * 12)).iloc[-1]
    upward = M.filter_states(pd.Series([1.7] * 12)).iloc[-1]
    downward = M.filter_states(pd.Series([-1.7] * 12)).iloc[-1]
    assert calm['range'] > .8
    assert upward['up'] > .8 and downward['down'] > .8
    np.testing.assert_allclose([calm.sum(), upward.sum(), downward.sum()], 1.)


def test_future_rows_cannot_relabel_filtered_regime():
    observed = pd.Series([0., .3, -.9, 1., .1, .2, -4., 2.])
    whole = M.filter_states(observed)
    for prefix in (3, 5, 7):
        pd.testing.assert_frame_equal(M.filter_states(observed.iloc[:prefix]), whole.iloc[:prefix])


def test_missing_observation_resets_regime_prior():
    observed = M.filter_states(pd.Series([2.] * 12 + [float('nan'), 0.]))
    assert observed.iloc[-2].isna().all()
    pd.testing.assert_series_equal(observed.iloc[-1], M.filter_states(pd.Series([0.])).iloc[-1], check_names=False)
