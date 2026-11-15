"""LONGSCOUT-V62: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: volume CLIMAX reversal days at a 20-day extreme (earlier volume rounds were
continuation or absorption rules, not exhaustion at an extreme).

S62A s62_buying_climax_4h  short day j makes a new 20-day high (above days j-20..j-1), its volume is at least 3x the average of
                                 days j-20..j-1, and it closes in the lower half of its own range; coin falling or sideways
                                 (R1). First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L62A l62_selling_climax_4h long  mirror: new 20-day low, volume >= 3x average, close in the upper half; coin rising or sideways.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _climax_v(D, p, top):
    n, k = p['n'], p['k']
    vol = D.v > k * D.v.shift().rolling(n, min_periods=n).mean()
    pos = (D.c - D.l) / (D.h - D.l).replace(0, np.nan)
    if top:
        ok = (D.h > D.h.shift().rolling(n, min_periods=n).max()) & vol & (pos < 0.5)
    else:
        ok = (D.l < D.l.shift().rolling(n, min_periods=n).min()) & vol & (pos > 0.5)
    return (ok & L27._consec(D, n)).fillna(False)


def _climax(d, p, top):
    D = L27._days_v(d)
    return L27._next_day(_climax_v(D, p, top), D, d)


def sig_top(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_climax(d, p, True) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


def sig_bottom(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_climax(d, p, False) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


CP = dict(n=20, k=3.0)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's62_buying_climax_4h': dict(
        name='[RESEARCH] S62A Daily 3x-volume buying climax at a 20-day high short 4h', style='reversal', fn=sig_top,
        sides='short', tf='4h', research_only=True, params=dict(CP), mgmt=dict(MG)),
    'l62_selling_climax_4h': dict(
        name='[RESEARCH] L62A Daily 3x-volume selling climax at a 20-day low long 4h', style='reversal', fn=sig_bottom,
        sides='long', tf='4h', research_only=True, params=dict(CP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
