"""Research-only long on open-interest rotation within Binance futures.

Compare three-day changes in base-unit open interest sampled at 23:55 UTC.
Require BTC open interest to fall by 2% while ETH and SOL each gain 2% or
more, with all intervening daily observations present. Enter on the first
observed rotation with positive target return and nonbear BTC and target R1.
Using native quantities avoids attributing a price rise to new exposure.
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

FAMILY_NOVELTY = ('Requires open-interest quantity to expand simultaneously in two futures '
                  'markets while contracting in BTC. V118 uses a BTC-only reset; V94/V130 '
                  'observe turnover; V134 compares option volatility. This compares changes '
                  'within each instrument, without asserting that capital moved between them.')
CONTRACT_ROTATION_SHA256 = 'a82581631330b7dfcd71bee71a541c7ce716e69fc3e73c1abd7903f0f2c67526'
MARKET_FEATURE = ('CONTRACT_ROTATION_SHA256', 'BTCUSDT, ETHUSDT and SOLUSDT native open interest at 23:55 UTC')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_contract_rotation_20261125/manifest.json'
INSTRUMENTS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
_BTC = None


def load_open_interest():
    raw = Path(os.environ.get('LONGSCOUT_CONTRACT_ROTATION_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONTRACT_ROTATION_SHA256:
        raise ValueError('expected pinned open-interest rotation data')
    data = json.loads(raw)
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', '23:55 UTC exact observation', 'sum_open_interest', INSTRUMENTS)
    if (data['status'], data['sample'], data['field'], data['instruments']) != expected:
        raise ValueError('invalid open-interest rotation input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row['date'] for row in data['days']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('open-interest rotation dates must be continuous and unique')
    return pd.DataFrame({symbol: [row['market'][symbol].get('quantity') for row in data['days']]
                         for symbol in INSTRUMENTS}, index=dates).apply(pd.to_numeric, errors='raise')


def rotation(quantity):
    q = quantity.where(np.isfinite(quantity) & quantity.gt(0))
    ready = q.notna().all(axis=1).rolling(4, min_periods=4).sum().eq(4)
    change = q.pct_change(3, fill_method=None)
    state = ready & change.BTCUSDT.le(-.02) & change.ETHUSDT.ge(.02) & change.SOLUSDT.ge(.02)
    return state & ready.shift(fill_value=False) & ~state.shift(fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    onset = rotation(load_open_interest())
    _BTC = onset.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_open_interest_rotation(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l144_native_open_interest_rotation_1d': dict(
        name='[RESEARCH] L144 Native open-interest rotation long', style='momentum',
        fn=sig_open_interest_rotation, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
