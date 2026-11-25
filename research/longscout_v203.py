"""Research-only long when an improvement in excess returns comes from the downside.

Compare sorted daily coin-minus-BTC log returns from two adjoining complete
24-day blocks. The six lowest return quantiles must improve by at least 40
basis points on average. The six highest may improve by at most 40 basis
points, and the median must improve by at least 15 basis points. Buy the
first qualifying bar in a nonbear coin and a nonbear BTC regime if the
coin rises that day. Equal BTC subtraction is fixed across the universe.
"""
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Compares the signed transport of empirical quantiles between consecutive '
                  'excess-return blocks. V84 compares the variance of positive and negative '
                  'returns in one window, and V101 classifies the z-score of a peer spread.')
WINDOW = 24
_BTC = _BTC_REGIME = None


def lower_tail_recovers(excess):
    flags = np.zeros(len(excess), dtype=bool)
    if len(excess) >= 2 * WINDOW:
        pairs = sliding_window_view(excess.to_numpy(dtype=float), 2 * WINDOW)
        complete = np.isfinite(pairs).all(axis=1)
        prior = np.sort(pairs[complete, :WINDOW], axis=1)
        recent = np.sort(pairs[complete, WINDOW:], axis=1)
        displacement = recent - prior
        quartile = WINDOW // 4
        lower = displacement[:, :quartile].mean(axis=1)
        upper = displacement[:, -quartile:].mean(axis=1)
        median_change = np.median(recent, axis=1) - np.median(prior, axis=1)
        ends = np.flatnonzero(complete) + 2 * WINDOW - 1
        flags[ends] = (lower >= .004) & (upper <= .004) & (median_change >= .0015)
    return pd.Series(flags, index=excess.index)


def set_btc(frame):
    global _BTC, _BTC_REGIME
    btc, _ = L101._native_daily(frame)
    _BTC = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC_REGIME = L1.regime_daily(_BTC).isin((0., 1.))


def sig_lower_tail_recovers(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    market = _BTC.reindex(grid.index)
    excess = np.log(grid).diff() - np.log(market).diff()
    qualifying = (lower_tail_recovers(excess) & _BTC_REGIME.reindex(grid.index, fill_value=False)
                  & L1.regime_daily(grid).isin((0., 1.)) & grid.diff().gt(0))
    enter = (qualifying & ~qualifying.shift(fill_value=False)).reindex(close.index)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(enter.to_numpy(), index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l203_excess_returns_lower_quantile_recovery_1d': dict(
        name='[RESEARCH] L203 downside-led shift in coin excess-return distribution', style='momentum',
        fn=sig_lower_tail_recovers, sides='long', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
