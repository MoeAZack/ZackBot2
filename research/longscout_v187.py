"""Research-only long on an exchange-specific BTC spot premium reversal.

Convert Binance BTCUSDT into dollars using Coinbase USDT-USD. Require
USDT within 20 basis points of par on both dates. The Coinbase BTC-USD
close must move from at least 10 basis points below the converted close to
at least 10 basis points above it. Use matched native UTC daily bar closes
and trade nonbear targets that close up while BTC R1 is nonbear.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Measures a traded spot price change across two exchanges after adjustment '
                  'for quote currency. V132 tests a same-venue spot triangle, V147 tests '
                  'spot and perpetual lead-lag, and V170 uses contract-index spreads.')
COINBASE_BTC_PREMIUM_SHA256 = '84d9da13930c28ce5d981c61d8203a574cc4f88274c43e169cff8e546b88ed8c'
MARKET_FEATURE = ('COINBASE_BTC_PREMIUM_SHA256', 'actual spot BTC and FX-conversion closes across two exchanges')
MARKET_FEATURE_SOURCE = 'Coinbase Exchange product candles and Binance Data Vision BTCUSDT spot candles'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_coinbase_btc_premium_20261125/manifest.json'
FIELDS = ['btc_usd_coinbase', 'btc_usdt_binance', 'usdt_usd_coinbase']
_BTC = None


def load_cross_market():
    raw = Path(os.environ.get('LONGSCOUT_COINBASE_BTC_PREMIUM', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != COINBASE_BTC_PREMIUM_SHA256:
        raise ValueError('expected pinned native spot prices')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['clock']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS,
        'same native UTC spot daily bar close; last underlying trade times unavailable',
    ):
        raise ValueError('invalid cross-exchange daily close source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('spot price dates must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def fiat_spot_premium_reverses(prices):
    usable = prices.where(np.isfinite(prices) & prices.gt(0))
    usd_per_usdt = usable.usdt_usd_coinbase
    normalized = usable.btc_usdt_binance * usd_per_usdt
    relative = usable.btc_usd_coinbase / normalized - 1.
    stable = usd_per_usdt.between(.998, 1.002)
    return stable & stable.shift(fill_value=False) & relative.shift().le(-.001) & relative.ge(.001)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = fiat_spot_premium_reverses(load_cross_market()).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_fiat_premium_reversal(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l187_cross_exchange_btc_spot_premium_reversal_1d': dict(
        name='[RESEARCH] L187 Currency-adjusted BTC spot premium reversal long', style='momentum',
        fn=sig_fiat_premium_reversal, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
