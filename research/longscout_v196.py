"""Research-only long when Bitcoin processes more transactions at stable block weight.

Compare seven-day average transactions, block weight and block count with
their preceding 28-day medians. Require at least 20% more transactions,
weight within 5% and blocks within 10%. Trade the first qualifying day
after seven additional UTC days, with nonbear BTC and a rising nonbear
target. This measures on-chain resource use without classifying transfers.
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

FAMILY_NOVELTY = ('Tests transaction count per physical chain weight and steady block production. '
                  'V138 measures fees paid, V183 header timing and V153 output churn.')
BTC_BLOCK_DENSITY_SHA256 = '1a19ad1e38c083f69df73d28ad41baa5398d54ed7731341e7b1cbe258998cff8'
MARKET_FEATURE = ('BTC_BLOCK_DENSITY_SHA256', 'Bitcoin block weight, block count and transaction count')
MARKET_FEATURE_SOURCE = 'Open Bitcoin Metrics 0.1.0 canonical full-node aggregates, CC BY 4.0'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_block_density_20261125/manifest.json'
FIELDS = ['block_weight', 'transaction_count', 'block_count']
_BTC = None


def load_block_density():
    raw = Path(os.environ.get('LONGSCOUT_BTC_BLOCK_DENSITY', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BTC_BLOCK_DENSITY_SHA256:
        raise ValueError('expected pinned Bitcoin physical block utilization input')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', FIELDS):
        raise ValueError('invalid Bitcoin block density archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('Bitcoin block utilization days must be consecutive and unique')
    return pd.DataFrame([row[1:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')


def transaction_density_rises(blocks):
    valid = (np.isfinite(blocks) & blocks.gt(0)).all(axis=1)
    values = blocks.where(valid, axis=0)
    current = values.rolling(7, min_periods=7).mean()
    prior = values.shift(7).rolling(28, min_periods=28).median()
    relative = current / prior
    observed = relative.notna().all(axis=1)
    dense = (observed & relative.transaction_count.ge(1.20)
             & relative.block_weight.between(.95, 1.05) & relative.block_count.between(.90, 1.10))
    pulse = dense & observed.shift(fill_value=False) & ~dense.shift(fill_value=False)
    return pulse.shift(7, fill_value=False)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    event = transaction_density_rises(load_block_density()).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_block_density(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l196_btc_transactions_per_stable_block_weight_1d': dict(
        name='[RESEARCH] L196 BTC transaction density rises at stable weight', style='momentum',
        fn=sig_block_density, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
