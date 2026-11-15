"""LONGSCOUT-V38: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New indicator families on completed daily bars (Parabolic SAR, Aroon, Chaikin
money flow).

R38A r38_daily_psar_flip_4h   both  daily Parabolic SAR (0.02 step, 0.2 max) flips below price at the last completed day with
                                    the coin rising (R1) -> long on the first 4h bar of the next day; flips above price with the
                                    coin falling -> short. Stop 2.5 ATR, 2R, 30 bars.
R38B r38_daily_aroon_4h       both  daily Aroon(25): Aroon-up rises through 70 while Aroon-down is below 30, coin rising -> long;
                                    mirror for shorts with the coin falling. First 4h bar of the next day.
                                    Stop 2.5 ATR, 2.5R, 36 bars.
L38A l38_daily_cmf_4h         long  20-day Chaikin money flow on daily bars rises through +0.2, coin rising (R1). First 4h bar of
                                    the next day. Stop 2.5 ATR, 2.5R, 36 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _psar(h, l, step, mx):
    h = np.asarray(h, float); l = np.asarray(l, float); n = len(h)
    trend = np.zeros(n); sar = np.full(n, np.nan)
    if n < 2:
        return trend
    up = h[1] >= h[0]
    s = l[0] if up else h[0]; ep = h[1] if up else l[1]; af = step
    trend[1] = 1 if up else -1; sar[1] = s
    for i in range(2, n):
        s = s + af * (ep - s)
        if up:
            s = min(s, l[i - 1], l[i - 2])
            if l[i] < s:
                up, s, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, mx)
        else:
            s = max(s, h[i - 1], h[i - 2])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, mx)
        trend[i] = 1 if up else -1; sar[i] = s
    return trend


def _psar_flip(d, p, up):
    D = L27._days_v(d)
    tr = pd.Series(_psar(D.h.values, D.l.values, p['step'], p['max']), index=D.index)
    want = 1 if up else -1
    ok = (tr == want) & (tr.shift() == -want) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_psar(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_psar_flip(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_psar_flip(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _aroon(d, p, up):
    D = L27._days_v(d)
    n = p['n']
    a_up = D.h.rolling(n + 1, min_periods=n + 1).apply(lambda a: 100 * int(np.argmax(a)) / n, raw=True)
    a_dn = D.l.rolling(n + 1, min_periods=n + 1).apply(lambda a: 100 * int(np.argmin(a)) / n, raw=True)
    x, y = (a_up, a_dn) if up else (a_dn, a_up)
    ok = (x >= p['hi']) & (x.shift() < p['hi']) & (y < p['lo']) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_aroon(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_aroon(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_aroon(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _cmf(d, p):
    D = L27._days_v(d)
    n = p['n']
    mfm = ((D.c - D.l) - (D.h - D.c)) / (D.h - D.l).replace(0, np.nan)
    cmf = (mfm.fillna(0) * D.v).rolling(n, min_periods=n).sum() / D.v.rolling(n, min_periods=n).sum()
    ok = (cmf >= p['lvl']) & (cmf.shift() < p['lvl']) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_cmf(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_cmf(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r38_daily_psar_flip_4h': dict(
        name='[RESEARCH] R38A Daily Parabolic SAR flip with regime L/S 4h', style='trend', fn=sig_psar,
        sides='both', tf='4h', research_only=True, params=dict(step=0.02, max=0.2), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'r38_daily_aroon_4h': dict(
        name='[RESEARCH] R38B Daily Aroon(25) trend start with regime L/S 4h', style='trend', fn=sig_aroon,
        sides='both', tf='4h', research_only=True, params=dict(n=25, hi=70.0, lo=30.0), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'l38_daily_cmf_4h': dict(
        name='[RESEARCH] L38A Daily Chaikin money flow +0.2 in rising regime long 4h', style='momentum', fn=sig_cmf,
        sides='long', tf='4h', research_only=True, params=dict(n=20, lvl=0.2), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
