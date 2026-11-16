"""LONGSCOUT-V54: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: classical chart patterns (double bottom / double top) on completed daily bars.

L54A l54_double_bottom_4h  long  on the last completed day j (with days j-40..j all present): trough 1 = lowest low of days
                                 j-40..j-21, trough 2 = lowest low of days j-20..j-3, the two within 3% of each other; the
                                 neckline (highest high between the troughs) is at least 5% above the higher trough; day j
                                 closes above the neckline and day j-1 did not. Coin rising or sideways (R1). First 4h bar
                                 of the next day. Stop 2.5 ATR, 2R, 30 bars.
S54A s54_double_top_4h     short mirror: peaks within 3%, neckline (lowest low between the peaks) at least 5% below the lower
                                 peak, close below the neckline for the first time; coin falling or sideways (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _pattern_v(D, p, bottom):
    """Boolean per completed day: day j breaks the neckline of a double bottom (or top) built on days j-40..j-1."""
    h, l, c = D.h.values, D.l.values, D.c.values
    n = len(c)
    full = L27._consec(D, 40).values                                     # days j-40..j are consecutive
    out = np.zeros(n, bool)
    for j in range(41, n):
        if not full[j]:
            continue
        if bottom:
            i1 = j - 40 + int(np.argmin(l[j - 40:j - 20])); i2 = j - 20 + int(np.argmin(l[j - 20:j - 2]))
            a, b = l[i1], l[i2]
            neck = h[i1:i2 + 1].max()
            ok = abs(b / a - 1) <= p['tol'] and neck >= (1 + p['depth']) * max(a, b)
            out[j] = ok and c[j] > neck and c[j - 1] <= neck
        else:
            i1 = j - 40 + int(np.argmax(h[j - 40:j - 20])); i2 = j - 20 + int(np.argmax(h[j - 20:j - 2]))
            a, b = h[i1], h[i2]
            neck = l[i1:i2 + 1].min()
            ok = abs(b / a - 1) <= p['tol'] and neck <= (1 - p['depth']) * min(a, b)
            out[j] = ok and c[j] < neck and c[j - 1] >= neck
    return pd.Series(out, index=D.index)


def _pattern(d, p, bottom):
    D = L27._days_v(d)
    return L27._next_day(_pattern_v(D, p, bottom), D, d)


def sig_bottom(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_pattern(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_top(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_pattern(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


DP = dict(tol=0.03, depth=0.05)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l54_double_bottom_4h': dict(
        name='[RESEARCH] L54A Daily double-bottom neckline break long 4h', style='reversal', fn=sig_bottom,
        sides='long', tf='4h', research_only=True, params=dict(DP), mgmt=dict(MG)),
    's54_double_top_4h': dict(
        name='[RESEARCH] S54A Daily double-top neckline break short 4h', style='reversal', fn=sig_top,
        sides='short', tf='4h', research_only=True, params=dict(DP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
