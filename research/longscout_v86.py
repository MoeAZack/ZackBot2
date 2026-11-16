"""LONGSCOUT-V86: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: ULCER INDEX (depth and duration of pullbacks from the running high). Earlier rounds used
range width, ATR and return variance; none measured drawdown depth inside the trend.

DD_t = c_t / max(c over days t-13..t) - 1 (pullback from the 14-day closing high), UI_j = sqrt(mean DD^2 over days j-29..j).
DU_t = c_t / min(c over days t-13..t) - 1 (bounce from the 14-day closing low), UU_j = sqrt(mean DU^2 over days j-29..j).
L86A l86_smooth_uptrend_4h   long  days j-133..j present; UI_j below every UI of days j-90..j-1 (first day of a new 90-day low:
                                   the shallowest pullbacks in 3 months), close_j = 14-day closing high (no current pullback) and
                                   c_j > c_{j-30}. Coin rising (R1). First 4h bar of the next day (modeled fill 04:00 UTC).
                                   Stop 2.5 ATR, 2R, 30 bars.
S86A s86_smooth_downtrend_4h short mirror: UU_j at a new 90-day low (first day), close_j = 14-day closing low, c_j < c_{j-30};
                                   coin falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _ulcer(D, k, m, up):
    c = D.c
    if up:
        dd = c / c.rolling(k, min_periods=k).max() - 1
    else:
        dd = c / c.rolling(k, min_periods=k).min() - 1
    return np.sqrt((dd ** 2).rolling(m, min_periods=m).mean())


def _smooth_v(D, p, up):
    k, m, n = p['k'], p['m'], p['n']
    U = _ulcer(D, k, m, up)
    low = U < U.shift().rolling(n, min_periods=n).min()
    c = D.c
    if up:
        ext = c >= c.rolling(k, min_periods=k).max()
        tr = c > c.shift(m)
    else:
        ext = c <= c.rolling(k, min_periods=k).min()
        tr = c < c.shift(m)
    hit = (low & ext & tr).fillna(False).astype(bool)
    first = low.fillna(False).astype(bool) & ~low.shift(fill_value=False).fillna(False).astype(bool)
    return hit & first & L27._consec(D, k + m + n - 1).astype(bool)


def _smooth(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_smooth_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_smooth(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_smooth(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


IP = dict(k=14, m=30, n=90)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l86_smooth_uptrend_4h': dict(
        name='[RESEARCH] L86A 30-day ulcer index at a 90-day low at a 14-day high in rising regime long 4h',
        style='trend', fn=sig_up, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's86_smooth_downtrend_4h': dict(
        name='[RESEARCH] S86A 30-day bounce index at a 90-day low at a 14-day low in falling regime short 4h',
        style='trend', fn=sig_dn, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
