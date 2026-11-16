"""LONGSCOUT-V83: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: QUADRATIC trend fit (curvature). Earlier rounds used linear slope / t-stat (V7, V36) and a
linear channel (V67), never a second-order fit.

Fit y = a + b x + c x^2 by least squares to the log closes of days j-29..j (x = 0..29). Slope at the last day
s_j = b + 2 c x_last.
L83A l83_convex_turn_up_4h    long  days j-30..j present; curvature c > 0 and s_j > 0, while the fit for day j-1 (days j-30..j-1)
                                    had s_{j-1} <= 0 (first upturn of a convex curve). Coin rising or sideways (R1). First 4h bar
                                    of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S83A s83_concave_turn_down_4h short mirror: c < 0, s_j < 0, s_{j-1} >= 0; coin falling or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _fit(D, n):
    y = np.log(D.c.values)
    x = np.arange(n, dtype=float)
    X = np.vstack([np.ones(n), x, x * x]).T
    P = np.linalg.pinv(X)
    slope = np.full(len(y), np.nan); curv = np.full(len(y), np.nan)
    for j in range(n - 1, len(y)):
        a, b, c = P @ y[j - n + 1:j + 1]
        slope[j] = b + 2 * c * (n - 1); curv[j] = c
    return slope, curv


def _turn_v(D, p, up):
    n = p['n']
    s, cv = _fit(D, n)
    sp = np.r_[np.nan, s[:-1]]
    with np.errstate(invalid='ignore'):
        ok = ((cv > 0) & (s > 0) & (sp <= 0)) if up else ((cv < 0) & (s < 0) & (sp >= 0))
    return pd.Series(ok, index=D.index) & L27._consec(D, n).astype(bool)


def _turn(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_turn_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_turn(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_turn(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l83_convex_turn_up_4h': dict(
        name='[RESEARCH] L83A Daily 30-day quadratic fit convex upturn long 4h', style='reversal', fn=sig_up,
        sides='long', tf='4h', research_only=True, params=dict(n=30), mgmt=dict(MG)),
    's83_concave_turn_down_4h': dict(
        name='[RESEARCH] S83A Daily 30-day quadratic fit concave downturn short 4h', style='reversal', fn=sig_dn,
        sides='short', tf='4h', research_only=True, params=dict(n=30), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
