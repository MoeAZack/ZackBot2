"""Synthetic causal timing and recovery checks for implied mining work."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v139 as M

PLANTED_MUTANTS = [
    ('ignore_publication_delay', 'recovered & available.le(decision)', 'recovered',
     'test_every_contributing_record_must_be_available'),
    ('remove_stress_condition', ' & stressed', '', 'test_recovery_requires_material_prior_stress'),
    ('count_ongoing_above_reference', 'prior.lt(1.) & ', '', 'test_crossing_fires_once_with_two_day_buffer'),
]


def setup_series(low):
    days = pd.date_range('2024-01-01', periods=65)
    hashrate = pd.Series([100.] * 45 + [low] * 6 + [110.] * 14, index=days)
    completion = pd.Series([(day + pd.Timedelta(hours=28)).value // 10**9 for day in days], index=days)
    return days, hashrate, completion


def test_crossing_fires_once_with_two_day_buffer():
    days, hashrate, completion = setup_series(90.)
    signals = M.mining_recovery(hashrate, completion)
    assert signals[signals].index.tolist() == [days[56]]
    for n in (48, 55, 57):
        pd.testing.assert_series_equal(M.mining_recovery(hashrate.iloc[:n], completion.iloc[:n]), signals.iloc[:n])


def test_recovery_requires_material_prior_stress():
    _, hashrate, completion = setup_series(99.)
    assert not M.mining_recovery(hashrate, completion).any()


def test_every_contributing_record_must_be_available():
    days, hashrate, completion = setup_series(90.)
    completion.iloc[13] = (days[57] + pd.Timedelta(seconds=1)).value // 10**9
    assert not M.mining_recovery(hashrate, completion).any()
    completion.iloc[13] -= 1
    assert M.mining_recovery(hashrate, completion).loc[days[56]]
