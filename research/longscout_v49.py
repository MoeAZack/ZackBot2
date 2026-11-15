"""LONGSCOUT-V49: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New families: daily slow stochastic and daily TRIX, never used in earlier rounds.

L49A l49_daily_stoch_up_4h   long  coin rising (R1); daily slow %K(14,3) crosses above 20 (pullback ends). First 4h bar
                                   of the next day. Stop 2 ATR, 2R, 24 bars.
S49A s49_daily_stoch_down_4h short coin falling (R1); daily slow %K(14,3) crosses below 80 (bounce ends). Same management.
R49A r49_daily_trix_4h       both  daily TRIX(15) (1-day % change of a triple EMA15 of close) crosses above 0 -> long if coin
                                   rising; crosses below 0 -> short if falling. First 4h bar of the next day.
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


def _stoch_k(D, p):
    n = p['n']
    lo = D.l.rolling(n, min_periods=n).min(); hi = D.h.rolling(n, min_periods=n).max()
    fast = 100 * (D.c - lo) / (hi - lo).replace(0, np.nan)
    return fast.rolling(p['smooth'], min_periods=p['smooth']).mean()


def _stoch(d, p, up):
    D = L27._days_v(d)
    k = _stoch_k(D, p)
    lvl = p['lo'] if up else p['hi']
    ok = ((k > lvl) & (k.shift() <= lvl)) if up else ((k < lvl) & (k.shift() >= lvl))
    return L27._next_day(ok & L27._consec(D, 2), D, d)


def _trix_v(D, p):
    n = p['n']
    e = S.ema(S.ema(S.ema(D.c, n), n), n)
    return 100 * (e / e.shift() - 1)


def _trix(d, p, up):
    D = L27._days_v(d)
    x = _trix_v(D, p)
    ok = ((x > 0) & (x.shift() <= 0)) if up else ((x < 0) & (x.shift() >= 0))
    return L27._next_day(ok & L27._consec(D, 2), D, d)


def sig_stoch_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_stoch(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_stoch_down(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_stoch(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def sig_trix(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_trix(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_trix(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


ST = dict(n=14, smooth=3, lo=20.0, hi=80.0)
SM = dict(stop_atr=2.0, tp_r=2.0, max_bars=24)
CANDIDATES = {
    'l49_daily_stoch_up_4h': dict(
        name='[RESEARCH] L49A Daily slow stochastic up-cross of 20 in rising regime long 4h', style='momentum',
        fn=sig_stoch_up, sides='long', tf='4h', research_only=True, params=dict(ST), mgmt=dict(SM)),
    's49_daily_stoch_down_4h': dict(
        name='[RESEARCH] S49A Daily slow stochastic down-cross of 80 in falling regime short 4h', style='momentum',
        fn=sig_stoch_down, sides='short', tf='4h', research_only=True, params=dict(ST), mgmt=dict(SM)),
    'r49_daily_trix_4h': dict(
        name='[RESEARCH] R49A Daily TRIX(15) zero cross with regime L/S 4h', style='trend', fn=sig_trix,
        sides='both', tf='4h', research_only=True, params=dict(n=15), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
