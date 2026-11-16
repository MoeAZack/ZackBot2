"""LONGSCOUT-V78: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: CUP-WITH-HANDLE (and its inverted mirror), a 2-3 month base pattern not tested before.

L78A l78_cup_handle_4h      long  days j-60..j present. Left lip a = day of the highest high in j-60..j-41 (earliest if tied),
                                  level H1. Right lip r = day of the highest high in j-15..j-5 (earliest if tied), level R, with
                                  R >= 0.95 H1. Cup: the lowest low of days a+1..r-1 <= 0.85 H1. Handle: days r+1..j-1 never
                                  above R and never below 0.88 R. Close j above max(H1, R), close j-1 not. Coin rising or
                                  sideways (R1). First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S78A s78_inverted_cup_4h    short mirror on lows: left low L1 (j-60..j-41), right low R (j-15..j-5) <= 1.05 L1; the highest high of
                                  a+1..r-1 >= 1.15 L1; handle never below R and never above 1.12 R; close j below min(L1, R),
                                  close j-1 not; coin falling or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _cup_v(D, p, up):
    n, ln, r0, r1, lip, depth, hd = p['n'], p['left'], p['r_from'], p['r_to'], p['lip'], p['depth'], p['handle']
    h, l, c = D.h.values, D.l.values, D.c.values
    full = L27._consec(D, n).values
    out = np.zeros(len(c), bool)
    for j in range(n, len(c)):
        if not full[j]:
            continue
        if up:
            a = j - n + int(np.argmax(h[j - n:j - ln + 1])); r = j - r0 + int(np.argmax(h[j - r0:j - r1 + 1]))
            A, R = h[a], h[r]
            ok = R >= (1 - lip) * A and l[a + 1:r].min() <= (1 - depth) * A
            ok = ok and h[r + 1:j].max() <= R and l[r + 1:j].min() >= (1 - hd) * R
            lv = max(A, R)
            out[j] = ok and c[j] > lv and c[j - 1] <= lv
        else:
            a = j - n + int(np.argmin(l[j - n:j - ln + 1])); r = j - r0 + int(np.argmin(l[j - r0:j - r1 + 1]))
            A, R = l[a], l[r]
            ok = R <= (1 + lip) * A and h[a + 1:r].max() >= (1 + depth) * A
            ok = ok and l[r + 1:j].min() >= R and h[r + 1:j].max() <= (1 + hd) * R
            lv = min(A, R)
            out[j] = ok and c[j] < lv and c[j - 1] >= lv
    return pd.Series(out, index=D.index)


def _cup(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_cup_v(D, p, up), D, d)


def sig_cup(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_cup(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_inv(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_cup(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


CP = dict(n=60, left=41, r_from=15, r_to=5, lip=0.05, depth=0.15, handle=0.12)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l78_cup_handle_4h': dict(
        name='[RESEARCH] L78A Daily cup-with-handle breakout long 4h', style='trend', fn=sig_cup,
        sides='long', tf='4h', research_only=True, params=dict(CP), mgmt=dict(MG)),
    's78_inverted_cup_4h': dict(
        name='[RESEARCH] S78A Daily inverted cup-with-handle breakdown short 4h', style='trend', fn=sig_inv,
        sides='short', tf='4h', research_only=True, params=dict(CP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
