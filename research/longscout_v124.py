"""Research-only range fade using the distribution of BTC book depth.

Use public BTC snapshot medians from the final five minutes of each UTC
day. Subtract bid share in the 1%–5% bands from bid share within 1% of mid.
After leaving a neutral band of +/−0.15, require BTC's completed return
to agree with the near-book side. Fade a sideways coin's move against
that side while BTC is rising/sideways. Model the next daily open.
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

FAMILY_NOVELTY = ('Measures where displayed liquidity is placed relative to the midpoint, comparing '
                  'the near and outer book. V98 is about aggressive trades, V118 total open interest, '
                  'and V119 top-trader positions. Those do not observe the shape of resting depth.')
BTC_DEPTH_MANIFEST_SHA256 = 'b82f3e519220719f57544e64fca068c69634615b5cf55774d06016825c48e47e'
MARKET_FEATURE = ('BTC_DEPTH_MANIFEST_SHA256', 'BTC near versus outer bid-depth share')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_depth_20261125/manifest.json'
_MARKET = None


def load_depth():
    path = Path(os.environ.get('LONGSCOUT_BTC_DEPTH_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_DEPTH_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_DEPTH_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC depth manifest')
    data = json.loads(raw)
    if (data['status'], data['field'], data['sample']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', 'near_minus_outer_bid_share', '23:55 to 24:00 UTC',
    ):
        raise ValueError('invalid depth source contract')
    dates = pd.DatetimeIndex([row['date'] for row in data['days']])
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('depth grid must be unique and consecutive')
    score = pd.to_numeric(pd.Series([row.get('near_minus_outer') for row in data['days']], index=dates))
    return score.where(np.isfinite(score) & score.between(-1, 1))


def market_conditions(btc, depth):
    score = depth.reindex(btc.index)
    neutral = score.shift().abs().lt(.15)
    nonbear = L1.regime_daily(btc).isin((0., 1.))
    move = btc.diff()
    return pd.DataFrame(dict(long=neutral & score.ge(.15) & move.gt(0) & nonbear,
                             short=neutral & score.le(-.15) & move.lt(0) & nonbear))


def set_btc(frame):
    global _MARKET
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _MARKET = market_conditions(btc, load_depth())


def sig_depth_fade(d, params, ctx):
    price, _ = L101._native_daily(d)
    daily = price.reindex(pd.date_range(price.index[0], price.index[-1], freq='D'))
    change = daily.diff()
    sideways = L1.regime_daily(daily).eq(0)
    market = _MARKET.reindex(daily.index)
    long = market.long.eq(True) & change.lt(0) & sideways
    short = market.short.eq(True) & change.gt(0) & sideways
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(long.reindex(price.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series(short.reindex(price.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r124_btc_orderbook_depth_redistribution_1d': dict(
        name='[RESEARCH] R124 BTC near versus outer depth range fade', style='range', fn=sig_depth_fade,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
