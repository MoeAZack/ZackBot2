"""LONGSCOUT-V90: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: INFORMATION-SHOCK DRIFT (continuation after a day that is extreme in BOTH return z-score
and volume, the crypto analogue of post-announcement drift). V62 faded volume climaxes only at a 20-day price extreme and only
when the day closed against its move; V41 used volume shocks WITHOUT a price move; V72 used O'Neil follow-through days.

r_t = log(c_t / c_{t-1}); sd_j = sample sd of r over days j-60..j-1; z_j = r_j / sd_j; V_j = median volume of days j-60..j-1.
L90A l90_up_shock_drift_4h   long  days j-61..j present; z_j >= 3 and v_j >= 3 V_j. Coin rising or sideways (R1). First 4h bar
                                   of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S90A s90_down_shock_drift_4h short z_j <= -3 and v_j >= 3 V_j; coin falling or sideways (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _shock_v(D, p, up):
    n, zt, vt = p['n'], p['z'], p['vol']
    r = np.log(D.c / D.c.shift())
    sd = r.shift().rolling(n, min_periods=n).std()
    z = r / sd
    V = D.v.shift().rolling(n, min_periods=n).median()
    big = D.v >= vt * V
    hit = ((z >= zt) if up else (z <= -zt)) & big
    return hit.fillna(False).astype(bool) & L27._consec(D, n + 1).astype(bool)


def _shock(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_shock_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_shock(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_shock(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


IP = dict(n=60, z=3.0, vol=3.0)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l90_up_shock_drift_4h': dict(
        name='[RESEARCH] L90A 3-sigma up day on 3x median volume, drift long in rising or sideways regime 4h',
        style='momentum', fn=sig_up, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's90_down_shock_drift_4h': dict(
        name='[RESEARCH] S90A 3-sigma down day on 3x median volume, drift short in falling or sideways regime 4h',
        style='momentum', fn=sig_dn, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
