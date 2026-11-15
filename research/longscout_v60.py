"""LONGSCOUT-V60 (final round, moe "stop on 60"): two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: weekly inside-bar (inside week) breakouts; earlier rounds used daily inside days only.

L60A l60_inside_week_break_4h  long  complete Mon-Sun week w-1, then complete week w whose high is below w-1's high and whose
                                     low is above w-1's low (inside week). During the following week w+1, the first completed
                                     day that closes above week w's high, coin rising (R1). First 4h bar of the next day
                                     (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S60A s60_inside_week_break_4h  short mirror: first close below the inside week's low during week w+1, coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _inside_v(D, up):
    mon = pd.Series(D.index - pd.to_timedelta(D.index.dayofweek, unit='D'), index=D.index)
    g = D.groupby(mon.values)
    W = pd.DataFrame({'h': g.h.max(), 'l': g.l.min(), 'n': g.size()})
    W = W[W.n == 7]
    step = W.index.to_series().diff() == pd.Timedelta(days=7)
    inside = (W.h < W.h.shift()) & (W.l > W.l.shift()) & step
    nxt = W.index + pd.Timedelta(days=7)                                  # levels apply during the following week
    lvl = mon.map(pd.Series(np.where(inside, W.h if up else W.l, np.nan), index=nxt))
    hit = (D.c > lvl) if up else (D.c < lvl)
    first = hit & ~hit.groupby(mon.values).transform(lambda s: s.shift(fill_value=False).cummax()).astype(bool)
    return first.fillna(False).astype(bool)


def _inside(d, p, up):
    D = L17._days(d)
    return L27._next_day(_inside_v(D, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_inside(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_inside(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l60_inside_week_break_4h': dict(
        name='[RESEARCH] L60A Inside-week breakout in rising regime long 4h', style='trend', fn=sig_up,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
    's60_inside_week_break_4h': dict(
        name='[RESEARCH] S60A Inside-week breakdown in falling regime short 4h', style='trend', fn=sig_dn,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
