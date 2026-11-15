"""LONGSCOUT-V74: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: the Williams FRACTAL high as a breakout level, and the PRIOR CALENDAR MONTH's high/low
(earlier rounds used the prior week, a 6-month close range, and in V70 the current month's open).

L74A l74_fractal_break_4h    long  an up fractal is a day k whose high is above the highs of days k-2, k-1, k+1 and k+2 (known
                                   from day k+2). On day j take the latest fractal k with j-20 <= k <= j-3; close j above high k
                                   and close j-1 at or below it. Days j-22..j present; coin rising (R1). First 4h bar of the next
                                   day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
R74A r74_prior_month_break_4h both  every day of the previous calendar month and every day from the 1st of day j's month to j is
                                   present. Close j above the previous month's highest high, first close of this month to do so
                                   -> long if coin rising (R1); close j below the previous month's lowest low, first this month
                                   -> short if falling. Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _frac_v(D, p):
    n = p['n']
    h, c = D.h.values, D.c.values
    m = len(c)
    up = np.zeros(m, bool)
    for k in range(2, m - 2):
        up[k] = h[k] > max(h[k - 2], h[k - 1], h[k + 1], h[k + 2])
    full = L27._consec(D, n + 2).values
    out = np.zeros(m, bool)
    for j in range(n + 2, m):
        if not full[j]:
            continue
        ks = [k for k in range(j - 3, j - n - 1, -1) if up[k]]
        if ks:
            lv = h[ks[0]]
            out[j] = c[j] > lv and c[j - 1] <= lv
    return pd.Series(out, index=D.index)


def _pm_v(D, up):
    idx = pd.DatetimeIndex(D.index); h, l, c = D.h.values, D.l.values, D.c.values
    pos = {t: i for i, t in enumerate(idx)}
    out = np.zeros(len(c), bool)
    for j, t in enumerate(idx):
        first = t.replace(day=1)
        prev = (first - pd.Timedelta(days=1)).replace(day=1)
        span = (t - prev).days
        if prev not in pos or pos[prev] != j - span:
            continue
        p0, f0 = pos[prev], j - (t - first).days
        if up:
            lv = h[p0:f0].max()
            out[j] = c[j] > lv and not (c[f0:j] > lv).any()
        else:
            lv = l[p0:f0].min()
            out[j] = c[j] < lv and not (c[f0:j] < lv).any()
    return pd.Series(out, index=D.index)


def sig_frac(d, p, ctx):
    cr = L1.coin_regime(d); D = L27._days_v(d)
    return dict(le=S.X(pd.Series(L27._next_day(_frac_v(D, p), D, d) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_pm(d, p, ctx):
    cr = L1.coin_regime(d); D = L27._days_v(d)
    return dict(le=S.X(pd.Series(L27._next_day(_pm_v(D, True), D, d) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(L27._next_day(_pm_v(D, False), D, d) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l74_fractal_break_4h': dict(
        name='[RESEARCH] L74A Daily up-fractal breakout in rising regime long 4h', style='trend', fn=sig_frac,
        sides='long', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
    'r74_prior_month_break_4h': dict(
        name='[RESEARCH] R74A Prior calendar-month high/low close break with regime L/S 4h', style='trend', fn=sig_pm,
        sides='both', tf='4h', research_only=True, params=dict(), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
