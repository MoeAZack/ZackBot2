"""LONGSCOUT-V85: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: PRICE-VOLUME CORRELATION (the Pearson correlation of the daily return with the day's log
volume). Earlier volume rounds used volume levels, spikes, OBV and close location; none correlated volume with return sign.

r_t = log(c_t / c_{t-1}); u_t = log(v_t). rho_j = corr(r, u) over days j-29..j; R_j = log(c_j / c_{j-30}).
L85A l85_volume_confirms_rally_4h  long  days j-31..j present; rho_j > 0.4 and rho_{j-1} <= 0.4 (first cross: heavier volume on up
                                         days) and R_j > 0. Coin rising (R1). First 4h bar of the next day (modeled fill
                                         04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S85A s85_volume_confirms_selloff_4h short mirror: rho_j < -0.4 and rho_{j-1} >= -0.4 (heavier volume on down days) and R_j < 0;
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


def _rho(D, n):
    r = np.log(D.c / D.c.shift())
    u = np.log(D.v)
    return r.rolling(n, min_periods=n).corr(u)


def _pv_v(D, p, up):
    n, lvl = p['n'], p['lvl']
    rho = _rho(D, n)
    rp = rho.shift()
    R = np.log(D.c / D.c.shift(n))
    with np.errstate(invalid='ignore'):
        if up:
            hit = (rho > lvl) & (rp <= lvl) & (R > 0)
        else:
            hit = (rho < -lvl) & (rp >= -lvl) & (R < 0)
    return hit.fillna(False).astype(bool) & L27._consec(D, n + 1).astype(bool)


def _pv(d, p, up):
    D = L27._days_v(d)
    return L27._next_day(_pv_v(D, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_pv(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_pv(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


IP = dict(n=30, lvl=0.4)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l85_volume_confirms_rally_4h': dict(
        name='[RESEARCH] L85A 30-day return-volume correlation first above 0.4 with 30-day gain in rising regime long 4h',
        style='trend', fn=sig_up, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's85_volume_confirms_selloff_4h': dict(
        name='[RESEARCH] S85A 30-day return-volume correlation first below -0.4 with 30-day loss in falling regime short 4h',
        style='trend', fn=sig_dn, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
