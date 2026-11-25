"""Research-only long after pressure in the expensive tail of Treasury repo eases.

Subtract median SOFR from its 99th percentile in basis points. Require
yesterday's gap at least six basis points and 1.5 times the prior 20-session
median, followed by a return to no more than that median. The typical
rate must move no more than two basis points. Both rows must be within
four calendar days. Use the outcome five UTC midnights after the repo
session. Require nonbear BTC and target R1 with positive target return.
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

FAMILY_NOVELTY = ('Tests normalization of the upper tail of overnight dollar collateral funding '
                  'while its typical rate holds. V173 compares maturities of Treasury real '
                  'yields, and V143 tests relative crypto futures funding.')
REPO_DISPERSION_SHA256 = 'e6a8bb94a05b950dad569de35e772860533fd37af3e7a3bc11c74c33d733fc36'
MARKET_FEATURE = ('REPO_DISPERSION_SHA256', 'SOFR and its 99th-percentile transaction rate')
MARKET_FEATURE_SOURCE = 'Federal Reserve Bank of New York SOFR historical reference-rate API'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_repo_dispersion_20261125/manifest.json'
_BTC = None


def load_repo_rates():
    raw = Path(os.environ.get('LONGSCOUT_REPO_DISPERSION', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != REPO_DISPERSION_SHA256:
        raise ValueError('expected pinned New York Fed SOFR rates')
    data = json.loads(raw)
    fields = ['percentRate', 'percentPercentile99']
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', fields, 'percent',
    ):
        raise ValueError('invalid repo archive contract')
    timestamps, median, upper = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(timestamps))
    if not (dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())):
        raise ValueError('repo session dates must be sorted unique midnights')
    return pd.DataFrame(dict(median=median, upper=upper), index=dates, dtype='float64')


def tail_eases(rates):
    usable = rates.where(np.isfinite(rates))
    median, upper = np.rint(100. * usable['median']), np.rint(100. * usable.upper)
    gap = (upper - median).where(upper.ge(median))
    typical_gap = gap.shift(2).rolling(20, min_periods=20).median()
    previous = gap.shift()
    stressed = previous.ge(6) & previous.ge(1.50 * typical_gap)
    recent = gap.index.to_series().diff().dt.days.between(1, 4)
    settled = recent & stressed & gap.le(typical_gap) & median.diff().abs().le(2)
    return settled.set_axis(settled.index + pd.Timedelta(days=4))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    relief = tail_eases(load_repo_rates()).reindex(btc.index, fill_value=False)
    _BTC = relief & L1.regime_daily(btc).isin((0., 1.))


def sig_sofr_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l175_sofr_upper_tail_relief_1d': dict(
        name='[RESEARCH] L175 Long after Treasury repo upper tail eases', style='momentum',
        fn=sig_sofr_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
