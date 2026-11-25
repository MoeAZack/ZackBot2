"""Research-only daily range fade based on ordered historical analogues.

Represent the latest five log returns in order, scaling each by its
previous-30-return volatility. Compare with 240 earlier paths whose
features and three-day outcomes end before the current path begins.
Freeze the 15 nearest observed paths by Euclidean distance and use
their three-day close return median. Fade a down/up day only when
at least 11 of 15 outcomes support the opposite side and their median
exceeds 0.7% in magnitude. Both regimes must allow non-bear evidence,
with the coin sideways. No adaptive parameter selection.
"""
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Matches chronological return paths to fully matured historical neighbours. '
                  'V105/V110 condition on single event categories and V100 compares unordered '
                  'return distributions. None learns from ordered nearest paths.')
_BTC = None


def history_features(price):
    values = price.to_numpy()
    log = np.log(price)
    daily = log.diff()
    scale = daily.shift().rolling(30, min_periods=30).std()
    normalized = (daily / scale.where(scale > 0)).to_numpy()
    paths = np.full((len(price), 5), np.nan)
    labels = np.full(len(price), np.nan)
    if len(price) >= 5:
        paths[4:] = sliding_window_view(normalized, 5)
    if len(price) >= 4:
        maturity = sliding_window_view(values, 4)
        valid = np.isfinite(maturity).all(axis=1)
        labels[:-3] = np.where(valid, maturity[:, -1] / maturity[:, 0] - 1, np.nan)
    return paths, labels


def analogue_statistics(paths, labels):
    result = np.full((len(paths), 3), np.nan)
    valid = np.isfinite(paths).all(axis=1)
    known = valid & np.isfinite(labels)
    for i in range(248, len(paths)):
        if not valid[i]:
            continue
        source = np.arange(i - 248, i - 8)
        source = source[known[source]]
        if len(source) < 200:
            continue
        distance = np.square(paths[source] - paths[i]).sum(axis=1)
        match = source[np.argsort(distance, kind='stable')[:15]]
        outcome = labels[match]
        result[i] = [np.median(outcome), (outcome > 0).sum(), (outcome < 0).sum()]
    return result


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = L1.regime_daily(close).isin((0., 1.))


def sig_analogue_fade(d, params, ctx):
    close, _ = L101._native_daily(d)
    dates = close.index
    price = close.reindex(pd.date_range(dates[0], dates[-1], freq='D'))
    median, up, down = analogue_statistics(*history_features(price)).T
    range_state = L1.regime_daily(price).eq(0) & _BTC.reindex(price.index, fill_value=False)
    long = range_state & price.diff().lt(0) & (up >= 11) & (median >= .007)
    short = range_state & price.diff().gt(0) & (down >= 11) & (median <= -.007)
    edge = lambda pattern: (pattern & ~pattern.shift(fill_value=False)).reindex(dates).to_numpy()
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(edge(long), index=d.index)),
                se=S.X(pd.Series(edge(short), index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r133_ordered_historical_analogue_fade_1d': dict(
        name='[RESEARCH] R133 ordered historical analogue range fade', style='range', fn=sig_analogue_fade,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
