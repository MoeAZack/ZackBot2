"""LONGSCOUT-V84: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: REALIZED SEMIVARIANCE asymmetry (signed jump variation). V31 used return skewness and
the largest single day; no earlier round split variance into its upside and downside parts.

r_t = log(c_t / c_{t-1}) per day. Over days j-29..j: RSP = sum of r^2 for r > 0, RSN = sum of r^2 for r < 0,
SJ_j = (RSP - RSN) / (RSP + RSN), in [-1, 1]. Literature direction (signed jump variation predicts reversal): variance
dominated by up days marks an over-extended, lottery-like coin; variance dominated by down days marks capitulation.
L84A l84_downside_variance_flush_4h long  days j-31..j present; SJ_j < -0.5 and SJ_{j-1} >= -0.5 (first cross). Coin rising or
                                          sideways (R1). First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R,
                                          30 bars.
S84A s84_upside_variance_spike_4h   short mirror: SJ_j > 0.5 and SJ_{j-1} <= 0.5; coin falling or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _sj(D, n):
    r = np.log(D.c / D.c.shift())
    rsp = (r.clip(lower=0) ** 2).rolling(n, min_periods=n).sum()
    rsn = (r.clip(upper=0) ** 2).rolling(n, min_periods=n).sum()
    return (rsp - rsn) / (rsp + rsn)


def _cross_v(D, p, up):
    n, lvl = p['n'], p['lvl']
    sj = _sj(D, n)
    sp = sj.shift()
    with np.errstate(invalid='ignore'):
        hit = ((sj > lvl) & (sp <= lvl)) if up else ((sj < -lvl) & (sp >= -lvl))
    return hit.fillna(False).astype(bool) & L27._consec(D, n + 1).astype(bool)


def _cross(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_cross_v(D, p, up), D, d)


def sig_flush(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_cross(d, p, False) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_spike(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_cross(d, p, True) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


IP = dict(n=30, lvl=0.5)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l84_downside_variance_flush_4h': dict(
        name='[RESEARCH] L84A 30-day signed semivariance first below -0.5 in rising or sideways regime long 4h',
        style='reversal', fn=sig_flush, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's84_upside_variance_spike_4h': dict(
        name='[RESEARCH] S84A 30-day signed semivariance first above +0.5 in falling or sideways regime short 4h',
        style='reversal', fn=sig_spike, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
