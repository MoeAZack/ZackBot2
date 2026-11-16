"""LONGSCOUT-V89: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: VOLUME CONCENTRATION (Herfindahl index of daily dollar volume). Earlier volume rounds used
levels, spikes, dry-ups, OBV, close location and return-volume correlation; none measured how evenly volume is spread.

q_t = c_t x v_t; over days j-29..j: H_j = sum (q_t / sum q)^2, in [1/30, 1]. R_j = log(c_j / c_{j-30}).
Hypothesis: a move carried by evenly spread daily participation (low H) is broad-based demand or supply, not one event day.
L89A l89_broad_volume_uptrend_4h   long  days j-150..j present; H_j below every H of days j-120..j-1 (first day of a new 120-day
                                         low) and R_j > 0. Coin rising (R1). First 4h bar of the next day (modeled fill
                                         04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S89A s89_broad_volume_downtrend_4h short same H condition and R_j < 0; coin falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _hhi(D, m):
    q = D.c * D.v
    tot = q.rolling(m, min_periods=m).sum()
    sq = (q ** 2).rolling(m, min_periods=m).sum()
    return sq / tot ** 2


def _broad_v(D, p, up):
    m, n = p['m'], p['n']
    H = _hhi(D, m)
    low = (H < H.shift().rolling(n, min_periods=n).min()).fillna(False).astype(bool)
    first = low & ~low.shift(fill_value=False)
    R = np.log(D.c / D.c.shift(m))
    tr = (R > 0) if up else (R < 0)
    return first & tr.fillna(False).astype(bool) & L27._consec(D, m + n).astype(bool)


def _broad(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_broad_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_broad(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_broad(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


IP = dict(m=30, n=120)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l89_broad_volume_uptrend_4h': dict(
        name='[RESEARCH] L89A 30-day dollar-volume concentration at a 120-day low with 30-day gain in rising regime long 4h',
        style='trend', fn=sig_up, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's89_broad_volume_downtrend_4h': dict(
        name='[RESEARCH] S89A 30-day dollar-volume concentration at a 120-day low with 30-day loss in falling regime short 4h',
        style='trend', fn=sig_dn, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
