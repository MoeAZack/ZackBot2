"""LONGSCOUT-V82: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: Amihud ILLIQUIDITY (price impact per dollar traded); no earlier round used a
liquidity measure.

ILLIQ_t = |log(c_t / c_{t-1})| / (c_t x v_t) per day; A_t = mean of ILLIQ over days t-19..t.
L82A l82_liquidity_improves_4h long  days j-140..j present. A_j below every A of days j-120..j-1 (a new 120-day low of the
                                     20-day illiquidity: trading is the easiest it has been), and A_{j-1} was not below every A of
                                     days j-121..j-2 (first day). Coin rising (R1). First 4h bar of the next day (modeled fill
                                     04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S82A s82_liquidity_dries_4h    short mirror: A_j above every A of days j-120..j-1 (first day); coin falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _illiq_v(D, p, low):
    m, n = p['m'], p['n']
    il = np.log(D.c / D.c.shift()).abs() / (D.c * D.v)
    A = il.rolling(m, min_periods=m).mean()
    if low:
        hit = A < A.shift().rolling(n, min_periods=n).min()
    else:
        hit = A > A.shift().rolling(n, min_periods=n).max()
    hit = hit.fillna(False).astype(bool) & L27._consec(D, n + m).astype(bool)
    return hit & ~hit.shift(fill_value=False)


def _illiq(d, p, low):
    D = L27._days_v(d)
    return L27._next_day(_illiq_v(D, p, low), D, d)


def sig_low(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_illiq(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_high(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_illiq(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


IP = dict(m=20, n=120)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l82_liquidity_improves_4h': dict(
        name='[RESEARCH] L82A 20-day Amihud illiquidity at a 120-day low in rising regime long 4h', style='trend',
        fn=sig_low, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's82_liquidity_dries_4h': dict(
        name='[RESEARCH] S82A 20-day Amihud illiquidity at a 120-day high in falling regime short 4h', style='trend',
        fn=sig_high, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
