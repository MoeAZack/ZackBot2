"""LONGSCOUT-V53: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New families (daily indicators never used before): Ichimoku Tenkan/Kijun cross,
Coppock curve turn, and Pring's Know Sure Thing (KST).

R53A r53_daily_tk_cross_4h    both  daily Tenkan(9) crosses above Kijun(26) -> long if coin rising (R1); below -> short if
                                    falling. First 4h bar of the next day. Stop 2.5 ATR, 2R, 30 bars.
L53A l53_daily_coppock_4h     long  coin rising (R1); daily Coppock curve WMA10(ROC14 + ROC11) is below 0 and turns up (today
                                    above yesterday, yesterday not above the day before). Same management.
R53B r53_daily_kst_4h         both  daily KST(10/15/20/30; SMA 10/10/10/15; weights 1-4) crosses its SMA9 signal up -> long if
                                    rising; down -> short if falling. Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _mid(D, n):
    return (D.h.rolling(n, min_periods=n).max() + D.l.rolling(n, min_periods=n).min()) / 2


def _tk_v(D):
    return _mid(D, 9), _mid(D, 26)


def _roc(c, n):
    return 100 * (c / c.shift(n) - 1)


def _wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    return x.rolling(n, min_periods=n).apply(lambda a: np.dot(a, w) / w.sum(), raw=True)


def _coppock_v(D):
    return _wma(_roc(D.c, 14) + _roc(D.c, 11), 10)


def _kst_v(D):
    sm = lambda x, n: x.rolling(n, min_periods=n).mean()
    k = (sm(_roc(D.c, 10), 10) + 2 * sm(_roc(D.c, 15), 10) + 3 * sm(_roc(D.c, 20), 10) + 4 * sm(_roc(D.c, 30), 15))
    return k, sm(k, 9)


def _cross(a, b, up):
    return ((a > b) & (a.shift() <= b.shift())) if up else ((a < b) & (a.shift() >= b.shift()))


def _tk(d, p, up):
    D = L27._days_v(d)
    t, k = _tk_v(D)
    return L27._next_day(_cross(t, k, up) & L27._consec(D, 2), D, d)


def _coppock(d, p):
    D = L27._days_v(d)
    x = _coppock_v(D)
    ok = (x < 0) & (x > x.shift()) & (x.shift() <= x.shift(2))
    return L27._next_day(ok & L27._consec(D, 3), D, d)


def _kst(d, p, up):
    D = L27._days_v(d)
    k, s = _kst_v(D)
    return L27._next_day(_cross(k, s, up) & L27._consec(D, 2), D, d)


def _both(fn):
    def sig(d, p, ctx):
        cr = L1.coin_regime(d)
        return dict(le=S.X(pd.Series(fn(d, p, True) & (cr == 1), index=d.index)),
                    se=S.X(pd.Series(fn(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))
    return sig


def sig_coppock(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_coppock(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


sig_tk, sig_kst = _both(_tk), _both(_kst)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r53_daily_tk_cross_4h': dict(
        name='[RESEARCH] R53A Daily Ichimoku Tenkan/Kijun cross with regime L/S 4h', style='trend', fn=sig_tk,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
    'l53_daily_coppock_4h': dict(
        name='[RESEARCH] L53A Daily Coppock curve turn below zero in rising regime long 4h', style='momentum',
        fn=sig_coppock, sides='long', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
    'r53_daily_kst_4h': dict(
        name='[RESEARCH] R53B Daily Know Sure Thing signal cross with regime L/S 4h', style='momentum', fn=sig_kst,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
