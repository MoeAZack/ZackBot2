"""LONGSCOUT-V15: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None is r6_regime_flip_4h with a filter added.

L15A l15_pivot_r1_breakout_4h      long  a 4h close crosses above the previous completed UTC day's classic pivot R1
                                         (2 * (H + L + C) / 3 - L) while the coin is rising (R1). Stop 2.5 ATR, 2.5R, 30 bars.
S15A s15_pivot_s1_breakdown_4h     short a 4h close crosses below the previous day's pivot S1 (2 * P - H) while falling (R1).
L15B l15_joint_btc_breakout_4h     long  the coin and BTC both close above their own prior 20-bar highs on the same 4h bar
                                         (first such bar), coin rising (R1). Market-confirmed breakout. Stop 2.5 ATR, 3R, 60 bars.
S15B s15_joint_btc_breakdown_4h    short mirror: both close below their prior 20-bar lows on the same bar, coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def _pivots(d):
    """Previous completed day's pivot R1/S1 (days with all six 4h bars), on bars from day + 1 00:00 (on_rows)."""
    t = pd.to_datetime(d.t)
    g = pd.DataFrame({'h': d.h.values, 'l': d.l.values, 'c': d.c.values, 'day': t.dt.normalize().values}).groupby('day')
    full = g.size() == 6
    H, Lo, C = g.h.max()[full], g.l.min()[full], g.c.last()[full]
    P = (H + Lo + C) / 3
    return (pd.Series(L1.on_rows(2 * P - Lo, d.t), index=d.index), pd.Series(L1.on_rows(2 * P - H, d.t), index=d.index))


def sig_pivot_r1_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    r1, _ = _pivots(d)
    return dict(le=S.X(S.xup(d.c, r1) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_pivot_s1_breakdown(d, p, ctx):
    cr = L1.coin_regime(d)
    _, s1 = _pivots(d)
    return dict(le=_z(d), se=S.X(S.xup(-d.c, -s1) & (cr == -1)), lx=_z(d), sx=_z(d))


def _btc_on(d):
    f = L1._BTC['f']
    b = f.set_index(pd.to_datetime(f.t))
    hh = b.h.rolling(20).max().shift(); ll = b.l.rolling(20).min().shift()
    idx = pd.to_datetime(d.t)
    return (pd.Series(b.c.reindex(idx).values, index=d.index), pd.Series(hh.reindex(idx).values, index=d.index),
            pd.Series(ll.reindex(idx).values, index=d.index))


def sig_joint_btc_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    bc, bh, _ = _btc_on(d)
    return dict(le=_first((d.c > d.hh20) & (bc > bh) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_joint_btc_breakdown(d, p, ctx):
    cr = L1.coin_regime(d)
    bc, _, bl = _btc_on(d)
    return dict(le=_z(d), se=_first((d.c < d.ll20) & (bc < bl) & (cr == -1)), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l15_pivot_r1_breakout_4h': dict(
        name='[RESEARCH] L15A Daily pivot R1 breakout long 4h', style='trend', fn=sig_pivot_r1_breakout,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=30)),
    's15_pivot_s1_breakdown_4h': dict(
        name='[RESEARCH] S15A Daily pivot S1 breakdown short 4h', style='trend', fn=sig_pivot_s1_breakdown,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=30)),
    'l15_joint_btc_breakout_4h': dict(
        name='[RESEARCH] L15B Coin+BTC joint 20-bar breakout long 4h', style='trend', fn=sig_joint_btc_breakout,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    's15_joint_btc_breakdown_4h': dict(
        name='[RESEARCH] S15B Coin+BTC joint 20-bar breakdown short 4h', style='trend', fn=sig_joint_btc_breakdown,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
