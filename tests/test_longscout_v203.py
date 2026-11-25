"""Synthetic tests for sorted quantile changes, overlap and incomplete inputs."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v203 as M

PLANTED_MUTANTS = [
    ('ignore_lower_tail', '(lower >= .004) & ', '', 'test_needs_actual_lower_tail_improvement'),
    ('ignore_upper_tail', ' & (upper <= .004)', '', 'test_rejects_broad_distribution_shift'),
    ('overlap_comparison_blocks', 'pairs[complete, :WINDOW]', 'pairs[complete, WINDOW:]', 'test_buys_lower_tail_recovery'),
]


def sample(lower=-.012, middle=.002, upper=.020):
    prior = [-.025, -.002, .002, .020] * 6
    recent = [upper, middle, middle, lower] * 6
    return pd.Series(prior + recent)


def test_buys_lower_tail_recovery():
    values = sample()
    assert M.lower_tail_recovers(values)[lambda x: x].index.tolist() == [47]
    assert not M.lower_tail_recovers(values.iloc[:47]).any()


def test_needs_actual_lower_tail_improvement():
    assert not M.lower_tail_recovers(sample(lower=-.023)).any()


def test_rejects_broad_distribution_shift():
    assert not M.lower_tail_recovers(sample(upper=.030)).any()


def test_rejects_missing_daily_return():
    values = sample()
    values.iloc[20] = float('nan')
    assert not M.lower_tail_recovers(values).any()


def test_handles_complete_window_missing_every_return():
    assert not M.lower_tail_recovers(pd.Series([float('nan')] * 70)).any()
