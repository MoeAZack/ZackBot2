"""Synthetic checks of DEX turnover rotation and decision lag."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v178 as M

PLANTED_MUTANTS = [
    ('allow_optimism_stagnation', ' & latest.Optimism.ge(1.20 * typical.Optimism)', '', 'test_each_rollup_participates'),
    ('allow_market_total_to_shock', ' & (latest_total / total_normal).between(.85, 1.15)', '',
     'test_total_must_stay_stable'),
    ('use_same_day_volume', 'return event.shift(2, fill_value=False)', 'return event', 'test_two_day_delay'),
]


def sample():
    return pd.DataFrame([[80., 15., 5.]] * 32 + [[65., 23., 12.]] * 8, columns=M.CHAINS)


def test_each_rollup_participates():
    volume = sample()
    volume.iloc[32:] = [65., 30., 5.]
    assert not M.rotation(volume).any()


def test_total_must_stay_stable():
    volume = sample()
    volume.iloc[32:] = [80., 50., 30.]
    assert not M.rotation(volume).any()


def test_two_day_delay():
    volume = sample()
    fixed = M.rotation(volume)
    assert fixed[fixed].index.tolist() == [35]
    assert not M.rotation(volume.iloc[:35]).any()
