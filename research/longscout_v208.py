"""Research-only crypto long after US commercial crude inventories rebuild.

Require a 0.5% weekly stock build after two consecutive draws. US refinery
capacity utilization must hold or rise, distinguishing a stock build from
one that coincides with a lower utilization rate. Use the actual EIA issue
date, including delayed issues, with a two-UTC-day buffer. Buy a rising
nonbear coin in nonbear BTC. The inferred supply channel is a hypothesis.
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

FAMILY_NOVELTY = ('Tests the physical US crude inventory balance alongside refinery use. '
                  'V175 measures funding in overnight repo and V205 measures US bill '
                  'auction allocations. Neither uses oil supply or processing quantities.')
EIA_CRUDE_STOCKS_SHA256 = '4b8876b2c9675e4ad64f8349cc27d1db9003955ad667a9ce6f1a069806a4b490'
MARKET_FEATURE = ('EIA_CRUDE_STOCKS_SHA256', 'EIA commercial crude stocks, refinery use and release dates')
MARKET_FEATURE_SOURCE = 'EIA Weekly Petroleum Status Report history and issue archive'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_eia_oil_20261125/manifest.json'
FIELDS = ['actual_release_date', 'commercial_crude_thousand_barrels', 'operable_refinery_utilization_pct']
_BTC = None


def load_crude_balances():
    raw = Path(os.environ.get('LONGSCOUT_EIA_CRUDE_STOCKS', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EIA_CRUDE_STOCKS_SHA256:
        raise ValueError('expected pinned EIA crude stocks, refinery and release history')
    data = json.loads(raw)
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', 'week ending Friday', FIELDS):
        raise ValueError('invalid EIA supply archive contract')
    weeks = pd.DatetimeIndex(pd.to_datetime([row[0] for row in data['rows']]))
    if not weeks.equals(pd.date_range(weeks[0], weeks[-1], freq='W-FRI')):
        raise ValueError('EIA week-ending Fridays must be unique and consecutive')
    released = pd.Series(pd.to_datetime([row[1] for row in data['rows']]), index=weeks)
    lag_days = (released - weeks.to_series()).dt.days
    if not lag_days.dropna().between(1, 14).all() or not released.dropna().is_monotonic_increasing:
        raise ValueError('EIA issues must have consistently ordered publication dates')
    quantities = pd.DataFrame([row[2:] for row in data['rows']], index=weeks, columns=FIELDS[1:], dtype=float)
    return released, quantities


def crude_inventory_rebuild(released, quantities):
    stocks = quantities.commercial_crude_thousand_barrels
    refinery = quantities.operable_refinery_utilization_pct
    known = released.notna()
    stocks = stocks.where(known & np.isfinite(stocks) & stocks.gt(0))
    refinery = refinery.where(known & np.isfinite(refinery) & refinery.gt(0) & refinery.le(110))
    weekly_change = stocks.div(stocks.shift()) - 1
    earlier_draws = weekly_change.shift(1).lt(0) & weekly_change.shift(2).lt(0)
    pulse = weekly_change.ge(.005) & earlier_draws & refinery.diff().ge(0)
    practical = released.loc[known] + pd.Timedelta(days=2)
    return pulse.loc[known].groupby(practical).any()


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    pulse = crude_inventory_rebuild(*load_crude_balances()).reindex(btc.index, fill_value=False)
    _BTC = pulse & L1.regime_daily(btc).isin((0., 1.))


def sig_crude_rebuild(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    eligible = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (eligible & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l208_commercial_crude_rebuild_steady_utilization_1d': dict(
        name='[RESEARCH] L208 Commercial crude rebuild at stable US refinery utilization',
        style='momentum', fn=sig_crude_rebuild, sides='long', tf='1d', research_only=True,
        params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
