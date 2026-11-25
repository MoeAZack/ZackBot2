"""Research-only daily long after BTC implied mining work recovers.

Compare the trailing seven-day mean estimated hashrate with the mean of the
28 days immediately before it. Require a recovery from below parity at the
previous observation to parity or higher now. At least one of the preceding
seven observations must have been 4% below its own reference. Use a two-day
buffer and require the recorded publication cutoff of every contributing
observation to be at or before the modeled decision.
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

FAMILY_NOVELTY = ('Tests recovery of estimated computational work settling the Bitcoin chain. '
                  'V138 measures user demand for block space via fees and transaction count. '
                  'Earlier exchange participation cases measure trading rather than mining.')
MINING_MANIFEST_SHA256 = '5bfc983595328e14a05daaf3c8c5ad25274a9f399b284b56d6278b065e443fda'
MARKET_FEATURE = ('MINING_MANIFEST_SHA256', 'BTC HashRate and AssetEODCompletionTime')
MARKET_FEATURE_SOURCE = 'Coin Metrics Community Network Data API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_mining_20261125/manifest.json'
_BTC = None


def load_mining():
    raw = Path(os.environ.get('LONGSCOUT_MINING_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != MINING_MANIFEST_SHA256:
        raise ValueError('expected pinned Bitcoin mining input')
    data = json.loads(raw)
    fields = ['HashRate', 'AssetEODCompletionTime']
    if (data['status'], data['asset'], data['frequency'], data['fields']) != ('COMPLETE', 'btc', '1d', fields):
        raise ValueError('invalid Bitcoin mining input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Bitcoin mining dates must be continuous and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=fields).apply(pd.to_numeric, errors='raise')


def mining_recovery(hashrate, complete):
    valid = np.isfinite(hashrate) & hashrate.gt(0) & np.isfinite(complete)
    work = hashrate.where(valid)
    recent = work.rolling(7, min_periods=7).mean()
    baseline = work.shift(7).rolling(28, min_periods=28).mean()
    ratio = recent / baseline
    prior = ratio.shift()
    stressed = prior.rolling(7, min_periods=7).min().le(.96)
    recovered = (prior.lt(1.) & ratio.ge(1.) & stressed).shift(2, fill_value=False)
    available = pd.to_datetime(complete.where(valid).rolling(42, min_periods=42).max().shift(2), unit='s')
    decision = pd.Series(hashrate.index + pd.Timedelta(days=1), index=hashrate.index)
    return recovered & available.le(decision)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    network = load_mining()
    recovery = mining_recovery(network.HashRate, network.AssetEODCompletionTime)
    _BTC = recovery.reindex(btc.index, fill_value=False) & L1.regime_daily(btc).isin((0., 1.))


def sig_mining_recovery(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l139_btc_mining_work_recovery_1d': dict(
        name='[RESEARCH] L139 Bitcoin mining work recovery long', style='momentum',
        fn=sig_mining_recovery, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
