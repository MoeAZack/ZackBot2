"""LONGSCOUT-V76: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: two-point TRENDLINE breaks (a line through two swing extremes, projected forward).
V57 used converging wedges/triangles and V67 a least-squares channel, not a two-anchor trendline.

L76A l76_downtrend_line_break_4h long  days j-60..j present. p1 = day of the highest high in j-60..j-21, p2 = day of the highest
                                       high in j-20..j-3 (earliest day if tied), with high p2 < high p1. Line(t) = high p1 +
                                       (high p2 - high p1) x (t - p1) / (p2 - p1). Close j > Line(j) and close j-1 <= Line(j-1). Coin rising or sideways
                                       (R1). First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S76A s76_uptrend_line_break_4h short  mirror through the lowest lows (low p2 > low p1); close j < Line(j) and close j-1 >= Line(j-1);
                                       coin falling or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _line_v(D, p, down):
    n, mid, last = p['n'], p['mid'], p['last']
    h, l, c = D.h.values, D.l.values, D.c.values
    x = h if down else l
    full = L27._consec(D, n).values
    out = np.zeros(len(c), bool)
    for j in range(n, len(c)):
        if not full[j]:
            continue
        w1, w2 = x[j - n:j - mid], x[j - mid:j - last + 1]
        if down:
            p1 = j - n + int(np.argmax(w1)); p2 = j - mid + int(np.argmax(w2))
            if not x[p2] < x[p1]:
                continue
        else:
            p1 = j - n + int(np.argmin(w1)); p2 = j - mid + int(np.argmin(w2))
            if not x[p2] > x[p1]:
                continue
        s = (x[p2] - x[p1]) / (p2 - p1)
        now, prev = x[p1] + s * (j - p1), x[p1] + s * (j - 1 - p1)
        out[j] = (c[j] > now and c[j - 1] <= prev) if down else (c[j] < now and c[j - 1] >= prev)
    return pd.Series(out, index=D.index)


def _line(d, p, down):
    D = L27._days_v(d)
    return L27._next_day(_line_v(D, p, down), D, d)


def sig_down(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_line(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_line(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


LP = dict(n=60, mid=20, last=3)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l76_downtrend_line_break_4h': dict(
        name='[RESEARCH] L76A Daily two-point downtrend line break long 4h', style='reversal', fn=sig_down,
        sides='long', tf='4h', research_only=True, params=dict(LP), mgmt=dict(MG)),
    's76_uptrend_line_break_4h': dict(
        name='[RESEARCH] S76A Daily two-point uptrend line break short 4h', style='reversal', fn=sig_up,
        sides='short', tf='4h', research_only=True, params=dict(LP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
