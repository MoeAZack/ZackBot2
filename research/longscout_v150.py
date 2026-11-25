"""Research-only long after two-sided BTC near-book depth recovers.

Use the median total displayed quantity within 1% of mid over matched
bid/ask samples in the final five UTC minutes. Require at least three
paired snapshots. Normalize by the prior 45-day median with at least 35
usable days. Enter after crossing back to 85% of baseline following a dip
to 65% or less in the preceding five valid consecutive observations.
Require positive target return and nonbear BTC and target regimes.
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

FAMILY_NOVELTY = ('Tests recovery in gross resting quantity on both sides, independent of its '
                  'bid/ask allocation. V124 compares that allocation between near and outer '
                  'bands. V131 estimates spread from prices and does not measure book capacity.')
GROSS_DEPTH_MANIFEST_SHA256 = 'fa8909a838a816dcff7b44c079a108dea5369b3fadd4509eb28cbd141443417a'
MARKET_FEATURE = ('GROSS_DEPTH_MANIFEST_SHA256', 'BTC total depth within one percent across both sides')
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_btc_gross_depth_20261125/manifest.json'
_BTC = None


def load_depth():
    raw = Path(os.environ.get('LONGSCOUT_BTC_GROSS_DEPTH_MANIFEST', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != GROSS_DEPTH_MANIFEST_SHA256:
        raise ValueError('expected pinned BTC gross depth input')
    data = json.loads(raw)
    expected = ('COMPLETE_WITH_EXPLICIT_MASKS', 'gross_near_depth', '23:55 to 24:00 UTC')
    if (data['status'], data['field'], data['sample']) != expected:
        raise ValueError('invalid BTC gross depth input contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row['date'] for row in data['days']]))
    if not dates.equals(pd.date_range(dates[0], dates[-1], freq='D')):
        raise ValueError('BTC gross depth dates must be continuous and unique')
    amount = pd.to_numeric(pd.Series([row.get('gross_near_depth') for row in data['days']], index=dates))
    count = pd.Series([row.get('snapshots', 0) for row in data['days']], index=dates)
    return amount.where(count.ge(3))


def liquidity_recovers(amount):
    depth = amount.where(np.isfinite(amount) & amount.gt(0))
    base = depth.shift().rolling(45, min_periods=35).median()
    ratio = depth / base
    previous = ratio.shift()
    prior_low = previous.rolling(5, min_periods=5).min()
    return prior_low.le(.65) & previous.lt(.85) & ratio.ge(.85)


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC = liquidity_recovers(load_depth().reindex(btc.index)) & L1.regime_daily(btc).isin((0., 1.))


def sig_gross_depth_recovery(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    event = _BTC.reindex(grid.index, fill_value=False) & grid.diff().gt(0) & L1.regime_daily(grid).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(event.reindex(close.index).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l150_btc_gross_near_book_recovery_1d': dict(
        name='[RESEARCH] L150 BTC gross near-book replenishment long', style='momentum',
        fn=sig_gross_depth_recovery, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
