"""Research-only daily long on positive market convexity during BTC shocks.

Fit the relationship between each coin's daily log return and BTC's daily
log return over the prior 90 completed days. Partial out BTC's linear
return and require a partial correlation of at least +0.20 between the
coin return and squared BTC return. Buy the first day of a BTC move beyond
3% and the previous 90-day absolute-return 90th percentile, in either
direction. Require rising/sideways BTC and coin R1 and a positive coin close
return. This tests an empirical nonlinearity in past returns.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Tests response to the magnitude of BTC movement after removing linear beta. '
                  'V33 and V93 use linear beta or downside-only linear beta. V97 uses linear '
                  'BTC residuals while BTC is quiet, and V83 fits curvature over calendar time. '
                  'None uses squared BTC shocks as an independent timing factor in both directions.')

_BTC = None


def closes(d):
    t = pd.DatetimeIndex(pd.to_datetime(d.t))
    if t.hasnans or t.tz is not None or not t.is_unique or not t.is_monotonic_increasing or not t.equals(t.normalize()):
        raise ValueError('daily labels must be exact, sorted unique UTC dates')
    data = pd.Series(d.c.to_numpy(dtype=float), index=t)
    return data.where(np.isfinite(data) & data.gt(0))


def set_btc(frame):
    global _BTC
    _BTC = closes(frame)
    _BTC = _BTC.reindex(pd.date_range(_BTC.index[0], _BTC.index[-1], freq='D'))


def convexity(coin_ret, btc_ret):
    """Rolling prior-only partial correlation against squared BTC returns."""
    y, x = coin_ret.shift(), btc_ret.shift()
    z = x * x
    xr, yr, zr = (value.rolling(90, min_periods=90) for value in (x, y, z))
    vx = xr.var().where(lambda v: v > 1e-12)
    xy, xz, yz = xr.cov(y), xr.cov(z), yr.cov(z)
    residual_y = yr.var() - xy * xy / vx
    residual_z = zr.var() - xz * xz / vx
    denominator = np.sqrt(residual_y.where(residual_y > 1e-12) * residual_z.where(residual_z > 1e-12))
    return ((yz - xy * xz / vx) / denominator).clip(-1, 1)


def sig_convexity(d, params, ctx):
    if _BTC is None:
        raise RuntimeError('set_btc required')
    price = closes(d)
    grid = pd.date_range(price.index[0], price.index[-1], freq='D')
    coin = price.reindex(grid)
    btc = _BTC.reindex(grid)
    y, x = np.log(coin / coin.shift()) * 100, np.log(btc / btc.shift()) * 100
    partial = convexity(y, x)
    absolute = x.abs()
    cutoff = absolute.shift().rolling(90, min_periods=90).quantile(.90)
    ready = (partial.ge(.20) & absolute.ge(3) & absolute.gt(cutoff) & y.gt(0)
             & L1.regime_daily(coin).isin((0., 1.)) & L1.regime_daily(btc).isin((0., 1.)))
    edge = (ready & ~ready.shift(fill_value=False)).reindex(price.index).to_numpy()
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(edge, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l114_positive_btc_convexity_1d': dict(
        name='[RESEARCH] L114 positive BTC convexity long', style='momentum',
        fn=sig_convexity, sides='long', tf='1d', research_only=True,
        params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
