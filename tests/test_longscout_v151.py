"""Synthetic tests for seasonality, complete bars and turnover isolation."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v151 as M

PLANTED_MUTANTS = [
    ('use_unadjusted_activity', 'counts / expected', 'counts',
     'test_utc_slot_norm_removes_routine_intraday_pattern'),
    ('allow_overall_turnover_surge', ' & settled', '', 'test_active_dollar_turnover_blocks_entry'),
    ('count_current_observation_in_slot_history', 'slot.shift().rolling(42, min_periods=42).median()',
     'slot.rolling(42, min_periods=42).median()', 'test_current_observation_cannot_create_historical_coverage'),
]


def sample():
    times = pd.date_range('2024-01-01', periods=100 * 6, freq='4h')
    slot = np.tile([9000, 400, 3000, 1600, 2400, 400], 100)
    counts = pd.Series(slot.astype(float), index=times)
    counts.iloc[90 * 6] *= 6
    turnover = pd.Series(100., index=times)
    return times, counts, turnover


def test_utc_slot_norm_removes_routine_intraday_pattern():
    times, count, turnover = sample()
    assert M.arrival_shock(count, turnover).loc[times[90 * 6]]
    count.iloc[90 * 6] /= 6
    assert not M.arrival_shock(count, turnover).any()


def test_active_dollar_turnover_blocks_entry():
    _, count, turnover = sample()
    turnover.iloc[90 * 6:91 * 6] *= 3
    assert not M.arrival_shock(count, turnover).any()


def test_current_observation_cannot_create_historical_coverage():
    times, count, turnover = sample()
    count.iloc[90 * 6] /= 6
    count.iloc[72 * 6] *= 6
    assert not M.arrival_shock(count, turnover).loc[times[72 * 6]]
