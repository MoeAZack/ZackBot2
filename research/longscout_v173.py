"""Research-only long after front-end real Treasury yields decline.

Use the matched five- and ten-year H.15 inflation-indexed par yields.
Over three published sessions (no more than eight calendar days), require
the five-year yield to fall at least 15 basis points while the ten-year
yield stays within five basis points. Trade the first qualifying event,
eligible four UTC midnights after the observation date, with nonbear BTC
and target R1 and an up-day for the target. The archive is a current snapshot.
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

FAMILY_NOVELTY = ('Uses non-crypto real-rate curve rotation while the long end stays stable. '
                  'V137 compares two BTC delivery expiries and V140 measures stablecoin supply.')
TIPS_YIELDS_SHA256 = '5c2285cbbbf7a033c2550ca00fd8ff4cc1bd36d2b5a0678f7531171ff207ebe6'
MARKET_FEATURE = ('TIPS_YIELDS_SHA256', 'matched five- and ten-year US real Treasury yields')
MARKET_FEATURE_SOURCE = 'Federal Reserve H.15 DFII5 and DFII10, retrieved from FRED'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_tips_steepening_20261125/manifest.json'
_BTC = None


def load_yields():
    raw = Path(os.environ.get('LONGSCOUT_TIPS_YIELDS', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != TIPS_YIELDS_SHA256:
        raise ValueError('expected pinned real Treasury yields')
    data = json.loads(raw)
    fields = ['DFII5', 'DFII10']
    if (data['status'], data['frequency'], data['fields'], data['units']) != (
        'COMPLETE_WITH_EXPLICIT_MASKS', '1d', fields, 'percent',
    ):
        raise ValueError('invalid Treasury yield archive contract')
    timestamps, short, long = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(timestamps))
    if not (dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())):
        raise ValueError('Treasury session dates must be sorted unique midnights')
    return pd.DataFrame(dict(DFII5=short, DFII10=long), index=dates, dtype='float64')


def real_curve_steepening(yields):
    paired = yields.where(np.isfinite(yields)).dropna()
    basis_points = np.rint(100. * paired)
    shift = basis_points.diff(3)
    elapsed = paired.index.to_series().diff(3).dt.days
    valid = elapsed.between(3, 8)
    easing = valid & shift.DFII5.le(-15) & shift.DFII10.abs().le(5)
    events = easing & valid.shift(fill_value=False) & ~easing.shift(fill_value=False)
    # The daily bar label marks its open. Source date + 3 closes on date + 4.
    return events.set_axis(events.index + pd.Timedelta(days=3))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    easing = real_curve_steepening(load_yields()).reindex(btc.index, fill_value=False)
    _BTC = easing & L1.regime_daily(btc).isin((0., 1.))


def sig_real_curve_easing(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    entry = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(entry.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l173_tips_curve_front_easing_1d': dict(
        name='[RESEARCH] L173 Long on front-end real Treasury yield easing', style='momentum',
        fn=sig_real_curve_easing, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
