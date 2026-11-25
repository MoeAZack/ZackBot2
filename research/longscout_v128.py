"""Research-only long after a BTC traded downside extreme diverges from references.

Require BTC's completed perpetual low to be at least 25 basis points below
both the mark and index lows. The upside excess beyond both reference highs
must be no greater than half the downside gap. The traded close must finish
within 10 basis points of both reference closes. Coin price must rise on the
bar. BTC and coin R1 must be rising or sideways. Compare completed-bar
extrema only, and simulate the next 4h open.
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Compares actual trade extremes against simultaneous-period mark and index '
                  'envelopes. V92/V109 use the chronology of traded extremes and V120 '
                  'uses changes in the premium index close. This tests trade-only range excess.')
BTC_REFERENCE_MANIFEST_SHA256 = '74bee3f375a9401395256e0412be16c09e046ecf1cbf4dff5727f60e30a2c2b5'
MARKET_FEATURE = ('BTC_REFERENCE_MANIFEST_SHA256', 'BTC 4h mark and index high/low/close envelopes')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_reference_20261125/manifest.json'
_BTC = None


def load_references():
    path = Path(os.environ.get('LONGSCOUT_BTC_REFERENCE_MANIFEST', DEFAULT_DATA))
    raw = path.read_bytes()
    if BTC_REFERENCE_MANIFEST_SHA256 is None or hashlib.sha256(raw).hexdigest() != BTC_REFERENCE_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC reference manifest')
    data = json.loads(raw)
    if (data['status'], data['symbol'], data['timeframe'], data['fields'], data['data']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', 'BTCUSDT', '4h', ['high', 'low', 'close'], ['mark', 'index'],
    ):
        raise ValueError('invalid BTC reference source contract')
    timestamps, mark, index = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(timestamps, unit='ms'))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='4h')):
        raise ValueError('BTC reference grid must be unique and consecutive')
    columns = ['high', 'low', 'close']
    missing = [None] * 3
    convert = lambda rows: pd.DataFrame([row or missing for row in rows], index=dates, columns=columns).apply(pd.to_numeric)
    return convert(mark), convert(index)


def traded_excess(trade, mark, index):
    mark, index = mark.reindex(trade.index), index.reindex(trade.index)
    quality = pd.Series(True, index=trade.index)
    for prices in (trade, mark, index):
        quality &= (np.isfinite(prices) & prices.gt(0)).all(axis=1)
        quality &= prices.high.ge(prices.close) & prices.low.le(prices.close)
    lower = np.minimum(mark.low, index.low)
    upper = np.maximum(mark.high, index.high)
    downside = (lower - trade.low) / lower
    upside = (trade.high - upper) / upper
    aligned = ((trade.close / mark.close - 1).abs().le(.001)
               & (trade.close / index.close - 1).abs().le(.001))
    return quality & aligned & downside.ge(.0025) & upside.le(.5 * downside)


def set_btc(frame):
    global _BTC
    dates = pd.DatetimeIndex(pd.to_datetime(frame.t))
    trades = frame[['h', 'l', 'c']].set_axis(dates).rename(columns={'h': 'high', 'l': 'low', 'c': 'close'})
    mark, index = load_references()
    hits = traded_excess(trades, mark, index) & np.isin(L1.coin_regime(frame), (0., 1.))
    _BTC = hits & ~hits.shift(fill_value=False)


def sig_traded_excess(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    enter = (_BTC.reindex(dates, fill_value=False).to_numpy() & d.c.diff().gt(0).to_numpy()
             & contiguous & np.isin(L1.coin_regime(d), (0., 1.)))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l128_btc_trade_only_downside_extreme_4h': dict(
        name='[RESEARCH] L128 BTC trade-only downside extreme', style='reversal', fn=sig_traded_excess,
        sides='long', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
