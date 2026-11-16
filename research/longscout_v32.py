"""LONGSCOUT-V32: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New families (volume-weighted price, trend efficiency, up/down volume balance).

R32A r32_vwap30_cross_4h        both  a 4h close crosses above the rolling 180-bar (30-day) VWAP while the coin is rising (R1)
                                      -> long; crosses below while falling -> short. Exit when the close crosses back through
                                      the VWAP. Stop 2.5 ATR, 120 bars.
R32B r32_efficiency_ratio_4h    both  Kaufman efficiency ratio of the last 20 completed daily closes rises through 0.5 (a clean,
                                      one-way move) and the 20-day move is up with the coin rising (R1) -> long; down with
                                      the coin falling -> short. First 4h bar of the next day. Stop 2.5 ATR, 2.5R, 36 bars.
L32A l32_updown_volume_4h       long  over the last 20 completed days, volume on up-close days is >= 2x volume on down-close days
                                      (first such day), coin not falling (R1); first 4h bar of the next day.
                                      Stop 2.5 ATR, 2.5R, 36 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _vwap(d, p):
    n = p['n']
    tp = (d.h + d.l + d.c) / 3
    return (tp * d.v).rolling(n, min_periods=n).sum() / d.v.rolling(n, min_periods=n).sum()


def sig_vwap_cross(d, p, ctx):
    cr = L1.coin_regime(d)
    w = _vwap(d, p)
    return dict(le=S.X(S.xup(d.c, w) & (cr == 1)), se=S.X(S.xup(-d.c, -w) & (cr == -1)),
                lx=S.X(S.xup(-d.c, -w)), sx=S.X(S.xup(d.c, w)))


def _er(d, p, up):
    D = L27._days_v(d)
    n = p['n']
    move = D.c - D.c.shift(n)
    path = D.c.diff().abs().rolling(n, min_periods=n).sum()
    er = move.abs() / path.replace(0, np.nan)
    hit = er >= p['lvl']
    ok = hit & ~hit.shift(fill_value=False) & ((move > 0) if up else (move < 0)) & L27._consec(D, n + 1)
    return L27._next_day(ok, D, d)


def sig_er(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_er(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_er(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _updown(d, p):
    D = L27._days_v(d)
    n = p['n']
    ch = D.c.diff()
    upv = (D.v * (ch > 0)).rolling(n, min_periods=n).sum()
    dnv = (D.v * (ch < 0)).rolling(n, min_periods=n).sum()
    hit = upv >= p['ratio'] * dnv
    ok = hit & ~hit.shift(fill_value=False) & dnv.notna() & L27._consec(D, n + 1)
    return L27._next_day(ok, D, d)


def sig_updown(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_updown(d, p) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r32_vwap30_cross_4h': dict(
        name='[RESEARCH] R32A 30-day rolling VWAP cross with regime L/S 4h', style='trend', fn=sig_vwap_cross,
        sides='both', tf='4h', research_only=True, params=dict(n=180), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'r32_efficiency_ratio_4h': dict(
        name='[RESEARCH] R32B Daily efficiency-ratio trend start L/S 4h', style='trend', fn=sig_er,
        sides='both', tf='4h', research_only=True, params=dict(n=20, lvl=0.5), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'l32_updown_volume_4h': dict(
        name='[RESEARCH] L32A 20-day up/down volume balance long 4h', style='momentum', fn=sig_updown,
        sides='long', tf='4h', research_only=True, params=dict(n=20, ratio=2.0), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
