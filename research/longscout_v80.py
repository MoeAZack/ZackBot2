"""LONGSCOUT-V80 (final round, moe "stop at 80"): two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family members: TRIPLE top / bottom (V54 tested double top / bottom on 40 days; this needs a
third test of the level over 60 days, a separate hypothesis frozen as its own candidate).

S80A s80_triple_top_4h     short days j-60..j present. Peak 1 = day of the highest high in j-60..j-41, peak 2 in j-40..j-21, peak
                                 3 in j-20..j-3 (earliest if tied). The highest of the three peak highs is at most 3% above the
                                 lowest. Neckline = the lowest low of days peak 1..peak 3, at least 5% below the lowest peak.
                                 Close j below the neckline and close j-1 not. Coin falling or sideways (R1). First 4h bar of
                                 the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L80A l80_triple_bottom_4h  long  mirror on lows: three troughs within 3%, neckline = highest high of trough 1..trough 3 at least 5%
                                 above the highest trough, first close above it; coin rising or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _triple_v(D, p, top):
    tol, depth = p['tol'], p['depth']
    h, l, c = D.h.values, D.l.values, D.c.values
    full = L27._consec(D, 60).values
    out = np.zeros(len(c), bool)
    wins = ((60, 41), (40, 21), (20, 3))
    for j in range(60, len(c)):
        if not full[j]:
            continue
        x = h if top else l
        pick = np.argmax if top else np.argmin
        ix = [j - a + int(pick(x[j - a:j - b + 1])) for a, b in wins]
        v = [x[i] for i in ix]
        if max(v) > (1 + tol) * min(v):
            continue
        if top:
            neck = l[ix[0]:ix[2] + 1].min()
            out[j] = neck <= (1 - depth) * min(v) and c[j] < neck and c[j - 1] >= neck
        else:
            neck = h[ix[0]:ix[2] + 1].max()
            out[j] = neck >= (1 + depth) * max(v) and c[j] > neck and c[j - 1] <= neck
    return pd.Series(out, index=D.index)


def _triple(d, p, top):
    D = L27._days_v(d)
    return L27._next_day(_triple_v(D, p, top), D, d)


def sig_top(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_triple(d, p, True) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


def sig_bottom(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_triple(d, p, False) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


TP = dict(tol=0.03, depth=0.05)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's80_triple_top_4h': dict(
        name='[RESEARCH] S80A Daily triple-top neckline break short 4h', style='reversal', fn=sig_top,
        sides='short', tf='4h', research_only=True, params=dict(TP), mgmt=dict(MG)),
    'l80_triple_bottom_4h': dict(
        name='[RESEARCH] L80A Daily triple-bottom neckline break long 4h', style='reversal', fn=sig_bottom,
        sides='long', tf='4h', research_only=True, params=dict(TP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
