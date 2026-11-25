"""Research-only range fade using competing first passages of daily closes.

From each historical day's close, classify which 2% barrier was crossed
first over the five following daily closes. Unresolved outcomes count in
the denominator. A start and its outcome become known on the day after
the observation window. In the preceding 120 matured states, require at
least 20 starts following a one-day move in the current direction. Fade
the move if at least 55% of that group reached the opposite barrier first.
Require sideways coin R1 and rising/sideways BTC R1. Fill next daily open.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Learns which of two competing price barriers is reached first after a completed '
                  'move. V105 and V110 learn average next-day returns. V21 and V81 measure '
                  'serial dependence. None conditions on the sequence of barrier crossings.')
_BTC = None


def matured_outcomes(price):
    if len(price) < 7:
        return np.full(len(price), np.nan)
    windows = sliding_window_view(price.to_numpy(), 6)[:-1]
    ready = np.isfinite(windows).all(axis=1) & (windows > 0).all(axis=1)
    with np.errstate(divide='ignore', invalid='ignore'):
        move = windows[:, 1:] / windows[:, :1] - 1
    upper = move >= .02
    lower = move <= -.02
    first_upper = np.where(upper.any(axis=1), upper.argmax(axis=1), 5)
    first_lower = np.where(lower.any(axis=1), lower.argmax(axis=1), 5)
    label = np.where(ready, np.sign(first_lower - first_upper), np.nan)
    return np.r_[np.full(6, np.nan), label]


def side_odds(price, direction):
    returns = price.pct_change(fill_method=None)
    observed = pd.Series(matured_outcomes(price), index=price.index)
    cohort = (direction * returns.shift(6)).ge(.01) & observed.notna()
    count = cohort.rolling(120, min_periods=120).sum()
    wins = (cohort & observed.eq(-direction)).rolling(120, min_periods=120).sum()
    return (direction * returns).ge(.01) & count.ge(20) & wins.ge(.55 * count)


def set_btc(frame):
    global _BTC
    close, _ = L101._native_daily(frame)
    close = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    _BTC = L1.regime_daily(close).isin((0., 1.))


def sig_first_passage(d, params, ctx):
    close, _ = L101._native_daily(d)
    price = close.reindex(pd.date_range(close.index[0], close.index[-1], freq='D'))
    regime = L1.regime_daily(price).eq(0) & _BTC.reindex(price.index, fill_value=False)
    long = regime & side_odds(price, -1)
    short = regime & side_odds(price, 1)
    edge = lambda flags: (flags & ~flags.shift(fill_value=False)).reindex(close.index).to_numpy()
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(edge(long), index=d.index)),
                se=S.X(pd.Series(edge(short), index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r126_competing_first_passage_fade_1d': dict(
        name='[RESEARCH] R126 competing first-passage range fade', style='range',
        fn=sig_first_passage, sides='both', tf='1d', research_only=True,
        params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
