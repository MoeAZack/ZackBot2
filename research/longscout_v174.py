"""Research-only long after a short-term US equity volatility inversion ends.

Require the previous paired Cboe session's VIX9D/VIX ratio at least 1.10,
then at most .95 with a 10% or larger decline in VIX9D and no rise in VIX.
The two sessions must be no more than four calendar days apart. Observe
one full extra UTC day before using the event. Buy a rising target bar
with nonbear BTC and target R1. Trade at the next daily open.
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

FAMILY_NOVELTY = ('Tests the observed unwind of an inversion in short-dated SPX implied '
                  'volatility. V22 uses a high-low price proxy, V127 compares BTC implied '
                  'with trailing realized volatility, and V134 compares ETH and BTC volatilities.')
CBOE_SHORT_VOL_SHA256 = 'c80e9b86d6be3c2d7af3b2645134d93dafd8a5db8e9a825a6f6c09ee90349081'
MARKET_FEATURE = ('CBOE_SHORT_VOL_SHA256', 'VIX9D and VIX daily S&P 500 implied volatility indices')
MARKET_FEATURE_SOURCE = 'Cboe historical daily index prices'
DEFAULT_DATA = 'C:/Dev/ZackBot_research_cache/longscout_codex_cboe_short_vol_20261125/manifest.json'
_BTC = None


def load_cboe_vol():
    raw = Path(os.environ.get('LONGSCOUT_CBOE_SHORT_VOL', DEFAULT_DATA)).read_bytes()
    if hashlib.sha256(raw).hexdigest() != CBOE_SHORT_VOL_SHA256:
        raise ValueError('expected pinned Cboe daily index quotes')
    data = json.loads(raw)
    fields = ['VIX9D', 'VIX']
    if (data['status'], data['frequency'], data['fields']) != ('COMPLETE_WITH_EXPLICIT_MASKS', '1d', fields):
        raise ValueError('invalid Cboe daily volatility archive contract')
    timestamps, near, month = zip(*data['rows'])
    dates = pd.DatetimeIndex(pd.to_datetime(timestamps))
    if not (dates.is_unique and dates.is_monotonic_increasing and dates.equals(dates.normalize())):
        raise ValueError('Cboe session dates must be sorted unique midnights')
    return pd.DataFrame(dict(VIX9D=near, VIX=month), index=dates, dtype='float64')


def inversion_ends(volatility):
    usable = volatility.where(np.isfinite(volatility) & volatility.gt(0))
    ratio = usable.VIX9D / usable.VIX
    recent = ratio.index.to_series().diff().dt.days.between(1, 4)
    result = (recent & ratio.shift().ge(1.10) & ratio.le(.95)
              & usable.VIX9D.le(.90 * usable.VIX9D.shift()) & usable.VIX.le(usable.VIX.shift()))
    return result.set_axis(result.index + pd.Timedelta(days=1))


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    events = inversion_ends(load_cboe_vol()).reindex(btc.index, fill_value=False)
    _BTC = events & L1.regime_daily(btc).isin((0., 1.))


def sig_equity_vol_relief(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    active = _BTC.reindex(grid.index, fill_value=False) & L1.regime_daily(grid).isin((0., 1.))
    enter = (active & grid.diff().gt(0)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l174_cboe_short_end_vol_inversion_unwind_1d': dict(
        name='[RESEARCH] L174 Equity volatility inversion unwind long', style='momentum',
        fn=sig_equity_vol_relief, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
