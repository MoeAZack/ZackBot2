"""Research-only daily point-and-figure breakout using completed closes.

Use multiplicative 2.5% boxes anchored at the first clean close of each
contiguous data segment. A three-box reversal starts a new column. Go long
when the current rising column first prints a box above the last rising
column's top, provided the intervening falling column made a higher low
than the falling column before it. Coin R1 must be rising or sideways.
Only daily closes enter the column logic. Simulate the next daily open.
"""
import math

import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('The signal compares alternating point-and-figure columns, which are '
                  'defined by three-box reversals on quantized prices. V2/V4 use calendar '
                  'Donchian levels and V74 uses time-bar fractals.')


def point_figure_breakout(dates, closes):
    step = math.log1p(.025)
    day = pd.Timedelta(days=1)
    previous_date = None
    anchor = None
    column = extreme = 0
    last_top = last_bottom = None
    rising_support = False
    flags = np.zeros(len(closes), dtype=bool)
    for i, (date, close) in enumerate(zip(dates, closes, strict=True)):
        if not math.isfinite(close) or close <= 0:
            anchor = None
        elif anchor is None or date - previous_date != day:
            anchor = math.log(close)
            column = extreme = 0
            last_top = last_bottom = None
            rising_support = False
        else:
            level = (math.log(close) - anchor) / step
            upper = math.floor(level + 1e-10)
            lower = math.ceil(level - 1e-10)
            if column < 0 and upper >= extreme + 3:
                rising_support = last_bottom is not None and extreme > last_bottom
                last_bottom, column = extreme, 1
                flags[i] = rising_support and last_top is not None and upper > last_top
                extreme = upper
            elif column > 0 and lower <= extreme - 3:
                last_top, extreme, column = extreme, lower, -1
            elif column > 0 and upper > extreme:
                flags[i] = rising_support and last_top is not None and extreme <= last_top < upper
                extreme = upper
            elif column < 0:
                extreme = min(extreme, lower)
            elif column == 0 and upper >= 3:
                column, extreme = 1, upper
            elif column == 0 and lower <= -3:
                column, extreme = -1, lower
        previous_date = date
    return flags


def sig_point_figure(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    if dates.hasnans or dates.tz is not None or not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('daily timestamps must be sorted unique UTC labels')
    close = pd.to_numeric(d.c, errors='coerce')
    close = close.where(np.isfinite(close) & close.gt(0))
    marks = point_figure_breakout(dates, close.to_numpy())
    eligible = np.isin(L1.regime_daily(close).to_numpy(), (0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(marks & eligible, index=d.index)),
                se=flat.copy(), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'l111_point_figure_higher_base_1d': dict(
        name='[RESEARCH] L111 point-and-figure higher-base breakout',
        style='trend', fn=sig_point_figure, sides='long', tf='1d',
        research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=10),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
