"""Research-only long after the Bitcoin transaction backlog clears.

Use daily means of two complete 12h mempool count and arrival-rate buckets.
Require pending count to fall below its prior 30-day median after reaching
1.5 times baseline during the preceding week. Average new virtual bytes per
second must stay within 15% of baseline. Apply a full additional UTC day
of delay. Require nonbear BTC and target regimes and positive target return.
Pending count can fall through confirmations or removals from the collector.
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

FAMILY_NOVELTY = ('Measures the queue of unconfirmed Bitcoin transactions while holding their '
                  'arrival rate near normal. V138 uses fees paid by confirmed transactions '
                  'and V153 uses already spent or unspent confirmed outputs.')
MEMPOOL_QUEUE_MANIFEST_SHA256 = '6993289319e3751be3dd3b64f8b8cbb0c42f0703813741287d4223a850e4ff41'
MARKET_FEATURE = ('MEMPOOL_QUEUE_MANIFEST_SHA256', 'mempool pending count and incoming virtual bytes per second')
MARKET_FEATURE_SOURCE = 'mempool.emzy.de public Bitcoin mempool statistics'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_mempool_queue_20261125/manifest.json'
_BTC = None


def load_queue():
    raw = Path(os.environ.get('LONGSCOUT_MEMPOOL_QUEUE_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != MEMPOOL_QUEUE_MANIFEST_SHA256:
        raise ValueError('expected pinned mempool queue input')
    data = json.loads(raw)
    fields = ['pending_count', 'arrival_vbytes_sec']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', fields):
        raise ValueError('invalid mempool queue input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('mempool queue dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def queue_clears(pending, arrivals):
    ready = np.isfinite(pending) & pending.ge(0) & np.isfinite(arrivals) & arrivals.gt(0)
    count = pending.where(ready)
    rate = arrivals.where(ready)
    median_pending = count.shift().rolling(30, min_periods=30).median()
    median_arrival = rate.shift().rolling(30, min_periods=30).median()
    relative = count / median_pending.where(median_pending.gt(0))
    previous = relative.shift()
    stress = previous.rolling(7, min_periods=7).max().ge(1.50)
    arrival_is_normal = (rate / median_arrival).between(.85, 1.15)
    return (stress & previous.ge(1.) & relative.lt(1.) & arrival_is_normal).shift(1, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_queue()
    event = queue_clears(source.pending_count, source.arrival_vbytes_sec)
    _BTC = event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_queue_clearance(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l165_btc_mempool_queue_clearance_1d': dict(
        name='[RESEARCH] L165 BTC mempool queue clearance long', style='momentum',
        fn=sig_queue_clearance, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
