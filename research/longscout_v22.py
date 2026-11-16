"""LONGSCOUT-V22: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None re-parameterises
r6/s16/r17.

L22A l22_vix_fix_spike_rising_4h     long  Williams VIX Fix (22-bar highest close vs low) closes above its 20-bar mean + 2 sd
                                           (a fear spike) while the coin is rising (R1); first such bar. Stop 2.5 ATR, 2R, 24 bars.
R22A r22_cci_regime_4h               both  CCI(20) crosses above +100 while the coin is rising (R1) -> long; below -100 while
                                           falling -> short; exit when CCI crosses back through zero.
L22B l22_range_expansion_day_4h      long  the last completed UTC day's range was >= 2x the mean of the prior 20 days' ranges and
                                           it closed in the top 20% of its range; enter on the first 4h bar of the next day,
                                           coin not falling (R1). Stop 2.5 ATR, 2R, 24 bars.
S22A s22_range_expansion_day_4h      short mirror: expansion day closing in its bottom 20%, coin not rising (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def sig_vix_fix_spike_rising(d, p, ctx):
    cr = L1.coin_regime(d)
    wvf = 100 * (d.c.rolling(p['n'], min_periods=p['n']).max() - d.l) / d.c.rolling(p['n'], min_periods=p['n']).max()
    band = wvf.rolling(p['bb'], min_periods=p['bb']).mean() + p['k'] * wvf.rolling(p['bb'], min_periods=p['bb']).std()
    return dict(le=_first((wvf > band) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_cci_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    tp = (d.h + d.l + d.c) / 3
    n = p['n']
    ma = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda a: np.abs(a - a.mean()).mean(), raw=True)
    cci = (tp - ma) / (0.015 * md)
    lim = cci * 0 + p['lim']
    return dict(le=S.X(S.xup(cci, lim) & (cr == 1)), se=S.X(S.xup(-cci, lim) & (cr == -1)),
                lx=S.X(S.xup(-cci, lim * 0)), sx=S.X(S.xup(cci, lim * 0)))


def _expansion(d, p, bull):
    D = L17._days(d)
    rng = D.h - D.l
    big = (rng >= p['mult'] * rng.rolling(p['days'], min_periods=p['days']).mean().shift()) & D.prev_ok
    pos = (D.c - D.l) / rng.replace(0, np.nan)
    ok = big & ((pos >= 1 - p['edge']) if bull else (pos <= p['edge']))
    first_bar = (pd.to_datetime(d.t).dt.hour == 0).values
    return (L17._on(ok, d) == 1) & first_bar


def sig_range_expansion_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(_expansion(d, p, True) & np.isin(cr, (0.0, 1.0))), se=_z(d), lx=_z(d), sx=_z(d))


def sig_range_expansion_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(_expansion(d, p, False) & np.isin(cr, (0.0, -1.0))), lx=_z(d), sx=_z(d))


EXP = dict(mult=2.0, days=20, edge=0.2)
CANDIDATES = {
    'l22_vix_fix_spike_rising_4h': dict(
        name='[RESEARCH] L22A Williams VIX Fix spike in rising regime long 4h', style='reversal', fn=sig_vix_fix_spike_rising,
        sides='long', tf='4h', research_only=True, params=dict(n=22, bb=20, k=2.0), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
    'r22_cci_regime_4h': dict(
        name='[RESEARCH] R22A Regime CCI(20) +/-100 L/S 4h', style='momentum', fn=sig_cci_regime,
        sides='both', tf='4h', research_only=True, params=dict(n=20, lim=100.0), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l22_range_expansion_day_4h': dict(
        name='[RESEARCH] L22B Bullish range-expansion day long 4h', style='momentum', fn=sig_range_expansion_long,
        sides='long', tf='4h', research_only=True, params=dict(EXP), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
    's22_range_expansion_day_4h': dict(
        name='[RESEARCH] S22A Bearish range-expansion day short 4h', style='momentum', fn=sig_range_expansion_short,
        sides='short', tf='4h', research_only=True, params=dict(EXP), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
