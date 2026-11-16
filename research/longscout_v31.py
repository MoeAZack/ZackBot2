"""LONGSCOUT-V31: three new research-only 4h candidates (Claude Projects, 2026-11-15). Scouting resumed by moe.

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families (return shape, distance to a one-year high, day of week); not variants of
r6/s16/r17/l24/s26.

R31A r31_return_skew_regime_4h  both  skewness of the last 60 completed daily returns <= -0.5 (crash-prone, unloved) while the
                                      coin is rising (R1) -> long; skewness >= +0.5 (lottery-like) while falling -> short.
                                      First such day; first 4h bar of the next day. Stop 2.5 ATR, 2R, 30 bars.
L31A l31_year_high_proximity_4h long  the last completed day's close rose to within 10% of the highest close of the prior 365
                                      completed days for the first time in 30 days, coin rising (R1); first 4h bar of the
                                      next day. Stop 3 ATR, 3R, 90 bars.
L31B l31_weekend_hold_4h        long  enter at the close of the Friday 20:00 UTC bar (the weekend open), coin rising (R1); exit
                                      after 12 bars (two days). Stop 2.5 ATR, no target. Day-of-week effect.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v26 as L26
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _skew(d, p, up):
    D = L27._days_v(d)
    n = p['n']
    r = D.c.pct_change()
    sk = r.rolling(n, min_periods=n).skew()
    hit = (sk <= -p['lim']) if up else (sk >= p['lim'])
    ok = hit & ~hit.shift(fill_value=False) & L27._consec(D, n + 1)
    return L27._next_day(ok, D, d)


def sig_skew(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_skew(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_skew(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _year_high(d, p):
    D = L27._days_v(d)
    hi = D.c.rolling(p['n'], min_periods=p['n']).max().shift()
    near = (D.c >= (1 - p['near']) * hi).astype(float)
    fresh = near.shift().rolling(p['gap'], min_periods=p['gap']).sum() == 0
    ok = (near == 1) & fresh & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_year_high(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_year_high(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_weekend(d, p, ctx):
    cr = L1.coin_regime(d)
    t = pd.to_datetime(d.t)
    fri = ((t.dt.weekday == 4) & (t.dt.hour == 20)).values
    return dict(le=S.X(pd.Series(fri & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r31_return_skew_regime_4h': dict(
        name='[RESEARCH] R31A 60-day return skew with regime L/S 4h', style='momentum', fn=sig_skew,
        sides='both', tf='4h', research_only=True, params=dict(n=60, lim=0.5), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'l31_year_high_proximity_4h': dict(
        name='[RESEARCH] L31A Within 10% of one-year high long 4h', style='trend', fn=sig_year_high,
        sides='long', tf='4h', research_only=True, params=dict(n=365, near=0.10, gap=30), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=90)),
    'l31_weekend_hold_4h': dict(
        name='[RESEARCH] L31B Weekend hold in rising regime long 4h', style='calendar', fn=sig_weekend,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=12)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
