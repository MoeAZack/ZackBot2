"""LONGSCOUT-V50: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: ATR-scaled pullback depth on daily bars, and the Elder impulse colour change.

L50A l50_atr_pullback_4h   long  coin rising (R1); the last completed day closed at least 3 daily ATR14 below the highest
                                 close of the prior 10 days, the day before did not (first touch). First 4h bar of the
                                 next day. Stop 2.5 ATR, 2R, 30 bars.
S50A s50_atr_rebound_4h    short mirror: coin falling; close at least 3 daily ATR14 above the lowest close of the prior
                                 10 days, first touch. Same management.
L50B l50_elder_impulse_4h  long  coin rising (R1); the last completed day is the first "green" Elder-impulse day (daily EMA13
                                 up and MACD(12,26,9) histogram up) after a day that was not green. First 4h bar of the
                                 next day. Stop 2 ATR, 2R, 24 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _datr(D, n):
    tr = pd.concat([D.h - D.l, (D.h - D.c.shift()).abs(), (D.l - D.c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _pb_flag(D, p, up):
    n, k = p['look'], p['k']
    a = _datr(D, p['atr'])
    if up:
        x = D.c <= D.c.shift().rolling(n, min_periods=n).max() - k * a
    else:
        x = D.c >= D.c.shift().rolling(n, min_periods=n).min() + k * a
    return x.fillna(False)


def _pb(d, p, up):
    D = L27._days_v(d)
    x = _pb_flag(D, p, up)
    ok = x & ~x.shift(fill_value=False).astype(bool) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def _green(D):
    e = S.ema(D.c, 13)
    m = S.ema(D.c, 12) - S.ema(D.c, 26)
    hist = m - S.ema(m, 9)
    return ((e > e.shift()) & (hist > hist.shift())).fillna(False)


def _impulse(d, p):
    D = L27._days_v(d)
    g = _green(D)
    ok = g & ~g.shift(fill_value=False).astype(bool) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_pb_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_pb(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_pb_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_pb(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def sig_impulse(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_impulse(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


PB = dict(look=10, k=3.0, atr=14)
PM = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l50_atr_pullback_4h': dict(
        name='[RESEARCH] L50A Three-ATR daily pullback in rising regime long 4h', style='reversal', fn=sig_pb_long,
        sides='long', tf='4h', research_only=True, params=dict(PB), mgmt=dict(PM)),
    's50_atr_rebound_4h': dict(
        name='[RESEARCH] S50A Three-ATR daily rebound in falling regime short 4h', style='reversal', fn=sig_pb_short,
        sides='short', tf='4h', research_only=True, params=dict(PB), mgmt=dict(PM)),
    'l50_elder_impulse_4h': dict(
        name='[RESEARCH] L50B First green daily Elder impulse in rising regime long 4h', style='momentum', fn=sig_impulse,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
