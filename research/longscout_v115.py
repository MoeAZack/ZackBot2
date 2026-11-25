"""Research-only long on a rise in cross-sectional return co-movement.

For each completed UTC day, include coins with 61 consecutive daily closes
and median quote volume of at least $10m over the prior 30 days. Exclude BTC
from the peer universe. Calculate each eligible coin's trailing 30-day
correlation to the equal-weight return of its other eligible peers at each
date. At least 50 peers are required in each benchmark and 50 usable
correlations are required to take the median. Buy positive 10-day coins
when this market median first crosses up through 0.50 with BTC and the
target in rising or sideways R1. Simulate the next daily open.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Tests whether cross-sectional returns begin to move together over time. '
                  'V92 counts positive returns, V94 measures the cross-sectional spread on one '
                  'day, and V108 ranks return levels. Their inputs do not estimate leave-one-out '
                  'peer-return correlations.')

_MEDIAN = _BTC = None


def median_peer_correlation(returns):
    count = returns.notna().sum(axis=1)
    total = returns.sum(axis=1, min_count=51)
    benchmark = returns.rsub(total, axis=0).div(count - 1, axis=0)
    correlation = returns.rolling(30, min_periods=30).corr(benchmark, pairwise=False)
    correlation = correlation.where(np.isfinite(correlation))
    return correlation.median(axis=1).where(correlation.notna().sum(axis=1).ge(50))


def set_panel(panel):
    global _MEDIAN, _BTC
    _MEDIAN = _BTC = None
    btc, _ = L101._native_daily(panel['BTCUSDT'])
    rows = {s: L101._native_daily(d) for s, d in panel.items() if s != 'BTCUSDT'}
    dates = pd.date_range(btc.index[0], btc.index[-1], freq='D')
    close = pd.DataFrame({s: c.reindex(dates) for s, (c, _) in rows.items()})
    volume = pd.DataFrame({s: v.reindex(dates) for s, (_, v) in rows.items()})
    ready = close.notna().rolling(61, min_periods=61).sum().eq(61)
    ready &= volume.shift().rolling(30, min_periods=30).median().ge(1e7)
    returns = np.log(close / close.shift()).where(ready)
    _MEDIAN = median_peer_correlation(returns)
    _BTC = L1.regime_daily(btc.reindex(dates))


def sig_peer_correlation(d, params, ctx):
    close, _ = L101._native_daily(d)
    dates = close.index
    days = pd.date_range(dates[0], dates[-1], freq='D')
    price = close.reindex(days)
    positive = price.gt(price.shift(10)) & price.notna().rolling(11, min_periods=11).sum().eq(11)
    cross = _MEDIAN.ge(.50) & _MEDIAN.shift().lt(.50)
    hits = cross.reindex(days, fill_value=False) & positive
    hits &= L1.regime_daily(price).isin((0., 1.)) & _BTC.reindex(days).isin((0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(hits.reindex(dates).to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l115_peer_correlation_thrust_1d': dict(
        name='[RESEARCH] L115 peer correlation thrust', style='momentum', fn=sig_peer_correlation,
        sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
