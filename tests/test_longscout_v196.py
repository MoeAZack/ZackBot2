"""Synthetic checks of resource controls and the fixed archival delay."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v196 as M

PLANTED_MUTANTS = [
    ('allow_more_physical_weight', ' & relative.block_weight.between(.95, 1.05)', '', 'test_weight_must_stay_stable'),
    ('allow_more_blocks', ' & relative.block_count.between(.90, 1.10)', '', 'test_block_count_must_stay_stable'),
    ('ignore_archive_pause', 'return pulse.shift(7, fill_value=False)', 'return pulse', 'test_uses_seven_day_delay'),
]


def sample():
    return pd.DataFrame({'block_weight': 1000., 'transaction_count': [100.] * 45 + [140.] * 17, 'block_count': 10.})


def test_weight_must_stay_stable():
    values = sample()
    values.loc[45:, 'block_weight'] = 1400.
    assert not M.transaction_density_rises(values).any()


def test_block_count_must_stay_stable():
    values = sample()
    values.loc[45:, 'block_count'] = 13.
    assert not M.transaction_density_rises(values).any()


def test_uses_seven_day_delay():
    values = sample()
    signal = M.transaction_density_rises(values)
    assert signal[signal].index.tolist() == [55]
    assert not M.transaction_density_rises(values.iloc[:55]).any()
