"""Synthetic effective-rank and factor-panel checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v154 as M

PLANTED_MUTANTS = [
    ('skip_return_standardization', 'centered / scale', 'centered', 'test_one_scaled_factor_has_rank_one'),
    ('include_current_close_in_risk_fit', 'history[current-40:current]', 'history[current-39:current+1]',
     'test_current_close_does_not_change_factor_inference'),
    ('allow_thin_matched_panel', 'len(eligible) < 48', 'len(eligible) < 47',
     'test_matched_panel_needs_48_contracts'),
]


def paths(width=48):
    rng = np.random.default_rng(106)
    common = pd.DataFrame(np.repeat(np.sin(np.arange(42))[:, None], width, axis=1))
    noise = pd.DataFrame(rng.normal(0., 1., size=(32, width)), index=range(42, 74))
    residuals = pd.concat([common, noise])
    liquidity = pd.DataFrame(2e7, index=residuals.index, columns=residuals.columns)
    return residuals, liquidity


def test_one_scaled_factor_has_rank_one():
    a, b = np.sin(np.arange(20) * np.pi / 10), np.cos(np.arange(20) * np.pi / 10)
    assert np.isclose(M.effective_rank(np.column_stack([a, a * 100, a * .01])), 1.)
    assert np.isclose(M.effective_rank(np.column_stack([a, b * 100])), 2.)


def test_current_close_does_not_change_factor_inference():
    residuals, liquidity = paths()
    base = M.factor_diffusion(residuals, liquidity)
    day = base[base].index[0]
    residuals.iloc[day, :] = np.nan
    changed = M.factor_diffusion(residuals, liquidity)
    pd.testing.assert_series_equal(base.iloc[:day+1], changed.iloc[:day+1])


def test_matched_panel_needs_48_contracts():
    residuals, liquidity = paths(47)
    assert not M.factor_diffusion(residuals, liquidity).any()
