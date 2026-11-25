"""Synthetic checks of quote conversion and adjacent premium reversal."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v187 as M

PLANTED_MUTANTS = [
    ('ignore_quote_conversion', 'usable.btc_usdt_binance * usd_per_usdt', 'usable.btc_usdt_binance',
     'test_converts_usdt_into_usd'),
    ('allow_prior_depeg', ' & stable.shift(fill_value=False)', '', 'test_requires_prior_stablecoin_parity'),
    ('use_future_premium', 'relative.shift().le(-.001)', 'relative.shift(-1).le(-.001)',
     'test_only_prior_discount_trades'),
]


def sample():
    return pd.DataFrame({'btc_usd_coinbase': [998., 1002., 1002.], 'btc_usdt_binance': 1000., 'usdt_usd_coinbase': 1.})


def test_converts_usdt_into_usd():
    prices = sample()
    prices.loc[1:, 'usdt_usd_coinbase'] = 1.0019
    assert not M.fiat_spot_premium_reverses(prices).any()


def test_requires_prior_stablecoin_parity():
    prices = sample()
    prices.iloc[0, 2] = 1.004
    assert not M.fiat_spot_premium_reverses(prices).any()


def test_only_prior_discount_trades():
    prices = sample()
    result = M.fiat_spot_premium_reverses(prices)
    assert result.tolist() == [False, True, False]
    pd.testing.assert_series_equal(M.fiat_spot_premium_reverses(prices.iloc[:2]), result.iloc[:2])
