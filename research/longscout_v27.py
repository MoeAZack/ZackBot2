"""LONGSCOUT-V27: three new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. Not variants of r6/s16/r17/l24/s26.

R27A r27_daily_staircase_4h        both  the last 3 completed UTC days each made a higher high AND a higher low than the day
                                         before, coin rising (R1) -> long on the first 4h bar of the next day; mirror (3 lower
                                         highs and lower lows, coin falling) -> short. Stop 2.5 ATR, 2R, 30 bars.
R27B r27_close_location_4h         both  mean daily close location (c-l)/(h-l) over the last 10 completed days >= 0.65 (buyers
                                         closing near highs), coin rising (R1) -> long; <= 0.35, coin falling -> short. First
                                         bar of the next day, first such day. Stop 2.5 ATR, 2R, 30 bars.
L27A l27_volume_dryup_near_high_4h long  coin rising (R1); the last 3 completed days each had volume below half the median of the
                                         prior 30 days, and the last close is within 5% of the 20-day high close. First bar of
                                         the next day. Stop 2.5 ATR, 2.5R, 36 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v26 as L26

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _days_v(d):
    t = pd.to_datetime(d.t)
    g = pd.DataFrame({'h': d.h.values, 'l': d.l.values, 'c': d.c.values, 'v': d.v.values,
                      'day': t.dt.normalize().values}).groupby('day')
    D = pd.DataFrame({'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last(), 'v': g.v.sum()})[g.size() == 6]
    return D


def _consec(D, k):
    step = (D.index.to_series().diff() == pd.Timedelta(days=1)).astype(float)
    return step.rolling(k, min_periods=k).sum() == k


def _next_day(ok, D, d):
    first = (pd.to_datetime(d.t).dt.hour == 0).values
    return (L17._on(ok, d) == 1).values & first & L26._day_is_yesterday(D, d)


def _staircase(d, p, up):
    D = _days_v(d)
    k = p['days']
    if up:
        st = (D.h > D.h.shift()) & (D.l > D.l.shift())
    else:
        st = (D.h < D.h.shift()) & (D.l < D.l.shift())
    ok = (st.astype(float).rolling(k, min_periods=k).sum() == k) & _consec(D, k)
    return _next_day(ok, D, d)


def sig_staircase(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_staircase(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_staircase(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _close_loc(d, p, up):
    D = _days_v(d)
    n = p['days']
    loc = ((D.c - D.l) / (D.h - D.l).replace(0, np.nan)).rolling(n, min_periods=n).mean()
    hit = (loc >= p['hi']) if up else (loc <= 1 - p['hi'])
    ok = hit & ~hit.shift(fill_value=False) & _consec(D, n)
    return _next_day(ok, D, d)


def sig_close_location(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_close_loc(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_close_loc(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _dryup(d, p):
    D = _days_v(d)
    med = D.v.rolling(p['base'], min_periods=p['base']).median().shift()
    quiet = (D.v < p['quiet'] * med).astype(float).rolling(p['days'], min_periods=p['days']).sum() == p['days']
    near = D.c >= (1 - p['near']) * D.c.rolling(p['hi_n'], min_periods=p['hi_n']).max()
    ok = quiet & near & _consec(D, p['days'])
    return _next_day(ok, D, d)


def sig_dryup(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_dryup(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r27_daily_staircase_4h': dict(
        name='[RESEARCH] R27A Three-day staircase with regime L/S 4h', style='trend', fn=sig_staircase,
        sides='both', tf='4h', research_only=True, params=dict(days=3), mgmt=dict(MG)),
    'r27_close_location_4h': dict(
        name='[RESEARCH] R27B 10-day close-location pressure with regime L/S 4h', style='momentum', fn=sig_close_location,
        sides='both', tf='4h', research_only=True, params=dict(days=10, hi=0.65), mgmt=dict(MG)),
    'l27_volume_dryup_near_high_4h': dict(
        name='[RESEARCH] L27A Volume dry-up near 20-day high long 4h', style='pullback', fn=sig_dryup,
        sides='long', tf='4h', research_only=True, params=dict(base=30, quiet=0.5, days=3, near=0.05, hi_n=20),
        mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
