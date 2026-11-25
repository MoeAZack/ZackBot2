"""Research-only range fade after a shift toward high-priority BTC fees.

Use one observed mempool fee histogram from each complete UTC half-day.
Require the share of sampled virtual bytes paying at least 10 sat/vB to
rise ten percentage points above its prior 40-day median while total
sampled virtual bytes remain within 20% of baseline. Trade the first
qualifying observation after a full-day buffer, fading a sideways target's
2% move with nonbear BTC moving at most 1%.
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

FAMILY_NOVELTY = ('Measures urgency as the fee composition of unconfirmed virtual bytes with '
                  'stable queue size. V165 uses the count of pending transactions and their '
                  'arrival rate. V138 uses fees already paid by confirmed transactions.')
MEMPOOL_PRIORITY_SOURCE_SHA256 = 'e9cb4d12879f8f814ea48afe2af30227a2429c055f303bcc0438a28c21b89e7b'
MARKET_FEATURE = ('MEMPOOL_PRIORITY_SOURCE_SHA256', 'sampled vbytes at and above 10 sat/vB and total vbytes')
MARKET_FEATURE_SOURCE = 'mempool.emzy.de public Bitcoin mempool statistics'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_mempool_queue_20261125/source.json'
FIRST_DAY, LAST_DAY = '2022-08-01', '2026-10-04'
_BTC = None


def load_priority():
    raw = Path(os.environ.get('LONGSCOUT_MEMPOOL_PRIORITY_SOURCE', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != MEMPOOL_PRIORITY_SOURCE_SHA256:
        raise ValueError('expected pinned mempool fee-priority data')
    dates = pd.date_range(FIRST_DAY, LAST_DAY, freq='D')
    sizes = {}
    for row in json.loads(raw):
        bucket = pd.to_datetime(row['added'], unit='s').floor('12h')
        if bucket.normalize() < dates[0] or bucket.normalize() > dates[-1]:
            continue
        weights = np.asarray(row['vsizes'], dtype=float)
        if bucket in sizes or weights.shape != (39,) or not np.isfinite(weights).all() or (weights < 0).any():
            raise ValueError('invalid or duplicate fee histogram')
        total = weights.sum()
        sizes[bucket] = (total, weights[8:].sum()) if total > 0 else (np.nan, np.nan)
    histograms = pd.DataFrame.from_dict(sizes, orient='index', columns=['total_vbytes', 'high_fee_vbytes'])
    return histograms.resample('D').sum(min_count=2).reindex(dates)


def priority_shock(total, high):
    ready = np.isfinite(total) & total.gt(0) & np.isfinite(high) & high.ge(0) & high.le(total)
    total = total.where(ready)
    share = high.where(ready) / total
    previous_share = share.shift().rolling(40, min_periods=40).median()
    previous_total = total.shift().rolling(40, min_periods=40).median()
    observed = ready & previous_share.notna() & previous_total.gt(0)
    state = observed & share.ge(previous_share + .10) & (total / previous_total).between(.80, 1.20)
    return (state & observed.shift(fill_value=False) & ~state.shift(fill_value=False)).shift(1, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    source = load_priority()
    event = priority_shock(source.total_vbytes, source.high_fee_vbytes)
    _BTC = (event.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))
            & btc.pct_change(fill_method=None).abs().le(.01))


def sig_fee_priority_shock(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).eq(0)
    change = grid.pct_change(fill_method=None)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series((active & change.le(-.02)).reindex(close.index).to_numpy(), index=d.index)),
                se=S.X(pd.Series((active & change.ge(.02)).reindex(close.index).to_numpy(), index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r167_btc_mempool_priority_composition_1d': dict(
        name='[RESEARCH] R167 BTC mempool priority composition fade', style='range',
        fn=sig_fee_priority_shock, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
