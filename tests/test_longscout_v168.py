"""Synthetic tests for direction neutrality and matched market coverage."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v168 as M

PLANTED_MUTANTS = [
    ('accept_directional_market', ' & center.abs().le(.05)', '', 'test_typical_market_must_remain_neutral'),
    ('accept_partial_market', 'relative.notna().all(axis=1)', 'relative.notna().any(axis=1)',
     'test_missing_contract_blocks_the_shock'),
    ('ignore_absolute_disagreement', ' & width.ge(.25)', '', 'test_tiny_dispersion_is_not_a_shock'),
]


def sample():
    values = pd.DataFrame([[1., 2., 3., 4., 5.]] * 102)
    values.iloc[100:, 0] *= 1.3
    values.iloc[100:, 1] *= .8
    return values


def test_typical_market_must_remain_neutral():
    values = sample()
    event = M.disagreement_shock(values)
    assert event[event].index.tolist() == [100]
    values.iloc[100:, :] *= 1.2
    assert not M.disagreement_shock(values).any()


def test_missing_contract_blocks_the_shock():
    values = sample()
    values.iloc[100:, 4] = float('nan')
    assert not M.disagreement_shock(values).any()


def test_tiny_dispersion_is_not_a_shock():
    values = sample()
    values.iloc[:100:2, 0] = 1.01
    values.iloc[1:100:2, 0] = .99
    values.iloc[100:, 0] = 1.05
    values.iloc[100:, 1] = 1.99
    assert not M.disagreement_shock(values).any()
