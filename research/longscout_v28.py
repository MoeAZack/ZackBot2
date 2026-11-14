"""LONGSCOUT-V28: three new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. Part of the bounded batch ending at V30.

R28A r28_band_walk_4h          both  the last 2 completed UTC days both closed above the daily Bollinger upper band (20, 2 sd),
                                     coin rising (R1) -> long on the first 4h bar of the next day; mirror (2 closes below the
                                     lower band, coin falling) -> short. First such day. Stop 2.5 ATR, 2R, 30 bars.
L28A l28_nr7_breakout_4h       long  the last completed day had the narrowest high-low range of the last 7 days (NR7), coin
                                     rising (R1); a 4h close crosses above that day's high during the next day only.
                                     Stop 2.5 ATR, 2.5R, 36 bars.
R28B r28_obv_confirmed_break_4h both daily OBV makes a 60-day high and the daily close makes a 20-day closing high on the same
                                     completed day, coin not falling (R1) -> long next day first bar; mirror (OBV 60-day low and
                                     20-day closing low, coin not rising) -> short. Stop 2.5 ATR, 2.5R, 36 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v26 as L26
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _band_walk(d, p, up):
    D = L27._days_v(d)
    n = p['n']
    ma = D.c.rolling(n, min_periods=n).mean(); sd = D.c.rolling(n, min_periods=n).std()
    out = (D.c > ma + p['k'] * sd) if up else (D.c < ma - p['k'] * sd)
    k = p['days']
    hit = (out.astype(float).rolling(k, min_periods=k).sum() == k) & L27._consec(D, n)
    ok = hit & ~hit.shift(fill_value=False)
    return L27._next_day(ok, D, d)


def sig_band_walk(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_band_walk(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_band_walk(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _nr7(d, p):
    D = L27._days_v(d)
    rng = D.h - D.l
    k = p['n']
    nr = (rng <= rng.rolling(k, min_periods=k).min()) & L27._consec(D, k)
    flag = L17._on(nr, d) == 1
    hi = L17._on(D.h, d)
    fresh = L26._day_is_yesterday(D, d)                          # only during the day right after the NR7 day
    return flag.values & fresh, hi


def sig_nr7_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    ok, hi = _nr7(d, p)
    le = S.xup(d.c, hi) & pd.Series(ok, index=d.index) & (cr == 1)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def _obv_break(d, p, up):
    D = L27._days_v(d)
    obv = (np.sign(D.c.diff()).fillna(0) * D.v).cumsum()
    if up:
        a = obv >= obv.rolling(p['obv_n'], min_periods=p['obv_n']).max()
        b = D.c >= D.c.rolling(p['px_n'], min_periods=p['px_n']).max()
    else:
        a = obv <= obv.rolling(p['obv_n'], min_periods=p['obv_n']).min()
        b = D.c <= D.c.rolling(p['px_n'], min_periods=p['px_n']).min()
    ok = a & b & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_obv_break(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_obv_break(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=S.X(pd.Series(_obv_break(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r28_band_walk_4h': dict(
        name='[RESEARCH] R28A Daily Bollinger band walk with regime L/S 4h', style='momentum', fn=sig_band_walk,
        sides='both', tf='4h', research_only=True, params=dict(n=20, k=2.0, days=2), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'l28_nr7_breakout_4h': dict(
        name='[RESEARCH] L28A Daily NR7 breakout in rising regime long 4h', style='breakout', fn=sig_nr7_breakout,
        sides='long', tf='4h', research_only=True, params=dict(n=7), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'r28_obv_confirmed_break_4h': dict(
        name='[RESEARCH] R28B OBV-confirmed 20-day close break L/S 4h', style='trend', fn=sig_obv_break,
        sides='both', tf='4h', research_only=True, params=dict(obv_n=60, px_n=20), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
