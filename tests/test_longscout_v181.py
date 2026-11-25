"""Synthetic tests of isolated Lido market relief and matched timing."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v181 as M

PLANTED_MUTANTS = [
    ('ignore_rocket_market', ' & rocket_stable', '', 'test_reth_stays_near_its_own_level'),
    ('allow_nominal_steth_break_above_par', ' & steth.lt(.998)', '', 'test_discount_remains_on_recovery'),
    ('trade_without_buffer', 'return relief.shift(1, fill_value=False)', 'return relief', 'test_delay_and_mark_prefix'),
]


def sample():
    return pd.DataFrame({'ETH': 1000., 'rETH': 1080., 'stETH': [1000.] * 61 + [980., 990., 990.]})


def test_reth_stays_near_its_own_level():
    marks = sample()
    marks.loc[61:, 'rETH'] = 1060.
    assert not M.isolated_discount_eases(marks).any()


def test_discount_remains_on_recovery():
    marks = sample()
    marks.loc[62:, 'stETH'] = 1002.
    assert not M.isolated_discount_eases(marks).any()


def test_delay_and_mark_prefix():
    marks = sample()
    result = M.isolated_discount_eases(marks)
    assert result[result].index.tolist() == [63]
    assert not M.isolated_discount_eases(marks.iloc[:63]).any()
