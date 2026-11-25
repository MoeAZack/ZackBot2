"""Research-only range fade when BTC premium contracts against price.

Measure the prior 4h premium-index close against the 30 observations
before it. After a dislocation of at least 10 bp, require a 5 bp move
toward that fixed median without crossing it. Fade an asset still
underperforming BTC on a negative dislocation, or still outperforming
on a positive dislocation, when BTC price itself moves against premium
convergence. Require sideways coin R1 and rising/sideways BTC R1.
Both BTC inputs refer to completed 4h bars. Model the next 4h open.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Uses short-horizon convergence of the BTC premium index against concurrent price '
                  'direction. V117 uses settled funding sign under a price breakout; V118 and V119 '
                  'use open interest or position concentration. Neither measures premia reverting '
                  'while the futures price continues the other way.')
BTC_PREMIUM_MANIFEST_SHA256 = 'c28f63707a0531074df43357e3a74a16a0f8b068275f73bac31cf2a69df7b300'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_premium_20261125/manifest.json'
_MARKET = None


def load_premium():
    path = Path(os.environ.get('LONGSCOUT_BTC_PREMIUM_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_PREMIUM_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_PREMIUM_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC premium manifest')
    data = json.loads(raw)
    if (data['status'], data['symbol'], data['timeframe'], data['field']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', 'BTCUSDT', '4h', 'premium_index_close',
    ):
        raise ValueError('invalid premium source contract')
    timestamps, values = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(timestamps, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('premium grid must be unique and consecutive')
    series = pd.to_numeric(pd.Series(values, index=dates))
    return series.where(np.isfinite(series))


def market_conditions(btc, premium, nonbear):
    premium = premium.reindex(btc.index)
    baseline = premium.shift(2).rolling(30, min_periods=30).median()
    previous = premium.shift() - baseline
    current = premium - baseline
    convergence = current - previous
    move = btc.pct_change(fill_method=None)
    long = (previous.le(-.001) & convergence.ge(.0005) & current.le(0) & move.lt(0) & nonbear)
    short = (previous.ge(.001) & convergence.le(-.0005) & current.ge(0) & move.gt(0) & nonbear)
    return pd.DataFrame(dict(long=long, short=short, move=move))


def set_btc(frame):
    global _MARKET
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    btc = pd.Series(frame.c.to_numpy(dtype=float), index=dates)
    nonbear = np.isin(L1.coin_regime(frame), (0., 1.))
    _MARKET = market_conditions(btc, load_premium(), nonbear)


def sig_premium_fade(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    market = _MARKET.reindex(dates)
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    sideways = (L1.coin_regime(d) == 0) & contiguous
    coin_move = d.c.pct_change(fill_method=None).to_numpy()
    long = sideways & market.long.eq(True).to_numpy() & (coin_move < market.move.to_numpy())
    short = sideways & market.short.eq(True).to_numpy() & (coin_move > market.move.to_numpy())
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(long, index=d.index)), se=S.X(pd.Series(short, index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r120_btc_premium_convergence_fade_4h': dict(
        name='[RESEARCH] R120 BTC premium convergence range fade', style='range', fn=sig_premium_fade,
        sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
