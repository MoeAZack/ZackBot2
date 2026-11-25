"""Research-only daily long on rising demand for Bitcoin block space.

Use native BTC paid per confirmed transaction and the confirmed transaction
count. A demand pulse begins when the fee per transaction reaches 1.5 times
its preceding 30-day median while the count is at least its own preceding
30-day median. A UTC observation is usable after two more UTC days close.
Require rising/sideways BTC and target regimes and a positive target day.
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

FAMILY_NOVELTY = ('Observes fees paid to confirm Bitcoin transactions and transaction count. '
                  'Earlier participation families use orders or turnover on exchanges; '
                  'V117/V123/V135 use perpetual funding, which settles between traders.')
BLOCKSPACE_MANIFEST_SHA256 = '6ad785fa29e62584eba63ea351c6f43596fb9de95368e0f17bed615f55efd7a8'
MARKET_FEATURE = ('BLOCKSPACE_MANIFEST_SHA256', 'BTC FeeTotNtv and TxCnt after two full UTC days')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community Network Data API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_blockspace_20261125/manifest.json'
_BTC = None


def load_blockspace():
    raw = Path(os.environ.get('LONGSCOUT_BLOCKSPACE_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BLOCKSPACE_MANIFEST_SHA256:
        raise ValueError('expected pinned Bitcoin block-space data')
    data = json.loads(raw)
    fields = ['FeeTotNtv', 'TxCnt']
    if (data['status'], data['asset'], data['frequency'], data['fields']) != ('COMPLETE', 'btc', '1d', fields):
        raise ValueError('invalid Bitcoin block-space input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Bitcoin block-space dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def demand_pulse(total_fee, transactions):
    valid = np.isfinite(total_fee) & np.isfinite(transactions) & total_fee.ge(0) & transactions.gt(0)
    fee_per_tx = total_fee.where(valid) / transactions.where(valid)
    count = transactions.where(valid)
    reference_fee = fee_per_tx.shift().rolling(30, min_periods=30).median()
    reference_count = count.shift().rolling(30, min_periods=30).median()
    usable = valid & reference_fee.gt(0) & reference_count.gt(0)
    elevated = fee_per_tx.ge(1.5 * reference_fee) & count.ge(reference_count)
    previous_below = usable.shift(fill_value=False) & ~elevated.shift(fill_value=False)
    return (usable & elevated & previous_below).shift(2, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    network = load_blockspace()
    pulse = demand_pulse(network.FeeTotNtv, network.TxCnt)
    _BTC = pulse.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_blockspace(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l138_btc_blockspace_demand_pulse_1d': dict(
        name='[RESEARCH] L138 Bitcoin block-space demand pulse long', style='momentum',
        fn=sig_blockspace, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
