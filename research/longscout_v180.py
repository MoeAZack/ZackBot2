"""Research-only long after a BTC bid-ask bounce proxy normalizes.

Infer a spread from negative adjacent five-minute return covariance inside
each completed four-hour window. Leave nonnegative covariance unidentified.
The prior spread must be at least 1.5 times its preceding 84-bar median.
Require the current spread at or below .75 times that median and realized
volatility within 20% of the previous window. Buy after both BTC and a
nonbear target close up, with BTC R1 also nonbear. The spread is a proxy.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Separates short-run negative price covariance from similar realized volatility '
                  'to track a bid-ask bounce proxy. V131 uses one- and two-day extrema, V82 '
                  'uses price move per dollar, and V21 uses 4h persistence directly.')
BTC_ROLL_SPREAD_SHA256 = '67c69eeb23ac8f1b889e94e45a6c306c4ede1c91c95dbb16cd6473b877bc8301'
MARKET_FEATURE = ('BTC_ROLL_SPREAD_SHA256', 'negative five-minute return covariance and realized volatility')
MARKET_FEATURE_SOURCE = 'Binance Data Vision BTCUSDT USD-M perpetual 5-minute klines'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_roll_5m_20261125/manifest.json'
_BTC = None


def load_roll_spread():
    raw = Path(os.environ.get('LONGSCOUT_BTC_ROLL_SPREAD', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BTC_ROLL_SPREAD_SHA256:
        raise ValueError('expected pinned five-minute BTC covariance')
    data = json.loads(raw)
    fields = ['negative_autocov_spread', 'realized_volatility']
    if (data['status'], data['frequency'], data['feature_resolution'], data['symbol'], data['fields']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '4h', '5m', 'BTCUSDT', fields,
    ):
        raise ValueError('invalid BTC five-minute covariance source contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']], unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('covariance windows must be unique and consecutive')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields, dtype='float64')


def spread_compresses(values):
    frame = values.where(np.isfinite(values) & values.gt(0))
    spread, volatility = frame.negative_autocov_spread, frame.realized_volatility
    benchmark = spread.shift(2).rolling(84, min_periods=28).median()
    vol_change = volatility / volatility.shift()
    return (spread.shift().ge(1.50 * benchmark) & spread.le(.75 * benchmark)
            & vol_change.between(.80, 1.20))


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    grid = pd.date_range(dates[0], dates[-1], freq='4h')
    close = pd.Series(frame.c.to_numpy(), index=dates).reindex(grid)
    nonbear = pd.Series(np.isin(L1.coin_regime(frame), (0., 1.)), index=dates)
    events = spread_compresses(load_roll_spread()).reindex(grid, fill_value=False)
    _BTC = events & close.diff().gt(0) & nonbear.reindex(grid, fill_value=False)


def sig_roll_compression(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    consecutive = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    enter = (_BTC.reindex(dates, fill_value=False).to_numpy() & consecutive
             & d.c.diff().gt(0).to_numpy() & np.isin(L1.coin_regime(d), (0., 1.)))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l180_btc_roll_spread_proxy_compression_4h': dict(
        name='[RESEARCH] L180 BTC bid-ask bounce proxy compression long', style='momentum',
        fn=sig_roll_compression, sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
