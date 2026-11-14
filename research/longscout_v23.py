"""LONGSCOUT-V23: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. These trade around the R1 anchor level
(daily SMA100) inside an existing regime; they are not regime-change entries like r6.

L23A l23_sma100_touch_rising_4h     long  coin rising (R1); a 4h bar's low reaches the last completed day's SMA100 and the bar
                                          closes back above it (first such bar). Stop 2.5 ATR, 2.5R, 36 bars.
S23A s23_sma100_touch_falling_4h    short mirror: coin falling; the bar's high reaches the SMA100 and it closes back below.
L23B l23_daily_rsi_reset_rising_4h  long  coin rising (R1) and the last completed day's RSI14 (daily closes) is below 45; a 4h
                                          close crosses above the last completed day's high. Stop 2.5 ATR, 2.5R, 36 bars.
S23B s23_daily_rsi_reset_falling_4h short mirror: falling, daily RSI14 above 55, 4h close crosses below the last day's low.
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


def _sma100(d):
    dc = L1.daily_closes(d.t, d.c)
    return pd.Series(L1.on_rows(dc.rolling(100).mean(), d.t), index=d.index)


def sig_sma100_touch_rising(d, p, ctx):
    cr = L1.coin_regime(d)
    m = _sma100(d)
    return dict(le=_first((d.l <= m) & (d.c > m) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_sma100_touch_falling(d, p, ctx):
    cr = L1.coin_regime(d)
    m = _sma100(d)
    return dict(le=_z(d), se=_first((d.h >= m) & (d.c < m) & (cr == -1)), lx=_z(d), sx=_z(d))


def _daily_rsi_and_levels(d):
    D = L17._days(d)
    rsi = S.rsi(D.c, 14)
    return L17._on(rsi, d), L17._on(D.h, d), L17._on(D.l, d)


def sig_daily_rsi_reset_rising(d, p, ctx):
    cr = L1.coin_regime(d)
    r, H, _ = _daily_rsi_and_levels(d)
    return dict(le=S.X(S.xup(d.c, H) & (r < p['lo']) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_daily_rsi_reset_falling(d, p, ctx):
    cr = L1.coin_regime(d)
    r, _, Lo = _daily_rsi_and_levels(d)
    return dict(le=_z(d), se=S.X(S.xup(-d.c, -Lo) & (r > p['hi']) & (cr == -1)), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.5, max_bars=36)
CANDIDATES = {
    'l23_sma100_touch_rising_4h': dict(
        name='[RESEARCH] L23A Daily SMA100 touch in rising regime long 4h', style='pullback', fn=sig_sma100_touch_rising,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
    's23_sma100_touch_falling_4h': dict(
        name='[RESEARCH] S23A Daily SMA100 touch in falling regime short 4h', style='pullback', fn=sig_sma100_touch_falling,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
    'l23_daily_rsi_reset_rising_4h': dict(
        name='[RESEARCH] L23B Daily RSI reset then breakout long 4h', style='pullback', fn=sig_daily_rsi_reset_rising,
        sides='long', tf='4h', research_only=True, params=dict(lo=45.0), mgmt=dict(MG)),
    's23_daily_rsi_reset_falling_4h': dict(
        name='[RESEARCH] S23B Daily RSI reset then breakdown short 4h', style='pullback', fn=sig_daily_rsi_reset_falling,
        sides='short', tf='4h', research_only=True, params=dict(hi=55.0), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
