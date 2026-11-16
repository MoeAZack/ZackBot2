"""LONGSCOUT-V51: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: market-wide (BTC) volatility squeeze as a timing trigger, and daily momentum
acceleration (second difference of the 10-day return).

R51A r51_btc_quiet_squeeze_4h  both  BTC's 7-day standard deviation of daily returns (consecutive days only) is below its
                                     lowest value of the prior 60 days, and was not the day before (first day of the
                                     squeeze); go with the coin's R1 regime (long rising, short falling). First 4h bar of the
                                     next day. Stop 2.5 ATR, 2R, 30 bars.
L51B l51_momentum_accel_4h     long  coin rising (R1); the 10-day return is positive and its change over the last 10 days
                                     crosses above 0 (momentum re-accelerating). First 4h bar of the next day.
                                     Stop 2.5 ATR, 2R, 30 bars.
S51B s51_momentum_accel_4h     short mirror: coin falling; 10-day return negative and its change crosses below 0.
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


def _squeeze_flag(J, p):
    sd = J.rb.rolling(p['w'], min_periods=p['w']).std()
    return (sd < sd.shift().rolling(p['look'], min_periods=p['look']).min()).fillna(False)


def _squeeze(d, p):
    D, J = L33._joint(d)
    f = _squeeze_flag(J, p)
    ok = f & ~f.shift(fill_value=False).astype(bool) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def _accel_v(D, p):
    n = p['n']
    roc = D.c / D.c.shift(n) - 1
    return roc, roc - roc.shift(n)


def _accel(d, p, up):
    D = L27._days_v(d)
    roc, acc = _accel_v(D, p)
    if up:
        ok = (roc > 0) & (acc > 0) & (acc.shift() <= 0)
    else:
        ok = (roc < 0) & (acc < 0) & (acc.shift() >= 0)
    return L27._next_day(ok & L27._consec(D, 2 * p['n'] + 1), D, d)


def sig_squeeze(d, p, ctx):
    cr = L1.coin_regime(d)
    q = _squeeze(d, p)
    return dict(le=S.X(pd.Series(q & (cr == 1), index=d.index)), se=S.X(pd.Series(q & (cr == -1), index=d.index)),
                lx=_z(d), sx=_z(d))


def sig_accel_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_accel(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_accel_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_accel(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


AM = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r51_btc_quiet_squeeze_4h': dict(
        name='[RESEARCH] R51A BTC 7-day volatility squeeze with coin regime L/S 4h', style='trend', fn=sig_squeeze,
        sides='both', tf='4h', research_only=True, params=dict(w=7, look=60), mgmt=dict(AM)),
    'l51_momentum_accel_4h': dict(
        name='[RESEARCH] L51B Daily 10-day momentum re-acceleration in rising regime long 4h', style='momentum',
        fn=sig_accel_long, sides='long', tf='4h', research_only=True, params=dict(n=10), mgmt=dict(AM)),
    's51_momentum_accel_4h': dict(
        name='[RESEARCH] S51B Daily 10-day momentum re-acceleration in falling regime short 4h', style='momentum',
        fn=sig_accel_short, sides='short', tf='4h', research_only=True, params=dict(n=10), mgmt=dict(AM)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
