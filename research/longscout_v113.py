"""Research-only range fade on an unexpected return for its UTC bar slot.

At each completed 4h bar, estimate the return median and MAD from the prior
30 occurrences of that exact UTC slot. The observations must be consecutive
days with valid four-hour returns. Fade a deviation of at least three robust
standard deviations and 2.5 percentage points in the sideways R1 regime.
Initial stop, profit target and holding cap use the frozen simulator.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('The range filter normalizes each completed 4h return against prior returns in '
                  'the same UTC slot. V47 holds fixed sessions, V93 uses raw half-day displacement, '
                  'and V106 learns the next day’s direction by weekday. None uses slot-specific '
                  'robust dispersion to identify a range fade.')


def slot_deviations(d):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    if dates.hasnans or dates.tz is not None or not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('4h labels must be sorted and unique')
    if not dates.equals(dates.floor('4h')):
        raise ValueError('4h labels must be exact UTC slots')
    close = pd.Series(d.c.to_numpy(dtype=float), index=dates).where(lambda c: np.isfinite(c) & c.gt(0))
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4))
    returns = np.log(close / close.shift()).where(contiguous).to_numpy()
    deviation = np.full(len(d), np.nan)
    limit = np.full(len(d), np.nan)
    for hour in range(0, 24, 4):
        at = np.flatnonzero(dates.hour == hour)
        if len(at) <= 30:
            continue
        history = sliding_window_view(returns[at], 30)[:-1]
        active = at[30:]
        consecutive = dates[active] - dates[at[:-30]] == pd.Timedelta(days=30)
        valid = consecutive & np.isfinite(history).all(axis=1) & np.isfinite(returns[active])
        med = np.median(history[valid], axis=1)
        mad = np.median(np.abs(history[valid] - med[:, None]), axis=1)
        deviation[active[valid]] = returns[active[valid]] - med
        limit[active[valid]] = np.maximum(.025, 3 * 1.4826 * mad)
    return deviation, limit


def sig_slot_fade(d, params, ctx):
    deviation, threshold = slot_deviations(d)
    sideways = L1.coin_regime(d) == 0
    long = sideways & (deviation <= -threshold)
    short = sideways & (deviation >= threshold)
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(long, index=d.index)), se=S.X(pd.Series(short, index=d.index)),
                lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r113_utc_slot_displacement_fade_4h': dict(
        name='[RESEARCH] R113 slot-specific return deviation fade', style='range',
        fn=sig_slot_fade, sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=18),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
