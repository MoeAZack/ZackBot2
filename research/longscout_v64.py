"""LONGSCOUT-V64: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: up-day frequency ("win-rate momentum") over 20 consecutive daily closes, which counts
how often the coin rose rather than how far (earlier momentum rules used return size or short streaks).

L64A l64_up_day_ratio_4h    long  of the last 20 completed day-over-day changes (days j-19..j vs the day before), at least 14
                                  are up, and on day j-1 fewer than 14 were (first day); coin rising (R1). First 4h bar of the
                                  next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S64A s64_down_day_ratio_4h  short mirror: at least 14 of 20 down, first day; coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _ratio_v(D, p, up):
    n, k = p['n'], p['k']
    ch = D.c.diff()
    hit = ((ch > 0) if up else (ch < 0)).astype(float)
    cnt = hit.rolling(n, min_periods=n).sum()
    ok = (cnt >= k) & (cnt.shift() < k)
    return (ok & L27._consec(D, n + 1)).fillna(False)


def _ratio(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_ratio_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_ratio(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_ratio(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


RP = dict(n=20, k=14)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l64_up_day_ratio_4h': dict(
        name='[RESEARCH] L64A 14 of 20 up days in rising regime long 4h', style='momentum', fn=sig_up,
        sides='long', tf='4h', research_only=True, params=dict(RP), mgmt=dict(MG)),
    's64_down_day_ratio_4h': dict(
        name='[RESEARCH] S64A 14 of 20 down days in falling regime short 4h', style='momentum', fn=sig_dn,
        sides='short', tf='4h', research_only=True, params=dict(RP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
