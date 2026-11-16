"""LONGSCOUT-V45: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New family: range trading only while the coin's R1 regime is SIDEWAYS (0); every
earlier LONGSCOUT rule traded the rising/falling states or ignored the regime.

R45A r45_sideways_bollinger_4h both  coin sideways (R1 = 0); the last completed day closed below its daily Bollinger lower band
                                     (20, 2 sd) -> long; above the upper band -> short. First 4h bar of the next day.
                                     Stop 2 ATR, target 1.5R, 18 bars.
R45B r45_sideways_donchian_fade_4h both coin sideways; the last completed day closed at a new 20-day closing high -> short; at a
                                     new 20-day closing low -> long. First 4h bar of the next day. Stop 2 ATR, 1.5R, 18 bars.
R45C r45_sideways_rsi_4h       both  coin sideways; daily RSI14 crosses below 30 -> long; crosses above 70 -> short. First 4h bar
                                     of the next day. Stop 2 ATR, 1.5R, 18 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _bb(D, p, low):
    n = p['n']
    ma = D.c.rolling(n, min_periods=n).mean(); sd = D.c.rolling(n, min_periods=n).std()
    return (D.c < ma - p['k'] * sd) if low else (D.c > ma + p['k'] * sd)


def _don(D, p, low):
    n = p['n']
    return (D.c < D.c.shift().rolling(n, min_periods=n).min()) if low else (D.c > D.c.shift().rolling(n, min_periods=n).max())


def _rsi(D, p, low):
    r = S.rsi(D.c, p['n'])
    return ((r < p['lo']) & (r.shift() >= p['lo'])) if low else ((r > p['hi']) & (r.shift() <= p['hi']))


def _side(d, p, fn, low):
    D = L27._days_v(d)
    ok = fn(D, p, low) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def _mk_sig(fn):
    def sig(d, p, ctx):
        cr = L1.coin_regime(d)
        flat = cr == 0
        return dict(le=S.X(pd.Series(_side(d, p, fn, True) & flat, index=d.index)),
                    se=S.X(pd.Series(_side(d, p, fn, False) & flat, index=d.index)), lx=_z(d), sx=_z(d))
    return sig


sig_bb, sig_don, sig_rsi = _mk_sig(_bb), _mk_sig(_don), _mk_sig(_rsi)
MR = dict(stop_atr=2.0, tp_r=1.5, max_bars=18)
CANDIDATES = {
    'r45_sideways_bollinger_4h': dict(
        name='[RESEARCH] R45A Daily Bollinger fade in sideways regime L/S 4h', style='reversal', fn=sig_bb,
        sides='both', tf='4h', research_only=True, params=dict(n=20, k=2.0), mgmt=dict(MR)),
    'r45_sideways_donchian_fade_4h': dict(
        name='[RESEARCH] R45B 20-day closing extreme fade in sideways regime L/S 4h', style='reversal', fn=sig_don,
        sides='both', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MR)),
    'r45_sideways_rsi_4h': dict(
        name='[RESEARCH] R45C Daily RSI14 30/70 in sideways regime L/S 4h', style='reversal', fn=sig_rsi,
        sides='both', tf='4h', research_only=True, params=dict(n=14, lo=30.0, hi=70.0), mgmt=dict(MR)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
