"""Synthetic checks of normalized block-gap tail and timestamp buffer."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v183 as M

PLANTED_MUTANTS = [
    ('allow_changed_block_count', ' & stable_count', '', 'test_blocks_must_stay_near_normal'),
    ('allow_missing_baseline', 'benchmark = share.shift(2).rolling(30, min_periods=30).median()',
     'benchmark = share.shift(2).rolling(30, min_periods=29).median()', 'test_requires_full_gap_baseline'),
    ('remove_timestamp_buffer', 'return relief.shift(2, fill_value=False)', 'return relief', 'test_uses_header_buffer'),
]


def sample():
    return pd.DataFrame({'block_count': 145., 'absolute_header_gap_at_least_30m': [7.] * 30 + [13., 6., 7., 7.]})


def test_blocks_must_stay_near_normal():
    rows = sample()
    rows.iloc[31, 0] = 190.
    assert not M.long_gaps_normalize(rows).any()


def test_requires_full_gap_baseline():
    rows = sample()
    rows.iloc[10, 1] = float('nan')
    assert not M.long_gaps_normalize(rows).any()


def test_uses_header_buffer():
    rows = sample()
    flag = M.long_gaps_normalize(rows)
    assert flag[flag].index.tolist() == [33]
    assert not M.long_gaps_normalize(rows.iloc[:33]).any()
