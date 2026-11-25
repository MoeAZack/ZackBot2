"""Research-only long on an equal-turnover acceleration in 4h observations.

At a bucket's first completed 4h bar, freeze its turnover target at six
times the median of the preceding 42 valid 4h bars. Close the bucket at
the first completed bar meeting that target. A bar is indivisible and
belongs to exactly one bucket. Buy after two falling buckets followed
by a rising bucket that finishes strictly faster than either decline.
Use rising/sideways coin R1 and simulate the next 4h open.
"""
from collections import deque

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Entry depends on elapsed time at a comparable turnover budget. V99 compares '
                  'volume shares within calendar days, V85/V95 use calendar-bar volume correlations, '
                  'and V110 learns reversals after fixed-day sign runs. None bins completed turnover '
                  'before comparing elapsed durations.')


def turnover_flags(d):
    t = pd.DatetimeIndex(pd.to_datetime(d.t))
    if t.hasnans or t.tz is not None or not t.is_unique or not t.is_monotonic_increasing or not t.equals(t.floor('4h')):
        raise ValueError('4h timestamps must be sorted unique UTC 4h labels')
    qv = d.qv.to_numpy(dtype=float)
    close = d.c.to_numpy(dtype=float)
    valid = np.isfinite(qv) & (qv > 0) & np.isfinite(close) & (close > 0)
    consecutive = t.to_series().diff().eq(pd.Timedelta(hours=4)).to_numpy()
    baseline = pd.Series(np.where(valid, qv, np.nan)).shift().rolling(42, min_periods=42).median().to_numpy()
    continuous = pd.Series(consecutive).rolling(42, min_periods=42).sum().eq(42).to_numpy()
    flags = np.zeros(len(d), dtype=bool)
    target, used, duration = None, 0.0, 0
    last_close = None
    previous = deque(maxlen=2)
    for i in range(len(d)):
        if not valid[i] or not consecutive[i]:
            target, used, duration = None, 0.0, 0
            last_close = None
            previous.clear()
            continue
        if target is None:
            if not continuous[i] or not np.isfinite(baseline[i]):
                continue
            target = 6.0 * baseline[i]
        used += qv[i]
        duration += 1
        if used < target:
            continue
        if last_close is not None:
            sign = np.sign(close[i] - last_close)
            flags[i] = (sign > 0 and len(previous) == 2
                        and all(old_sign < 0 and duration < span for old_sign, span in previous))
            previous.append((sign, duration))
        last_close = close[i]
        target, used, duration = None, 0.0, 0
    return flags


def sig_turnover_acceleration(d, params, ctx):
    ready = turnover_flags(d) & np.isin(L1.coin_regime(d), (0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(ready, index=d.index)), se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l112_turnover_clock_acceleration_4h': dict(
        name='[RESEARCH] L112 turnover-clock acceleration long', style='momentum',
        fn=sig_turnover_acceleration, sides='long', tf='4h', research_only=True,
        params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
