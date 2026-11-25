"""Research-only long after primary-dealer Treasury delivery fails ease.

Sum weekly reported fails-to-deliver for TIPS and other US Treasuries.
Require the previous figure at least twice its preceding twelve-report
median and current fails no higher than that median. Require consecutive
weeks and a consistent reporting series. Wait sixteen UTC midnights
after the observation date and buy an up-day target in nonbear R1.
Volumes are cumulative dealer-reported fails and can include repeat fails.
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

FAMILY_NOVELTY = ('Uses failed delivery in the Treasury settlement process. V175 uses the cost '
                  'of financing collateral, V182 uses Fed balances, and V173 uses real yields.')
TREASURY_FAILS_SHA256 = '47a95ed6fbca30a8f9f048bddd226df7db15f89f344d984d5ff7296c8a8ed512'
MARKET_FEATURE = ('TREASURY_FAILS_SHA256', 'NY Fed cumulative primary-dealer Treasury fails-to-deliver')
MARKET_FEATURE_SOURCE = 'New York Fed FR 2004 primary-dealer weekly statistics, two Treasury sale categories'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_treasury_settlement_fails_20261125/manifest.json'
FIELDS = ['PDFTD-USTET', 'PDFTD-UST']
_BTC = None


def load_settlement_fails():
    raw = Path(os.environ.get('LONGSCOUT_TREASURY_SETTLEMENT_FAILS', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != TREASURY_FAILS_SHA256:
        raise ValueError('expected pinned Treasury settlement fails')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1w', FIELDS, 'USD millions',
    ):
        raise ValueError('invalid Treasury fails archive contract')
    dates = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not (dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())):
        raise ValueError('Treasury report dates must be sorted unique midnights')
    period = pd.Series([row[1] for row in data['rows']], index=dates)
    if not period.isin(('SBN2022', 'SBN2024')).all():
        raise ValueError('unrecognized Treasury fails reporting series')
    fail = pd.DataFrame([row[2:] for row in data['rows']], index=dates, columns=FIELDS, dtype='float64')
    return fail, period


def delivery_fails_ease(fails, period):
    valid = (np.isfinite(fails) & fails.ge(0)).all(axis=1)
    total = fails.sum(axis=1, min_count=2).where(valid)
    baseline = total.shift(2).rolling(12, min_periods=12).median()
    dates = total.index.to_series().diff().dt.days.between(6, 8)
    adjacent = dates.rolling(13, min_periods=13).sum().eq(13)
    block = period.ne(period.shift()).cumsum()
    same_series = block.eq(block.shift(13))
    relief = adjacent & same_series & baseline.gt(0) & total.shift().ge(2. * baseline) & total.le(baseline)
    return relief.set_axis(relief.index + pd.Timedelta(days=15))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    fails, period = load_settlement_fails()
    event = delivery_fails_ease(fails, period).reindex(btc.index, fill_value=False)
    _BTC = event & L1.regime_daily(btc).isin((0., 1.))


def sig_treasury_settlement_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l191_treasury_delivery_fails_normalize_1d': dict(
        name='[RESEARCH] L191 Treasury delivery fails normalize long', style='momentum',
        fn=sig_treasury_settlement_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
