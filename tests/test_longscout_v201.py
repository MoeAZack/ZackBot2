"""Synthetic checks of ratio recovery, turnover and delayed use."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v201 as M

PLANTED_MUTANTS = [
    ('allow_turnover_shock', ' & stable_volume', '', 'test_volume_must_hold'),
    ('ignore_prior_fee_elevation', 'effective.shift().ge(1.25 * median_fee) & ', '', 'test_requires_prior_fee_elevation'),
    ('ignore_utc_delay', 'return pulse.shift(2, fill_value=False)', 'return pulse', 'test_uses_prior_intervals'),
]


def sample():
    return pd.DataFrame({'ethereum_swap_fees_usd': [1.] * 32 + [1.50, 1., 1., 1.],
                         'ethereum_swap_volume_usd': 1000.})


def test_volume_must_hold():
    values = sample()
    values.iloc[33, 1] = 1500.
    assert not M.effective_fees_ease(values).any()


def test_requires_prior_fee_elevation():
    values = sample()
    values.iloc[32, 0] = 1.
    assert not M.effective_fees_ease(values).any()


def test_uses_prior_intervals():
    values = sample()
    flags = M.effective_fees_ease(values)
    assert flags[flags].index.tolist() == [35]
    assert not M.effective_fees_ease(values.iloc[:35]).any()
