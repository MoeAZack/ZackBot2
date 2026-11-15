"""LONGSCOUT-V44: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families (4h thrust bar, two-day full reversal).

L44A l44_thrust_bar_4h       long  this 4h bar's high-low range is the widest of the prior 180 bars (30 days) and it closes in
                                   the top 20% of its range, coin rising (R1): enter at its close. Stop 2.5 ATR, 2R, 24 bars.
S44A s44_thrust_bar_4h       short mirror: widest bar of 180 closing in its bottom 20%, coin falling (R1).
L44B l44_two_day_reversal_4h long  the day before last fell >= 6% close-to-close and the last completed day closed above that
                                   down day's open (fully reversed), coin rising (R1). First 4h bar of the next day.
                                   Stop 2.5 ATR, 2R, 30 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _thrust(d, p, up):
    rng = d.h - d.l
    wide = rng > rng.shift().rolling(p['n'], min_periods=p['n']).max()
    pos = (d.c - d.l) / rng.replace(0, np.nan)
    return wide & ((pos >= 1 - p['edge']) if up else (pos <= p['edge']))


def sig_thrust_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(_thrust(d, p, True) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_thrust_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(_thrust(d, p, False) & (cr == -1)), lx=_z(d), sx=_z(d))


def _two_day(d, p):
    t = pd.to_datetime(d.t)
    g = pd.DataFrame({'o': d.o.values, 'c': d.c.values, 'day': t.dt.normalize().values}).groupby('day')
    D = pd.DataFrame({'o': g.o.first(), 'c': g.c.last()})[g.size() == 6]
    drop = D.c.shift() / D.c.shift(2) - 1 <= -p['fall']
    ok = drop & (D.c > D.o.shift()) & L27._consec(D, 3)
    return L27._next_day(ok, D, d)


def sig_two_day(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_two_day(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


TB = dict(n=180, edge=0.2)
CANDIDATES = {
    'l44_thrust_bar_4h': dict(
        name='[RESEARCH] L44A Widest 4h bar in 30 days closing strong long 4h', style='momentum', fn=sig_thrust_long,
        sides='long', tf='4h', research_only=True, params=dict(TB), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
    's44_thrust_bar_4h': dict(
        name='[RESEARCH] S44A Widest 4h bar in 30 days closing weak short 4h', style='momentum', fn=sig_thrust_short,
        sides='short', tf='4h', research_only=True, params=dict(TB), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
    'l44_two_day_reversal_4h': dict(
        name='[RESEARCH] L44B Two-day full reversal of a 6% drop long 4h', style='reversal', fn=sig_two_day,
        sides='long', tf='4h', research_only=True, params=dict(fall=0.06), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
