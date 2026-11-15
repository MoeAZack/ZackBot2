"""LONGSCOUT-V56: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: single-day failed-extreme reversals on completed daily bars (Wyckoff upthrust /
spring, key reversal day).

S56A s56_daily_upthrust_4h  short the last completed day's high exceeded the highest high of the prior 30 days, but it closed
                                  back below that level and in the lower half of its own range; coin falling or sideways (R1).
                                  First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L56A l56_daily_spring_4h    long  mirror: low under the lowest low of the prior 30 days, close back above it and in the upper
                                  half of its range; coin rising or sideways.
R56A r56_key_reversal_4h    both  key reversal day, no regime filter: a new 20-day high that closes below the prior day's low
                                  -> short; a new 20-day low that closes above the prior day's high -> long. Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _fail_v(D, p, up):
    n = p['n']
    pos = (D.c - D.l) / (D.h - D.l).replace(0, np.nan)
    if up:                                                                # upthrust (bearish)
        lvl = D.h.shift().rolling(n, min_periods=n).max()
        ok = (D.h > lvl) & (D.c < lvl) & (pos < 0.5)
    else:                                                                 # spring (bullish)
        lvl = D.l.shift().rolling(n, min_periods=n).min()
        ok = (D.l < lvl) & (D.c > lvl) & (pos > 0.5)
    return (ok & L27._consec(D, n)).fillna(False)


def _key_v(D, p, top):
    n = p['n']
    if top:
        ok = (D.h > D.h.shift().rolling(n, min_periods=n).max()) & (D.c < D.l.shift())
    else:
        ok = (D.l < D.l.shift().rolling(n, min_periods=n).min()) & (D.c > D.h.shift())
    return (ok & L27._consec(D, n)).fillna(False)


def _on(fn):
    def f(d, p, flag):
        D = L27._days_v(d)
        return L27._next_day(fn(D, p, flag), D, d)
    return f


_fail, _key = _on(_fail_v), _on(_key_v)


def sig_upthrust(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_fail(d, p, True) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


def sig_spring(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_fail(d, p, False) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_key(d, p, ctx):
    return dict(le=S.X(pd.Series(_key(d, p, False), index=d.index)), se=S.X(pd.Series(_key(d, p, True), index=d.index)),
                lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's56_daily_upthrust_4h': dict(
        name='[RESEARCH] S56A Daily 30-day upthrust in falling/sideways regime short 4h', style='reversal',
        fn=sig_upthrust, sides='short', tf='4h', research_only=True, params=dict(n=30), mgmt=dict(MG)),
    'l56_daily_spring_4h': dict(
        name='[RESEARCH] L56A Daily 30-day spring in rising/sideways regime long 4h', style='reversal', fn=sig_spring,
        sides='long', tf='4h', research_only=True, params=dict(n=30), mgmt=dict(MG)),
    'r56_key_reversal_4h': dict(
        name='[RESEARCH] R56A Daily 20-day key reversal day L/S 4h', style='reversal', fn=sig_key,
        sides='both', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
