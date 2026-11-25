"""Research-only long on deeper public Bitcoin Lightning channels.

Compare public capacity and channel count observed by the same collector.
Over 14 days require capacity per channel to rise at least 10%, total
capacity at least 2%, and channel count to decline at least 1%. Enter the
first qualifying day after two full additional UTC days. Require nonbear
BTC and target regimes and positive target return. Public channel capacity
does not measure payment volume or private channels.
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

FAMILY_NOVELTY = ('Tests the split between public layer-two routing capacity and channel count. '
                  'V153 measures the Bitcoin base-layer output set; V157 tracks BTC-backed '
                  'token supply. Neither observes the topology of public payment channels.')
LIGHTNING_CAPACITY_MANIFEST_SHA256 = 'eec756889d02cc07202c6263cebb6b62ba6a0a83bb627c7005f7ddd54e650ba9'
MARKET_FEATURE = ('LIGHTNING_CAPACITY_MANIFEST_SHA256', 'public Lightning channel count and capacity')
MARKET_FEATURE_SOURCE = 'mempool.emzy.de Lightning statistics'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_lightning_20261125/manifest.json'
_BTC = None


def load_lightning():
    raw = Path(os.environ.get('LONGSCOUT_LIGHTNING_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != LIGHTNING_CAPACITY_MANIFEST_SHA256:
        raise ValueError('expected pinned Lightning capacity input')
    data = json.loads(raw)
    fields = ['channel_count', 'capacity_sats']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', fields):
        raise ValueError('invalid Lightning capacity input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Lightning capacity dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def channels_deepen(count, capacity):
    valid = np.isfinite(count) & count.gt(0) & np.isfinite(capacity) & capacity.gt(0)
    count, capacity = count.where(valid), capacity.where(valid)
    average = capacity / count
    observed = valid.rolling(15, min_periods=15).sum().eq(15)
    deepens = (observed & average.pct_change(14, fill_method=None).ge(.10)
               & capacity.pct_change(14, fill_method=None).ge(.02)
               & count.pct_change(14, fill_method=None).le(-.01))
    return (deepens & observed.shift(fill_value=False) & ~deepens.shift(fill_value=False)).shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_lightning()
    event = channels_deepen(source.channel_count, source.capacity_sats)
    _BTC = event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_lightning_capacity(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l164_btc_public_lightning_capacity_1d': dict(
        name='[RESEARCH] L164 BTC public Lightning channel depth long', style='momentum',
        fn=sig_lightning_capacity, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
