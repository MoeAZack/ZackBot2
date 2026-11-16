"""LONGSCOUT-V87: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: return KURTOSIS (tail weight of the daily return distribution). V31 used skewness and the
largest single day; V84 split variance by sign; no earlier round used the fourth moment.

r_t = log(c_t / c_{t-1}); K_j = pandas sample excess kurtosis of r over days j-59..j; R_j = log(c_j / c_{j-60}).
Hypothesis: a trend whose daily moves have the thinnest tails in four months is carried by steady flow, not by a few jumps.
L87A l87_thin_tail_uptrend_4h   long  days j-181..j present; K_j below every K of days j-120..j-1 (first day of a new 120-day low)
                                      and R_j > 0. Coin rising (R1). First 4h bar of the next day (modeled fill 04:00 UTC).
                                      Stop 2.5 ATR, 2R, 30 bars.
S87A s87_thin_tail_downtrend_4h short same K condition and R_j < 0; coin falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _kurt(D, m):
    r = np.log(D.c / D.c.shift())
    return r.rolling(m, min_periods=m).kurt()


def _thin_v(D, p, up):
    m, n = p['m'], p['n']
    K = _kurt(D, m)
    low = (K < K.shift().rolling(n, min_periods=n).min()).fillna(False).astype(bool)
    first = low & ~low.shift(fill_value=False)
    R = np.log(D.c / D.c.shift(m))
    tr = (R > 0) if up else (R < 0)
    return first & tr.fillna(False).astype(bool) & L27._consec(D, m + n + 1).astype(bool)


def _thin(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_thin_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_thin(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_thin(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


IP = dict(m=60, n=120)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l87_thin_tail_uptrend_4h': dict(
        name='[RESEARCH] L87A 60-day return kurtosis at a 120-day low with 60-day gain in rising regime long 4h',
        style='trend', fn=sig_up, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's87_thin_tail_downtrend_4h': dict(
        name='[RESEARCH] S87A 60-day return kurtosis at a 120-day low with 60-day loss in falling regime short 4h',
        style='trend', fn=sig_dn, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
