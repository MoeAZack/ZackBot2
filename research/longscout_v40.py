"""LONGSCOUT-V40: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families (multi-indicator consensus vote, dual momentum vs BTC, relative-weakness short).

R40A r40_trend_consensus_4h     both  five daily trend votes (close > SMA50, SMA50 > SMA100, MACD(12,26,9) histogram > 0,
                                      close above the 20-day Donchian midline, 20-day return > 0) reach 5 of 5 for the first
                                      time in 10 days with the coin rising (R1) -> long; 0 of 5 with the coin falling -> short.
                                      First 4h bar of the next day. Stop 2.5 ATR, 2.5R, 36 bars.
L40A l40_dual_momentum_4h       long  the coin's 90-day return is positive AND beats BTC's 90-day return, for the first day in
                                      20, coin not falling (R1). First 4h bar of the next day. Stop 3 ATR, 3R, 60 bars.
S40A s40_relative_weakness_4h   short the coin's 30-day return trails BTC's by >= 20 points for the first day in 20, coin falling
                                      (R1). First 4h bar of the next day. Stop 3 ATR, 2.5R, 60 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v33 as L33

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _votes(D):
    c = D.c
    s50 = c.rolling(50, min_periods=50).mean(); s100 = c.rolling(100, min_periods=100).mean()
    m = _ema(c, 12) - _ema(c, 26); h = m - _ema(m, 9)
    mid = (D.h.rolling(20, min_periods=20).max() + D.l.rolling(20, min_periods=20).min()) / 2
    roc = c / c.shift(20) - 1
    parts = [c > s50, s50 > s100, h > 0, c > mid, roc > 0]
    known = s100.notna() & h.notna() & mid.notna() & roc.notna()
    return sum(x.astype(int) for x in parts).where(known)


def _consensus(d, p, up):
    D = L27._days_v(d)
    v = _votes(D)
    hit = (v == 5) if up else (v == 0)
    fresh = hit.astype(float).shift().rolling(p['gap'], min_periods=p['gap']).sum() == 0
    ok = hit & fresh & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_consensus(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_consensus(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_consensus(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _rel(d, n):
    D, J = L33._joint(d)
    rc = J.c / J.c.shift(n) - 1
    rb = J.b / J.b.shift(n) - 1
    full = J.index.to_series().diff(n) == pd.Timedelta(days=n)
    return D, rc.where(full), rb.where(full)


def _dual(d, p):
    D, rc, rb = _rel(d, p['n'])
    hit = (rc > 0) & (rc > rb)
    fresh = hit.astype(float).shift().rolling(p['gap'], min_periods=p['gap']).sum() == 0
    return L27._next_day(hit & fresh, D, d)


def sig_dual(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_dual(d, p) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _weak(d, p):
    D, rc, rb = _rel(d, p['n'])
    hit = rc <= rb - p['gap_pts']
    fresh = hit.astype(float).shift().rolling(p['gap'], min_periods=p['gap']).sum() == 0
    return L27._next_day(hit & fresh, D, d)


def sig_weak(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_weak(d, p) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r40_trend_consensus_4h': dict(
        name='[RESEARCH] R40A Five-vote daily trend consensus with regime L/S 4h', style='trend', fn=sig_consensus,
        sides='both', tf='4h', research_only=True, params=dict(gap=10), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'l40_dual_momentum_4h': dict(
        name='[RESEARCH] L40A 90-day dual momentum vs BTC long 4h', style='momentum', fn=sig_dual,
        sides='long', tf='4h', research_only=True, params=dict(n=90, gap=20), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    's40_relative_weakness_4h': dict(
        name='[RESEARCH] S40A 30-day relative weakness vs BTC short 4h', style='momentum', fn=sig_weak,
        sides='short', tf='4h', research_only=True, params=dict(n=30, gap_pts=0.20, gap=20),
        mgmt=dict(stop_atr=3.0, tp_r=2.5, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
