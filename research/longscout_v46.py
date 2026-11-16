"""LONGSCOUT-V46: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: counter-regime trades at extremes (long inside a FALLING regime, short inside a RISING
regime), which earlier rounds never took.

L46A l46_falling_oversold_bounce_4h long  coin falling (R1); daily RSI14 crosses below 20 (deeply oversold). First 4h bar of the
                                          next day. Stop 2.5 ATR, 1.5R, 18 bars.
S46A s46_rising_overbought_fade_4h  short coin rising (R1); daily RSI14 crosses above 85. Same management.
L46B l46_falling_crash_bounce_4h    long  coin falling (R1); the last 3 completed days fell >= 25% close-to-close. First 4h bar of
                                          the next day. Stop 3 ATR, 1.5R, 18 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _rsi_x(d, p, below):
    D = L27._days_v(d)
    r = S.rsi(D.c, 14)
    lvl = p['lvl']
    ok = ((r < lvl) & (r.shift() >= lvl)) if below else ((r > lvl) & (r.shift() <= lvl))
    return L27._next_day(ok & L27._consec(D, 2), D, d)


def sig_oversold(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_rsi_x(d, p, True) & (cr == -1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_overbought(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_rsi_x(d, p, False) & (cr == 1), index=d.index)), lx=_z(d), sx=_z(d))


def _crash(d, p):
    D = L27._days_v(d)
    k = p['days']
    ok = (D.c / D.c.shift(k) - 1 <= -p['fall']) & L27._consec(D, k + 1)
    return L27._next_day(ok, D, d)


def sig_crash(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_crash(d, p) & (cr == -1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CT = dict(stop_atr=2.5, tp_r=1.5, max_bars=18)
CANDIDATES = {
    'l46_falling_oversold_bounce_4h': dict(
        name='[RESEARCH] L46A Daily RSI<20 bounce inside falling regime long 4h', style='reversal', fn=sig_oversold,
        sides='long', tf='4h', research_only=True, params=dict(lvl=20.0), mgmt=dict(CT)),
    's46_rising_overbought_fade_4h': dict(
        name='[RESEARCH] S46A Daily RSI>85 fade inside rising regime short 4h', style='reversal', fn=sig_overbought,
        sides='short', tf='4h', research_only=True, params=dict(lvl=85.0), mgmt=dict(CT)),
    'l46_falling_crash_bounce_4h': dict(
        name='[RESEARCH] L46B Three-day 25% crash bounce inside falling regime long 4h', style='reversal', fn=sig_crash,
        sides='long', tf='4h', research_only=True, params=dict(days=3, fall=0.25), mgmt=dict(stop_atr=3.0, tp_r=1.5, max_bars=18)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
