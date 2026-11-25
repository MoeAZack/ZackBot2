"""Research-only weekly-open range fade on complete daily candles.

Freeze a weekly box around Monday's UTC open using the last complete week's
high-low span. Fade a close back through the +/-35% bands toward the open
only when the coin's daily R1 regime is sideways. Exit on the first close
at the open, beyond +/-70% of the same span, or on Sunday. An incomplete
week suppresses entries and asks open positions to exit. The engine models
signals at the next open and keeps its native ATR stop, target and time cap.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('This fades a completed reentry into a weekly-open box toward the open. '
                  'V12/V74 break prior period extremes, and V70 follows a monthly-open '
                  'directional cross. Neither fades failed displacement toward a fixed weekly anchor.')


def weekly_box(d):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    if dates.hasnans or dates.tz is not None or not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('daily timestamps must be unique sorted UTC labels')
    if (dates != dates.normalize()).any():
        raise ValueError('daily timestamps must be on UTC midnight')
    week = dates.to_period('W-SUN').start_time
    weekday = pd.Series(dates.dayofweek, index=d.index)
    prices = d[['o', 'h', 'l', 'c']].where(lambda p: np.isfinite(p) & (p > 0))
    valid = prices.notna().all(axis=1) & (prices.h >= prices.l)
    clean = prices.where(valid, axis=0)
    weekly = clean.groupby(week).agg({'h': 'max', 'l': 'min', 'o': 'count'})
    previous_week = week - pd.Timedelta(days=7)
    prior_range = (weekly.h - weekly.l).where(weekly.o.eq(7)).reindex(previous_week)
    width = pd.Series(prior_range.to_numpy(), index=d.index)
    monday_open = clean.o.where(weekday.eq(0)).groupby(week).ffill()
    complete_to_date = valid.groupby(week).cumsum().eq(weekday + 1)
    anchor = monday_open.where(complete_to_date & width.gt(0))
    return anchor, width, weekday


def sig_weekly_box(d, params, ctx):
    anchor, width, weekday = weekly_box(d)
    low, high = anchor - .35 * width, anchor + .35 * width
    sideways = L1.regime_daily(d.c).to_numpy() == 0
    same_box = weekday.between(1, 5) & anchor.notna() & anchor.shift().notna()
    close, previous = d.c, d.c.shift()
    long_entry = same_box & sideways & previous.le(low) & close.gt(low) & close.lt(anchor)
    short_entry = same_box & sideways & previous.ge(high) & close.lt(high) & close.gt(anchor)
    box_closed = weekday.eq(6) | anchor.isna()
    long_exit = box_closed | close.ge(anchor) | close.le(anchor - .70 * width)
    short_exit = box_closed | close.le(anchor) | close.ge(anchor + .70 * width)
    return dict(le=S.X(long_entry), se=S.X(short_entry), lx=S.X(long_exit), sx=S.X(short_exit))


CANDIDATES = {
    'r107_weekly_open_box_fade_1d': dict(
        name='[RESEARCH] R107 weekly-open box fade', style='range', fn=sig_weekly_box,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
