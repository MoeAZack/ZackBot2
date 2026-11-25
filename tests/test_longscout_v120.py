"""Synthetic premium timing, missing-input and orientation checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v120 as M

PLANTED_MUTANTS = [
    ('ignore_price_disagreement', ' & move.lt(0)', ' & move.gt(0)',
     'test_both_convergence_sides'),
    ('allow_premium_to_overshoot', ' & current.le(0)', '',
     'test_wrong_price_and_overshoot_suppress_signal'),
    ('fill_unobserved_premium', 'premium = premium.reindex(btc.index)', 'premium = premium.reindex(btc.index).ffill()',
     'test_historical_gap_and_future_prefix'),
]


def fixture():
    t = pd.date_range('2024-01-01', periods=45, freq='4h')
    price = pd.Series(100., index=t)
    price.iloc[36] = 99.
    price.iloc[41] = 101.
    premium = pd.Series(0., index=t)
    premium.iloc[35:37] = [-.002, -.001]
    premium.iloc[40:42] = [.002, .001]
    return price, premium


def test_both_convergence_sides():
    price, premium = fixture()
    flags = M.market_conditions(price, premium, True)
    assert np.flatnonzero(flags.long).tolist() == [36]
    assert np.flatnonzero(flags.short).tolist() == [41]
    falling = M.market_conditions(price, premium, False)
    assert not falling.long.any() and not falling.short.any()


def test_wrong_price_and_overshoot_suppress_signal():
    price, premium = fixture()
    premium.iloc[36] = .0002
    assert not M.market_conditions(price, premium, True).long.any()
    premium.iloc[36] = -.001
    price.iloc[36:38] = 101.
    assert not M.market_conditions(price, premium, True).long.any()


def test_historical_gap_and_future_prefix():
    price, premium = fixture()
    full = M.market_conditions(price, premium, True)
    for end in (30, 36, 37, 42):
        pd.testing.assert_frame_equal(M.market_conditions(price.iloc[:end], premium.iloc[:end], True), full.iloc[:end])
    premium.iloc[20] = np.nan
    gap = M.market_conditions(price, premium, True)
    assert not gap.long.any() and not gap.short.any()
    for key, value in M.CANDIDATES.items():
        assert key not in M.S.STRATEGIES and value['research_only'] and value['sides'] == 'both'
