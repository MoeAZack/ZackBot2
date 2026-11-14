"""LONGSCOUT-V19: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. Outside the trend-in-regime family of r6/r17.

L19A l19_hammer_in_uptrend_4h        long  a green 4h bar whose lower wick is >= 2x its body and >= 1 ATR, coin rising (R1);
                                           stop under that bar's low (min 1 ATR), 2R, 24 bars.
S19A s19_shooting_star_downtrend_4h  short mirror: red bar, upper wick >= 2x body and >= 1 ATR, coin falling (R1); stop over high.
L19B l19_red_days_in_uptrend_4h      long  the last three completed UTC days each closed below its open while the coin is rising
                                           (R1); enter on the first 4h bar of the next day. Stop 2.5 ATR, 2R, 24 bars.
S19B s19_green_days_in_downtrend_4h  short mirror: three green days while the coin is falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _wick_sig(d, p, long):
    cr = L1.coin_regime(d)
    body = (d.c - d.o).abs()
    lw = np.minimum(d.o, d.c) - d.l
    uw = d.h - np.maximum(d.o, d.c)
    w = lw if long else uw
    pat = (w >= p['ratio'] * body) & (w >= p['wick_atr'] * d.atr) & ((d.c > d.o) if long else (d.c < d.o))
    sig = S.X(pat & (cr == (1 if long else -1)))
    raw = (d.c - (d.l - p['buf_atr'] * d.atr)) if long else ((d.h + p['buf_atr'] * d.atr) - d.c)
    dist = np.where(sig, np.maximum(raw.values, p['min_atr'] * d.atr.values), np.nan)
    return sig, dist


def sig_hammer_in_uptrend(d, p, ctx):
    s, dist = _wick_sig(d, p, True)
    return dict(le=s, se=_z(d), lx=_z(d), sx=_z(d), atr=dist)


def sig_shooting_star_downtrend(d, p, ctx):
    s, dist = _wick_sig(d, p, False)
    return dict(le=_z(d), se=s, lx=_z(d), sx=_z(d), atr=dist)


def _counter_days(d, p, red):
    D = L17._days(d)
    col = ((D.c < D.o) if red else (D.c > D.o)) & D.prev_ok
    run = col.astype(float).rolling(p['days'], min_periods=p['days']).sum() == p['days']
    on = L17._on(run, d) == 1
    first_bar = (pd.to_datetime(d.t).dt.hour == 0).values
    return on & first_bar


def sig_red_days_in_uptrend(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(_counter_days(d, p, True) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_green_days_in_downtrend(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(_counter_days(d, p, False) & (cr == -1)), lx=_z(d), sx=_z(d))


WICK = dict(ratio=2.0, wick_atr=1.0, buf_atr=0.1, min_atr=1.0)
CANDIDATES = {
    'l19_hammer_in_uptrend_4h': dict(
        name='[RESEARCH] L19A Hammer in rising regime long 4h', style='reversal', fn=sig_hammer_in_uptrend,
        sides='long', tf='4h', research_only=True, params=dict(WICK), mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24)),
    's19_shooting_star_downtrend_4h': dict(
        name='[RESEARCH] S19A Shooting star in falling regime short 4h', style='reversal', fn=sig_shooting_star_downtrend,
        sides='short', tf='4h', research_only=True, params=dict(WICK), mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24)),
    'l19_red_days_in_uptrend_4h': dict(
        name='[RESEARCH] L19B Three red days in rising regime long 4h', style='pullback', fn=sig_red_days_in_uptrend,
        sides='long', tf='4h', research_only=True, params=dict(days=3), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
    's19_green_days_in_downtrend_4h': dict(
        name='[RESEARCH] S19B Three green days in falling regime short 4h', style='pullback', fn=sig_green_days_in_downtrend,
        sides='short', tf='4h', research_only=True, params=dict(days=3), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
