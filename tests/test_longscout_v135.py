"""Synthetic controls for settlement alignment, stale events and unit conversion."""
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v135 as M

PLANTED_MUTANTS = [
    ('ignore_mismatched_settlement', ' & (np.abs(lt-it) <= 60_000)', '',
     'test_settlements_must_match'),
    ('omit_interval_conversion', 'signed[latest[fresh]] * 8 / hours[latest[fresh]]', 'signed[latest[fresh]]',
     'test_rates_compare_in_equal_time_units'),
    ('count_payment_at_same_close', "side='left'", "side='right'",
     'test_stale_and_not_yet_published_values_are_excluded'),
]


def events(times, rates, hours):
    return [(t*M.HOUR_MS, h, rate) for t, h, rate in zip(times, hours, rates, strict=True)]


def test_settlements_must_match():
    linear = events([0, 8, 16], [.00025]*3, [8]*3)
    inverse = events([2, 10, 18], [-.00025]*3, [8]*3)
    assert not M.divergence(np.array([20])*M.HOUR_MS, linear, inverse).any()


def test_rates_compare_in_equal_time_units():
    linear = events([0, 8, 16], [.00015]*3, [8]*3)
    inverse = events([4, 8, 12, 16], [-.00009]*4, [4]*4)
    assert M.divergence(np.array([19])*M.HOUR_MS, linear, inverse).tolist() == [True]


def test_stale_and_not_yet_published_values_are_excluded():
    linear = events([0, 8, 16], [.00025, .00025, -.00025], [8]*3)
    inverse = events([0, 8, 16], [-.0002]*3, [8]*3)
    ts = np.array([8, 12, 16, 20, 28])*M.HOUR_MS
    assert M.divergence(ts, linear, inverse).tolist() == [False, True, True, False, False]
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'long'
