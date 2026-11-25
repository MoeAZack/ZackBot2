"""Research-only long after a decline in primary dealers' net Treasury inventory.

Aggregate all seven maturity buckets of nominal Treasury coupons. Require
the prior report's net long balance above its preceding twelve-report
median, with the new balance still positive but at least 25% lower.
Use continuous weeks from one reporting series and wait sixteen UTC
midnights after the observation. Buy an up-day target in nonbear R1.
Reported changes can reflect trading, hedges, valuation or composition.
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

FAMILY_NOVELTY = ('Uses net primary-dealer coupon inventory. V191 examines failed settlement, '
                  'V176 regulated futures trader direction, and V182 Fed liabilities and credit.')
TREASURY_INVENTORY_SHA256 = '07d39a75d2f6bbdf7ed2ff8f9a3abcfd6e92c1e4547eb846a4b50bc867eda1c5'
MARKET_FEATURE = ('TREASURY_INVENTORY_SHA256', 'NY Fed weekly net primary-dealer nominal Treasury coupon holdings')
MARKET_FEATURE_SOURCE = 'New York Fed FR 2004 weekly dealer positions across all nominal coupon maturities'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_treasury_coupon_inventory_20261125/manifest.json'
FIELDS = ['PDPOSGSC-' + suffix for suffix in ('L2', 'G2L3', 'G3L6', 'G6L7', 'G7L11', 'G11L21', 'G21')]
_BTC = None


def load_coupon_positions():
    raw = Path(os.environ.get('LONGSCOUT_TREASURY_INVENTORY', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != TREASURY_INVENTORY_SHA256:
        raise ValueError('expected pinned primary-dealer net Treasury holdings')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1w', FIELDS, 'USD millions outright net positions (long minus short)',
    ):
        raise ValueError('invalid Treasury net positions archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not (dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())):
        raise ValueError('Treasury report dates must be sorted unique midnights')
    period = pd.Series([row[1] for row in data['rows']], index=dates)
    if not period.isin(('SBN2022', 'SBN2024')).all():
        raise ValueError('unrecognized Treasury inventory reporting series')
    amounts = pd.DataFrame([row[2:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')
    return amounts, period


def dealer_inventory_reduces(amounts, period):
    net = amounts.where(np.isfinite(amounts)).sum(axis=1, min_count=len(FIELDS))
    baseline = net.shift(2).rolling(12, min_periods=12).median()
    dates = net.index.to_series().diff().dt.days.between(6, 8)
    adjacent = dates.rolling(13, min_periods=13).sum().eq(13)
    block = period.ne(period.shift()).cumsum()
    same_series = block.eq(block.shift(13))
    previous = net.shift()
    unload = adjacent & same_series & previous.gt(baseline) & net.gt(0) & net.le(.75 * previous)
    return unload.set_axis(unload.index + pd.Timedelta(days=15))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    amounts, period = load_coupon_positions()
    pulse = dealer_inventory_reduces(amounts, period).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_dealer_inventory(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l192_treasury_dealer_coupon_inventory_unloads_1d': dict(
        name='[RESEARCH] L192 Treasury dealer coupon inventory falls', style='momentum',
        fn=sig_dealer_inventory, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
