"""Synthetic checks of paired funding breadth and leave-one-out decisions."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v195 as M

PLANTED_MUTANTS = [
    ('include_target_in_denominator', 'peers = paired.rsub(total, axis=0)',
     'peers = pd.DataFrame({symbol: total for symbol in paired})',
     'test_exact_leave_one_out_threshold'),
    ('count_unmatched_negative_settlements', 'before.le(-.0001) & paired', 'before.le(-.0001)',
     'test_missing_quote_does_not_create_fake_relief'),
    ('ignore_minimum_peer_count', 'peers.ge(60)', 'peers.ge(59)', 'test_needs_sixty_other_peers'),
]


def sample(width=60, prior=21, current=11):
    rates = pd.DataFrame(0., index=range(2), columns=['target', *[f'peer{i}' for i in range(width)]])
    rates.iloc[0, 1:prior+1] = -.00025
    rates.iloc[1, 1:current+1] = -.00025
    return rates


def test_exact_leave_one_out_threshold():
    rates = sample()
    flags = M.funding_pressure_eases(rates)
    assert flags.target.tolist() == [False, True]
    pd.testing.assert_series_equal(M.funding_pressure_eases(rates.iloc[:1]).target, flags.target.iloc[:1])


def test_missing_quote_does_not_create_fake_relief():
    rates = sample(width=90, prior=36, current=10)
    rates.iloc[1, 17:37] = np.nan
    assert not M.funding_pressure_eases(rates).target.any()


def test_needs_sixty_other_peers():
    rates = sample(width=59, prior=24, current=10)
    assert not M.funding_pressure_eases(rates).target.any()
