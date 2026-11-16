"""LONGSCOUT-V37: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook definitions); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New families (classic floor pivots, daily hammer candle).

L37A l37_pivot_r1_break_4h   long  classic floor pivots from the last completed UTC day (P = (H+L+C)/3, R1 = 2P - L); a 4h
                                   close crosses above R1 during the next day (first such cross that day), coin rising (R1
                                   regime). Stop 2 ATR, 2R, 12 bars.
S37A s37_pivot_s1_break_4h   short mirror: close crosses below S1 = 2P - H, coin falling.
L37B l37_daily_hammer_low_4h long  the last completed day is a hammer (lower wick >= 2x body, upper wick <= body, body > 0) whose
                                   low is the lowest low of the last 20 completed days, coin rising (R1). First 4h bar of the
                                   next day. Stop 2.5 ATR, 2.5R, 36 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v26 as L26
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first_in(group, x):
    x = pd.Series(np.asarray(x, bool), index=group.index)
    return (x & (x.astype(int).groupby(group.values).cumsum() == 1)).values


def _pivot(d, up):
    D = L27._days_v(d)
    P = (D.h + D.l + D.c) / 3
    lvl = (2 * P - D.l) if up else (2 * P - D.h)
    L = L17._on(lvl, d)
    fresh = L26._day_is_yesterday(D, d)
    L = L.where(fresh)
    day = pd.Series(pd.to_datetime(d.t).dt.normalize().values, index=d.index)
    same = day.shift() == day
    x = ((d.c > L) & (d.c.shift() <= L)) if up else ((d.c < L) & (d.c.shift() >= L))
    x = np.asarray(x & same, bool) & L.notna().values
    return _first_in(day, x)


def sig_pivot_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_pivot(d, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_pivot_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_pivot(d, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _hammer(d, p):
    t = pd.to_datetime(d.t)
    g = pd.DataFrame({'o': d.o.values, 'h': d.h.values, 'l': d.l.values, 'c': d.c.values,
                      'day': t.dt.normalize().values}).groupby('day')
    D = pd.DataFrame({'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last()})[g.size() == 6]
    body = (D.c - D.o).abs()
    lower = np.minimum(D.o, D.c) - D.l
    upper = D.h - np.maximum(D.o, D.c)
    ham = (body > 0) & (lower >= p['wick'] * body) & (upper <= body)
    low = D.l <= D.l.rolling(p['n'], min_periods=p['n']).min()
    ok = ham & low & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_hammer(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_hammer(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


PV = dict(stop_atr=2.0, tp_r=2.0, max_bars=12)
CANDIDATES = {
    'l37_pivot_r1_break_4h': dict(
        name='[RESEARCH] L37A Daily floor-pivot R1 break in rising regime long 4h', style='breakout', fn=sig_pivot_long,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(PV)),
    's37_pivot_s1_break_4h': dict(
        name='[RESEARCH] S37A Daily floor-pivot S1 break in falling regime short 4h', style='breakout', fn=sig_pivot_short,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(PV)),
    'l37_daily_hammer_low_4h': dict(
        name='[RESEARCH] L37B Daily hammer at 20-day low in rising regime long 4h', style='reversal', fn=sig_hammer,
        sides='long', tf='4h', research_only=True, params=dict(wick=2.0, n=20), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
