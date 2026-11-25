"""Synthetic lead-direction, co-movement and causal timestamp checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v147 as M

PLANTED_MUTANTS = [
    ('count_raw_correlation_as_price_discovery', '(raw - target_own * suggested_own) / np.sqrt(denom.where(denom.gt(1e-6)))',
     'raw', 'test_comovement_without_incremental_information_does_not_qualify'),
    ('fit_through_decision_bar', 'partial.clip(-1., 1.).shift()', 'partial.clip(-1., 1.)',
     'test_current_bar_cannot_change_fitted_lead'),
    ('accept_reverse_discovery', 'directional_lead(perpetual, spot)', 'directional_lead(spot, perpetual)',
     'test_directional_source_must_lead'),
]


def direct_lead():
    rng = np.random.default_rng(21)
    spot = pd.Series(rng.normal(0, .015, 220))
    perpetual = .75 * spot.shift() + pd.Series(rng.normal(0, .006, 220))
    return spot, perpetual


def test_directional_source_must_lead():
    spot, perpetual = direct_lead()
    spot.iloc[-1] = .02
    perpetual.iloc[-1] = .001
    assert M.spot_confirmation(spot, perpetual).iloc[-1]
    assert not M.spot_confirmation(perpetual, spot).iloc[-1]


def test_current_bar_cannot_change_fitted_lead():
    spot, perpetual = direct_lead()
    before = M.directional_lead(perpetual, spot)
    perpetual.iloc[205] = .8
    after = M.directional_lead(perpetual, spot)
    assert before.iloc[205] == after.iloc[205]
    assert not np.isclose(before.iloc[206], after.iloc[206])


def test_comovement_without_incremental_information_does_not_qualify():
    rng = np.random.default_rng(18)
    target = pd.Series(np.zeros(220))
    for i in range(1, len(target)):
        target.iloc[i] = .9 * target.iloc[i-1] + rng.normal(0, .1)
    predictor = target + pd.Series(rng.normal(0, .04, 220))
    effect = M.directional_lead(target, predictor).iloc[-1]
    assert abs(effect) < .20
