"""LONGSCOUT-V63: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: volatility-contraction sequence (three successively tighter 10-day ranges) before a
30-day break. Earlier base rules (V10 tight base, V29 squeeze) measured one window, not a contracting sequence.

L63A l63_vcp_breakout_4h   long  days j-30..j present. Range of a block = (max high - min low) / max high. Blocks A = days
                                 j-30..j-21, B = j-20..j-11, C = j-10..j-1. Require A > B > C and C <= half of A. Day j closes
                                 above the highest high of days j-30..j-1; coin rising (R1). First 4h bar of the next day
                                 (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S63A s63_vcp_breakdown_4h  short same contraction; day j closes below the lowest low of days j-30..j-1; coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _vcp_v(D, p, up):
    w = p['w']
    def block(k):                                                         # block ending k days before j (k=1 -> j-w..j-1)
        hi = D.h.shift(k).rolling(w, min_periods=w).max(); lo = D.l.shift(k).rolling(w, min_periods=w).min()
        return (hi - lo) / hi
    a, b, c = block(2 * w + 1), block(w + 1), block(1)
    tight = (a > b) & (b > c) & (c <= p['ratio'] * a)
    if up:
        brk = D.c > D.h.shift().rolling(3 * w, min_periods=3 * w).max()
    else:
        brk = D.c < D.l.shift().rolling(3 * w, min_periods=3 * w).min()
    return (tight & brk & L27._consec(D, 3 * w)).fillna(False)


def _vcp(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_vcp_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_vcp(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_vcp(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


VP = dict(w=10, ratio=0.5)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l63_vcp_breakout_4h': dict(
        name='[RESEARCH] L63A Three-step volatility contraction then 30-day breakout long 4h', style='trend', fn=sig_up,
        sides='long', tf='4h', research_only=True, params=dict(VP), mgmt=dict(MG)),
    's63_vcp_breakdown_4h': dict(
        name='[RESEARCH] S63A Three-step volatility contraction then 30-day breakdown short 4h', style='trend', fn=sig_dn,
        sides='short', tf='4h', research_only=True, params=dict(VP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
