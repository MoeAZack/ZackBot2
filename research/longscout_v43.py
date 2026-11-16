"""LONGSCOUT-V43: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: single-week return events (complete Mon-Sun UTC weeks), entry on the next Monday.

L43A l43_big_up_week_4h        long  the last completed week rose >= 20% (close of week vs close of the week before), coin
                                     rising (R1): continuation. Monday 00:00 bar. Stop 3 ATR, 2.5R, 42 bars.
S43A s43_big_down_week_4h      short the last completed week fell >= 20%, coin falling (R1): continuation. Same management.
L43B l43_down_week_in_uptrend_4h long the last completed week fell >= 15% while the coin is rising (R1): one-week dip in an
                                     uptrend. Monday 00:00 bar. Stop 3 ATR, 2R, 42 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v26 as L26

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _week_event(d, p, up):
    D = L17._days(d)
    wk = D.index - pd.to_timedelta(D.index.weekday, unit='D')
    g = D.c.groupby(wk)
    W = g.last()[g.size() == 7]
    consec = W.index.to_series().diff() == pd.Timedelta(days=7)
    r = W / W.shift() - 1
    ok = ((r >= p['move']) if up else (r <= -p['move'])) & consec
    flag = pd.Series(0.0, index=D.index)
    flag.loc[(W.index + pd.Timedelta(days=6))[ok.values]] = 1.0
    t = pd.to_datetime(d.t)
    first = ((t.dt.weekday == 0) & (t.dt.hour == 0)).values
    return (L17._on(flag, d) == 1).values & first & L26._day_is_yesterday(D, d)


def sig_up_week(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_week_event(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_down_week(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_week_event(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def sig_dip_week(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_week_event(d, p, False) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


WM = dict(stop_atr=3.0, tp_r=2.5, max_bars=42)
CANDIDATES = {
    'l43_big_up_week_4h': dict(
        name='[RESEARCH] L43A +20% week continuation in rising regime long 4h', style='momentum', fn=sig_up_week,
        sides='long', tf='4h', research_only=True, params=dict(move=0.20), mgmt=dict(WM)),
    's43_big_down_week_4h': dict(
        name='[RESEARCH] S43A -20% week continuation in falling regime short 4h', style='momentum', fn=sig_down_week,
        sides='short', tf='4h', research_only=True, params=dict(move=0.20), mgmt=dict(WM)),
    'l43_down_week_in_uptrend_4h': dict(
        name='[RESEARCH] L43B -15% week dip in rising regime long 4h', style='pullback', fn=sig_dip_week,
        sides='long', tf='4h', research_only=True, params=dict(move=0.15), mgmt=dict(stop_atr=3.0, tp_r=2.0, max_bars=42)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
