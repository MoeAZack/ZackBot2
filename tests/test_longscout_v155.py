"""Synthetic unit invariance, chronology and missingness checks."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v155 as M

PLANTED_MUTANTS = [
    ('ignore_linear_leg', ' - np.log(usd_notional.where(safe))', '', 'test_matched_growth_is_not_a_share_shock'),
    ('ignore_prior_variation', ' & move.ge(4. * median)', '', 'test_shock_must_exceed_its_recent_noise'),
    ('forward_fill_missing_linear_leg', '    safe = ', '    usd_notional = usd_notional.ffill()\n    safe = ',
     'test_missing_leg_masks_both_adjacent_moves'),
]


def sample():
    return pd.Series([100.] * 70 + [110.] * 5), pd.Series([1000.] * 75)


def test_matched_growth_is_not_a_share_shock():
    coin, usd = sample()
    hit = M.collateral_shock(coin, usd)
    assert hit[hit].index.tolist() == [70]
    pd.testing.assert_series_equal(M.collateral_shock(coin * .01, usd * 345), hit)
    assert not M.collateral_shock(coin, coin * 10).any()


def test_shock_must_exceed_its_recent_noise():
    coin, usd = sample()
    coin.iloc[:70] = np.tile([100., 103.], 35)
    coin.iloc[70:] = 114.
    assert not M.collateral_shock(coin, usd).any()


def test_missing_leg_masks_both_adjacent_moves():
    coin, usd = sample()
    usd.iloc[69] = float('nan')
    assert not M.collateral_shock(coin, usd).any()
