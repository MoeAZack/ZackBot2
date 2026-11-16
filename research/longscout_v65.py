"""LONGSCOUT-V65: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: DAILY anchored VWAP from the 180-day extreme (V5 used a 4h VWAP anchored at a recent
low; V11/V32 used rolling 30-day VWAPs).

S65A s65_high_avwap_loss_4h   short days j-180..j present. Anchor a = the day with the highest high in j-180..j-1, at least 5
                                    days before j. AVWAP_t = sum(typical price x volume, a..t) / sum(volume, a..t), typical =
                                    (h+l+c)/3. Day j closes below AVWAP_j and day j-1 closed at or above AVWAP_{j-1} (same
                                    anchor); coin falling or sideways (R1). First 4h bar of the next day (modeled fill
                                    04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L65A l65_low_avwap_reclaim_4h long  mirror: anchor at the lowest low of j-180..j-1, first close above its AVWAP; coin rising or
                                    sideways.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _avwap_v(D, p, high):
    n, age = p['n'], p['age']
    h, l, c, v = D.h.values, D.l.values, D.c.values, D.v.values
    tp = (h + l + c) / 3
    cpv = np.r_[0.0, np.cumsum(tp * v)]; cv = np.r_[0.0, np.cumsum(v)]
    full = L27._consec(D, n).values
    out = np.zeros(len(c), bool)
    for j in range(n, len(c)):
        if not full[j]:
            continue
        w = slice(j - n, j)
        a = j - n + int(np.argmax(h[w]) if high else np.argmin(l[w]))
        if a > j - age:
            continue
        now = (cpv[j + 1] - cpv[a]) / (cv[j + 1] - cv[a])
        prev = (cpv[j] - cpv[a]) / (cv[j] - cv[a])
        out[j] = (c[j] < now and c[j - 1] >= prev) if high else (c[j] > now and c[j - 1] <= prev)
    return pd.Series(out, index=D.index)


def _avwap(d, p, high):
    D = L27._days_v(d)
    return L27._next_day(_avwap_v(D, p, high), D, d)


def sig_loss(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_avwap(d, p, True) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


def sig_reclaim(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_avwap(d, p, False) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


AP = dict(n=180, age=5)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's65_high_avwap_loss_4h': dict(
        name='[RESEARCH] S65A Close below the VWAP anchored at the 180-day high short 4h', style='trend', fn=sig_loss,
        sides='short', tf='4h', research_only=True, params=dict(AP), mgmt=dict(MG)),
    'l65_low_avwap_reclaim_4h': dict(
        name='[RESEARCH] L65A Close above the VWAP anchored at the 180-day low long 4h', style='trend', fn=sig_reclaim,
        sides='long', tf='4h', research_only=True, params=dict(AP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
