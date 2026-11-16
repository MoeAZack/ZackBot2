"""LONGSCOUT-V73: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: on-balance-volume DIVERGENCE at a 20-day closing extreme (V28/V9 used OBV only as a
confirming cross; V58 tested RSI divergence, not volume).

Daily OBV: OBV_t = OBV_{t-1} + v_t if c_t > c_{t-1}, - v_t if c_t < c_{t-1}, unchanged otherwise.
L73A l73_obv_bull_div_4h  long  days j-20..j present. Close j is below every close of days j-20..j-1; let a = the latest day in
                                j-20..j-1 holding the lowest of those closes; OBV_j > OBV_a (price lower low, OBV higher low).
                                Coin rising or sideways (R1). First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5
                                ATR, 2R, 30 bars.
S73A s73_obv_bear_div_4h  short mirror: close j above every close of days j-20..j-1, a = latest day holding the highest of those
                                closes, OBV_j < OBV_a; coin falling or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _div_v(D, p, bull):
    n = p['n']
    c, v = D.c.values, D.v.values
    obv = np.r_[0.0, np.cumsum(np.sign(np.diff(c)) * v[1:])]
    full = L27._consec(D, n).values
    out = np.zeros(len(c), bool)
    for j in range(n, len(c)):
        if not full[j]:
            continue
        w = c[j - n:j]
        ext = w.min() if bull else w.max()
        if not (c[j] < ext if bull else c[j] > ext):
            continue
        a = j - n + int(np.flatnonzero(w == ext)[-1])
        out[j] = obv[j] > obv[a] if bull else obv[j] < obv[a]
    return pd.Series(out, index=D.index)


def _div(d, p, bull):
    D = L27._days_v(d)
    return L27._next_day(_div_v(D, p, bull), D, d)


def sig_bull(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_div(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_bear(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_div(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l73_obv_bull_div_4h': dict(
        name='[RESEARCH] L73A Daily OBV bullish divergence at a 20-day closing low long 4h', style='reversal',
        fn=sig_bull, sides='long', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
    's73_obv_bear_div_4h': dict(
        name='[RESEARCH] S73A Daily OBV bearish divergence at a 20-day closing high short 4h', style='reversal',
        fn=sig_bear, sides='short', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
