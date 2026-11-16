"""LONGSCOUT-V18: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. Deliberately outside the trend-in-regime
family that produced r6/r17, so a pass here would not just repeat them.

L18A l18_monday_drift_long_4h      long  calendar: at the close of the last Sunday 4h bar (opens 20:00 UTC), buy coins that are
                                         rising (R1); exit after 6 bars (Monday). Stop 2.5 ATR.
S18A s18_monday_drift_short_4h     short mirror: short coins that are falling (R1) at the same time; exit after 6 bars.
S18B s18_high_vol_falling_short_4h short first bar where the coin is falling (R1) and its 42-bar realised volatility is in the top 20% of
                                         its prior 720 bars; exit when volatility falls back below that window's median.
L18B l18_high_vol_rising_long_4h   long  mirror: rising (R1) and volatility in the top 20%; exit when it falls below the median.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def _sunday_last(d):
    t = pd.to_datetime(d.t)
    return pd.Series(((t.dt.weekday == 6) & (t.dt.hour == 20)).values, index=d.index)


def sig_monday_drift_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(_sunday_last(d) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_monday_drift_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(_sunday_last(d) & (cr == -1)), lx=_z(d), sx=_z(d))


def _vol_state(d, p):
    rv = np.log(d.c).diff().rolling(p['vol_n'], min_periods=p['vol_n']).std()
    hi = rv.rolling(p['win'], min_periods=p['win']).quantile(p['q']).shift()
    med = rv.rolling(p['win'], min_periods=p['win']).median().shift()
    return rv, hi, med


def sig_high_vol_falling_short(d, p, ctx):
    cr = L1.coin_regime(d)
    rv, hi, med = _vol_state(d, p)
    return dict(le=_z(d), se=_first((rv > hi) & (cr == -1)), lx=_z(d), sx=S.X(rv < med))


def sig_high_vol_rising_long(d, p, ctx):
    cr = L1.coin_regime(d)
    rv, hi, med = _vol_state(d, p)
    return dict(le=_first((rv > hi) & (cr == 1)), se=_z(d), lx=S.X(rv < med), sx=_z(d))


VOL = dict(vol_n=42, win=720, q=0.8)
CANDIDATES = {
    'l18_monday_drift_long_4h': dict(
        name='[RESEARCH] L18A Monday drift long 4h', style='calendar', fn=sig_monday_drift_long,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=6)),
    's18_monday_drift_short_4h': dict(
        name='[RESEARCH] S18A Monday drift short 4h', style='calendar', fn=sig_monday_drift_short,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=6)),
    's18_high_vol_falling_short_4h': dict(
        name='[RESEARCH] S18B High-volatility falling-regime short 4h', style='volatility', fn=sig_high_vol_falling_short,
        sides='short', tf='4h', research_only=True, params=dict(VOL), mgmt=dict(stop_atr=3.0, max_bars=60)),
    'l18_high_vol_rising_long_4h': dict(
        name='[RESEARCH] L18B High-volatility rising-regime long 4h', style='volatility', fn=sig_high_vol_rising_long,
        sides='long', tf='4h', research_only=True, params=dict(VOL), mgmt=dict(stop_atr=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
