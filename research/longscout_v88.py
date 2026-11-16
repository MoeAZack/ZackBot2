"""LONGSCOUT-V88: two new research-only 4h candidates (Claude Projects, 2026-11-16).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: IDIOSYNCRATIC VOLATILITY (residual volatility after removing the coin's BTC beta). V33 used
the coin-BTC correlation and beta themselves; no earlier round used the residual volatility (the low-IVOL anomaly).

rc_t, rb_t = daily simple returns of the coin and of BTC (only across a one-day step). Over days j-59..j (sample moments):
IV_j = sqrt(max(0, var(rc) - cov(rc, rb)^2 / var(rb))) (the standard deviation of the OLS residual of rc on rb).
R_j = log(c_j / c_{j-60}).
L88A l88_low_idio_vol_uptrend_4h   long  days j-181..j present; IV_j below every IV of days j-120..j-1 (first day of a new 120-day
                                         low) and R_j > 0. Coin rising (R1). First 4h bar of the next day (modeled fill
                                         04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S88A s88_high_idio_vol_downtrend_4h short IV_j above every IV of days j-120..j-1 (first day of a new 120-day high) and R_j < 0;
                                         coin falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v33 as L33

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _iv(J, m):
    vc = J.rc.rolling(m, min_periods=m).var()
    vb = J.rb.rolling(m, min_periods=m).var()
    cv = J.rc.rolling(m, min_periods=m).cov(J.rb)
    return np.sqrt((vc - cv ** 2 / vb).clip(lower=0))


def _idio_v(D, J, p, up):
    m, n = p['m'], p['n']
    IV = _iv(J, m)
    if up:
        ext = IV < IV.shift().rolling(n, min_periods=n).min()
    else:
        ext = IV > IV.shift().rolling(n, min_periods=n).max()
    ext = ext.fillna(False).astype(bool)
    first = ext & ~ext.shift(fill_value=False)
    R = np.log(D.c / D.c.shift(m))
    tr = (R > 0) if up else (R < 0)
    return first & tr.fillna(False).astype(bool) & L27._consec(D, m + n + 1).astype(bool)


def _idio(d, p, up):
    D, J = L33._joint(d)
    return L27._next_day(_idio_v(D, J, p, up), D, d)


def sig_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_idio(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_dn(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_idio(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


IP = dict(m=60, n=120)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l88_low_idio_vol_uptrend_4h': dict(
        name='[RESEARCH] L88A 60-day BTC-residual volatility at a 120-day low with 60-day gain in rising regime long 4h',
        style='trend', fn=sig_up, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    's88_high_idio_vol_downtrend_4h': dict(
        name='[RESEARCH] S88A 60-day BTC-residual volatility at a 120-day high with 60-day loss in falling regime short 4h',
        style='trend', fn=sig_dn, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
