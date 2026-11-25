"""Synthetic checks of the constant-mix feature and trading direction."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v200 as M

PLANTED_MUTANTS = [
    ('reweight_log_returns_instead_of_values', 'np.logaddexp(target.diff(), market.diff()) - LOG_TWO',
     '.5 * (target.diff() + market.diff())', 'test_constant_mix_gross_growth_is_exact'),
    ('allow_missing_return_pairs', 'rolling(20, min_periods=20)', 'rolling(20, min_periods=18)',
     'test_needs_complete_gross_return_window'),
    ('buy_coin_after_outperformance', ' & relative_lag', '', 'test_fades_only_lagging_coin'),
]


def sample():
    coin = pd.Series([100., 120.] * 12 + [100.])
    bitcoin = pd.Series(100., index=coin.index)
    return coin, bitcoin


def test_constant_mix_gross_growth_is_exact():
    coin, bitcoin = sample()
    excess = M.constant_mix_excess(coin, bitcoin)
    assert np.isclose(excess.iloc[20], 10. * np.log(1.1 * ((100. / 120. + 1.) / 2.)))
    equal = M.constant_mix_excess(coin, coin)
    assert equal.dropna().abs().lt(1e-12).all()
    pd.testing.assert_series_equal(M.constant_mix_excess(coin.iloc[:22], bitcoin.iloc[:22]), excess.iloc[:22])


def test_needs_complete_gross_return_window():
    coin, bitcoin = sample()
    coin.iloc[10] = np.nan
    assert M.constant_mix_excess(coin, bitcoin).isna().all()


def test_fades_only_lagging_coin():
    coin, bitcoin = sample()
    range_mask = pd.Series(True, index=coin.index)
    event = M.long_from_rebalancing(coin, bitcoin, range_mask)
    assert event.iloc[20] and not event.iloc[21] and event.iloc[22]
