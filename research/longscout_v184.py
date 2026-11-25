"""Research-only range fade at the intersection of three completed weeks.

Freeze the overlap of three prior full UTC Monday-Sunday high-low ranges.
Require overlap at least 60% of their union. During Monday through Thursday
decisions, fade a shallow wick through an edge when the close returns to
the outer quarter. Only trade sideways targets in nonbear BTC. Exit at
the middle, on a close outside the box, on a regime change, or Sunday.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import longscout_v101 as L101
import strategies as S

FAMILY_NOVELTY = ('Freezes the intersection of three nonoverlapping weekly ranges with elapsed-time '
                  'expiry. V45 uses the most extreme Donchian closes, V100 compares close '
                  'distributions, and V102 estimates volume-weighted auction acceptance.')
_BTC = None


def completed_week_box(high, low):
    valid = np.isfinite(high) & np.isfinite(low) & high.ge(low) & low.gt(0)
    complete = valid.resample('W-SUN').sum().eq(7)
    weekly_high = high.where(valid).resample('W-SUN').max().where(complete)
    weekly_low = low.where(valid).resample('W-SUN').min().where(complete)
    high_windows = weekly_high.rolling(3, min_periods=3)
    low_windows = weekly_low.rolling(3, min_periods=3)
    bottom, top = low_windows.max(), high_windows.min()
    intersection = top - bottom
    union = high_windows.max() - low_windows.min()
    enough = intersection.gt(0) & intersection.ge(.60 * union)
    weekly = pd.DataFrame(dict(bottom=bottom.where(enough), top=top.where(enough)))
    weekly.index += pd.Timedelta(days=1)
    return weekly.reindex(high.index, method='ffill', tolerance=pd.Timedelta(days=6))


def flags_for_box(high, low, close, box, regime):
    bottom, top = box.bottom, box.top
    width = top - bottom
    midpoint = bottom + width * .50
    valid = close.between(low, high) & width.gt(0)
    decision = pd.Series(close.index.dayofweek, index=close.index)
    can_enter = valid & regime & decision.le(3)
    lower_test = low.lt(bottom) & low.ge(bottom - .15 * width)
    upper_test = high.gt(top) & high.le(top + .15 * width)
    long = can_enter & lower_test & high.le(top) & close.between(bottom, bottom + .25 * width)
    short = can_enter & upper_test & low.ge(bottom) & close.between(top - .25 * width, top)
    common_exit = ~valid | ~regime | decision.eq(6)
    exit_long = common_exit | close.ge(midpoint) | close.lt(bottom)
    exit_short = common_exit | close.le(midpoint) | close.gt(top)
    return long, short, exit_long, exit_short


def set_btc(frame):
    global _BTC
    btc, _ = L101._native_daily(frame)
    btc = btc.reindex(pd.date_range(btc.index[0], btc.index[-1], freq='D'))
    _BTC = L1.regime_daily(btc).isin((0., 1.))


def sig_weekly_box(d, params, ctx):
    close, _ = L101._native_daily(d)
    grid = pd.date_range(close.index[0], close.index[-1], freq='D')
    price = close.reindex(grid)
    bars = d[['h', 'l']].set_axis(close.index).reindex(grid)
    box = completed_week_box(bars.h, bars.l)
    adjacent = price.notna().shift(fill_value=False)
    approved = _BTC.reindex(grid, fill_value=False) & L1.regime_daily(price).eq(0) & adjacent
    flags = flags_for_box(bars.h, bars.l, price, box, approved)
    return {name: S.X(pd.Series(flag.reindex(close.index).to_numpy(), index=d.index))
            for name, flag in zip(('le', 'se', 'lx', 'sx'), flags)}


CANDIDATES = {
    'r184_overlap_of_three_completed_weeks_1d': dict(
        name='[RESEARCH] R184 Preceding weekly range overlap fade', style='range',
        fn=sig_weekly_box, sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=5),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
