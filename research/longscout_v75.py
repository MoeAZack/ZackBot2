"""LONGSCOUT-V75: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: ASCENDING / DESCENDING triangles (a flat side tested twice plus a sloping side); V57
tested only the symmetric triangle and wedges.

L75A l75_ascending_triangle_4h  long  days j-30..j present. T = highest high of days j-30..j-1. At least two days in j-30..j-1 have a
                                      high >= 0.98 T, and the first and last of them are at least 5 days apart (flat top). The
                                      lowest low of days j-10..j-1 is at least 5% above the lowest low of days j-30..j-21 (rising
                                      lows). Close j > T. Coin rising or sideways (R1). First 4h bar of the next day (modeled fill
                                      04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S75A s75_descending_triangle_4h short mirror: B = lowest low of j-30..j-1; two days with low <= 1.02 B at least 5 days apart; the
                                      highest high of j-10..j-1 at least 5% below the highest high of j-30..j-21 (falling
                                      highs); close j < B; coin falling or sideways. Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _tri_v(D, p, asc):
    n, tol, gap, rec, slope = p['n'], p['tol'], p['gap'], p['rec'], p['slope']
    h, l, c = D.h.values, D.l.values, D.c.values
    full = L27._consec(D, n).values
    out = np.zeros(len(c), bool)
    for j in range(n, len(c)):
        if not full[j]:
            continue
        hw, lw = h[j - n:j], l[j - n:j]
        if asc:
            T = hw.max(); touch = np.flatnonzero(hw >= (1 - tol) * T)
            side = lw[-rec:].min() >= (1 + slope) * lw[:rec].min()
            brk = c[j] > T
        else:
            T = lw.min(); touch = np.flatnonzero(lw <= (1 + tol) * T)
            side = hw[-rec:].max() <= (1 - slope) * hw[:rec].max()
            brk = c[j] < T
        out[j] = brk and side and touch[-1] - touch[0] >= gap
    return pd.Series(out, index=D.index)


def _tri(d, p, asc):
    D = L27._days_v(d)
    return L27._next_day(_tri_v(D, p, asc), D, d)


def sig_asc(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_tri(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_desc(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_tri(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


TP = dict(n=30, tol=0.02, gap=5, rec=10, slope=0.05)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l75_ascending_triangle_4h': dict(
        name='[RESEARCH] L75A Daily 30-day ascending triangle breakout long 4h', style='trend', fn=sig_asc,
        sides='long', tf='4h', research_only=True, params=dict(TP), mgmt=dict(MG)),
    's75_descending_triangle_4h': dict(
        name='[RESEARCH] S75A Daily 30-day descending triangle breakdown short 4h', style='trend', fn=sig_desc,
        sides='short', tf='4h', research_only=True, params=dict(TP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
