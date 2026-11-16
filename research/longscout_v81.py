"""LONGSCOUT-V81: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: the VARIANCE RATIO (statistical persistence of daily returns, not used before), and the
UNCONFIRMED RALLY (a strong 20-day rise on shrinking volume).

R81A r81_variance_ratio_4h   both  days j-60..j present. r_t = log(c_t / c_{t-1}) for days j-59..j. VR = variance of the
                                   overlapping 5-day sums of r over that window / (5 x variance of r) (population variances).
                                   VR_j >= 1.3 and VR_{j-1} < 1.3 (first day returns persist) -> long if the coin is rising (R1),
                                   short if falling. First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30
                                   bars.
S81A s81_unconfirmed_rally_4h short days j-20..j present. Close j >= 1.15 x close j-20, and the mean volume of days j-9..j is at most
                                   0.7 x the mean volume of days j-19..j-10; day j-1 did not meet both (first day). Coin falling
                                   or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _vr_series(D, p):
    n, k = p['n'], p['k']
    c = D.c.values
    out = np.full(len(c), np.nan)
    full = L27._consec(D, n).values
    for j in range(n, len(c)):
        if not full[j]:
            continue
        r = np.diff(np.log(c[j - n:j + 1]))
        v1 = r.var()
        if v1 <= 0:
            continue
        s = np.convolve(r, np.ones(k), 'valid')
        out[j] = s.var() / (k * v1)
    return pd.Series(out, index=D.index)


def _vr_v(D, p):
    vr = _vr_series(D, p)
    return ((vr >= p['lvl']) & (vr.shift() < p['lvl'])).fillna(False)


def _rally_v(D, p):
    n, up, q = p['n'], p['up'], p['vol']
    a = D.v.rolling(10, min_periods=10).mean()
    ok = (D.c >= (1 + up) * D.c.shift(n)) & (a <= q * a.shift(10))
    ok = ok.fillna(False).astype(bool) & L27._consec(D, n).astype(bool)
    return ok & ~ok.shift(fill_value=False)


def _on(fn):
    def f(d, p):
        D = L27._days_v(d)
        return L27._next_day(fn(D, p), D, d)
    return f


_vr, _rally = _on(_vr_v), _on(_rally_v)


def sig_vr(d, p, ctx):
    cr = L1.coin_regime(d); f = _vr(d, p)
    return dict(le=S.X(pd.Series(f & (cr == 1), index=d.index)), se=S.X(pd.Series(f & (cr == -1), index=d.index)),
                lx=_z(d), sx=_z(d))


def sig_rally(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_rally(d, p) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r81_variance_ratio_4h': dict(
        name='[RESEARCH] R81A Daily 60-day variance ratio(5) above 1.3 with regime L/S 4h', style='trend', fn=sig_vr,
        sides='both', tf='4h', research_only=True, params=dict(n=60, k=5, lvl=1.3), mgmt=dict(MG)),
    's81_unconfirmed_rally_4h': dict(
        name='[RESEARCH] S81A +15% 20-day rally on 30% lower volume short 4h', style='reversal', fn=sig_rally,
        sides='short', tf='4h', research_only=True, params=dict(n=20, up=0.15, vol=0.7), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
