"""Synthetic checks of actual revenue improvement and availability."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v199 as M

PLANTED_MUTANTS = [
    ('allow_bch_denominator_shock', ' & b.diff().abs().le(np.log(1.05))', '', 'test_bch_revenue_must_hold'),
    ('allow_btc_weak_improvement', ' & a.diff().ge(np.log(1.05))', '', 'test_requires_own_revenue_improvement'),
    ('allow_unpublished_network_input', " & pd.to_datetime(ready, unit='s').le(decision)", '', 'test_guard_uses_provider_time'),
]


def sample():
    dates = pd.date_range('2025-01-01', periods=90)
    btc = pd.Series(0., index=dates)
    btc.iloc[70:72] = [-.15, -.01]
    bch = pd.Series(0., index=dates)
    finished = pd.Series((dates + pd.Timedelta(hours=27)).as_unit('s').asi8, index=dates, dtype=float)
    return btc, bch, finished


def test_bch_revenue_must_hold():
    a, b, known = sample()
    a.iloc[71] = -.06
    b.iloc[71] = -.12
    assert not M.mining_spread_recovers(a, b, known).any()


def test_requires_own_revenue_improvement():
    a, b, known = sample()
    a.iloc[70:72] = [-.105, -.061]
    a.iloc[72:] = -.061
    b.iloc[71:] = -.043
    assert not M.mining_spread_recovers(a, b, known).any()


def test_guard_uses_provider_time():
    a, b, known = sample()
    flags = M.mining_spread_recovers(a, b, known)
    assert flags[flags].index.tolist() == [a.index[73]]
    known.iloc[71] = a.index[75].timestamp()
    assert not M.mining_spread_recovers(a, b, known).any()
