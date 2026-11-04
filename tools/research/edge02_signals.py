"""EDGE-02 Stage-A signal definitions (research-only; never imported by the runtime engine).

The primary thresholds are the middle values frozen in the EDGE-02 contract.  The
neighbour values are metadata for later diagnostic cells; this module deliberately
does not select among them and exposes no optimisation/search surface.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


RT_COST = 2 * (0.0005 + 0.0002)  # taker fee + bar-model slippage, both fills
COST_X = 4.0
RSI2_PRIMARY = 10.0
RSI2_NEIGHBOURS = (5.0, 15.0)
SQUEEZE_PRIMARY = 0.20
SQUEEZE_NEIGHBOURS = (0.15, 0.25)

STRUCTURE = dict(cost_x=COST_X, volume_x=1.25, volume_n=20, stop_atr=1.0)
RSI2 = dict(level=RSI2_PRIMARY, cost_x=COST_X, stop_atr=1.25)
SQUEEZE = dict(percentile=SQUEEZE_PRIMARY, donchian_n=20, volume_n=20, volume_x=1.25,
               cost_x=COST_X, stop_atr=1.5)


def _x(v):
    return v.fillna(False).astype(bool)


def _one_side(s):
    le, se = _x(s['le']), _x(s['se'])
    return dict(s, le=le & ~se, se=se & ~le)


def _cost_ok(d, stop_atr, cost_x=COST_X):
    """Frozen Stage-A bar-cost floor; all inputs belong to the closed decision bar."""
    return _x(float(stop_atr) * d.atr >= float(cost_x) * RT_COST * d.c)


def _volume_ok(d, n, multiple):
    """Decision-bar volume versus an average ending one bar earlier (no self-dilution/lookahead)."""
    prior = d.v.rolling(int(n)).mean().shift()
    return _x((d.v > 0) & (prior > 0) & (d.v >= float(multiple) * prior))


def _structure(d):
    """The original causal three-candle confirmed structure rule and its exits."""
    ph = d.h.shift(1).where((d.h.shift(1) > d.h.shift(2)) & (d.h.shift(1) >= d.h))
    pl = d.l.shift(1).where((d.l.shift(1) < d.l.shift(2)) & (d.l.shift(1) <= d.l))
    hi, lo = ph.ffill(), pl.ffill()
    up, dn = d.e50 > d.e200, d.e50 < d.e200
    le = up & (d.c > hi) & (d.c.shift() <= hi.shift())
    se = dn & (d.c < lo) & (d.c.shift() >= lo.shift())
    return dict(le=le, se=se, lx=(d.c < lo) | dn, sx=(d.c > hi) | up)


def sig_structure_cf(d, p, ctx):
    """Confirmed structure plus frozen cost/ATR and prior-average volume admission gates."""
    s = _structure(d)
    gate = _cost_ok(d, STRUCTURE['stop_atr'], STRUCTURE['cost_x']) & _volume_ok(
        d, STRUCTURE['volume_n'], STRUCTURE['volume_x'])
    return _one_side(dict(s, le=s['le'] & gate, se=s['se'] & gate))


def sig_structure_cf_cost_ablation(d, p, ctx):
    """Comparator: structure_cf.v1 with only the cost floor removed; every other byte-level rule is retained."""
    s = _structure(d)
    gate = _volume_ok(d, STRUCTURE['volume_n'], STRUCTURE['volume_x'])
    return _one_side(dict(s, le=s['le'] & gate, se=s['se'] & gate))


def sig_rsi2_reclaim(d, p, ctx):
    """RSI2 exhaustion on the prior bar, then a close through that bar's extreme in the local trend."""
    level = RSI2['level']
    up, dn = d.e50 > d.e200, d.e50 < d.e200
    gate = _cost_ok(d, RSI2['stop_atr'], RSI2['cost_x'])
    le = (up & (d.c > d.e20) & (d.rsi2.shift() <= level) & (d.rsi2 > level)
          & (d.c > d.h.shift()) & gate)
    se = (dn & (d.c < d.e20) & (d.rsi2.shift() >= 100.0 - level)
          & (d.rsi2 < 100.0 - level) & (d.c < d.l.shift()) & gate)
    return _one_side(dict(le=le, se=se, lx=dn | (d.c < d.e20), sx=up | (d.c > d.e20)))


def sig_squeeze_release(d, p, ctx):
    """Prior-bar compression followed by a volume-confirmed Donchian release in the local trend."""
    n = SQUEEZE['donchian_n']
    hi, lo = d.h.rolling(n).max().shift(), d.l.rolling(n).min().shift()
    compressed = d.bbw_pct.shift() <= SQUEEZE['percentile']
    up, dn = d.e50 > d.e200, d.e50 < d.e200
    gate = (_volume_ok(d, SQUEEZE['volume_n'], SQUEEZE['volume_x'])
            & _cost_ok(d, SQUEEZE['stop_atr'], SQUEEZE['cost_x']))
    le = compressed & up & (d.c > hi) & (d.c.shift() <= hi.shift()) & gate
    se = compressed & dn & (d.c < lo) & (d.c.shift() >= lo.shift()) & gate
    return _one_side(dict(le=le, se=se, lx=dn | (d.c < d.e20), sx=up | (d.c > d.e20)))


_WHEN = {'long': 'bull', 'short': 'bear'}
_COMMON = dict(sides='both', tf='15m', research_only=True, when_by_side=_WHEN)

CANDIDATES = {
    'structure_cf.v1': dict(
        **_COMMON, name='[RESEARCH] EDGE-02 confirmed structure', fn=sig_structure_cf,
        params=dict(STRUCTURE), reentry_lock=True,
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24),
        desc='EDGE-02 Stage-A only: original confirmed structure with cost, volume and side-regime gates.'),
    'structure_cf.cost_ablation.v1': dict(
        **_COMMON, name='[RESEARCH] EDGE-02 structure cost ablation', fn=sig_structure_cf_cost_ablation,
        params=dict(STRUCTURE, cost_x=None), reentry_lock=True,
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24),
        desc='EDGE-02 comparator: structure_cf.v1 with only its cost floor removed.'),
    'rsi2_reclaim.v1': dict(
        **_COMMON, name='[RESEARCH] EDGE-02 RSI2 reclaim', fn=sig_rsi2_reclaim,
        params=dict(RSI2), mgmt=dict(stop_atr=1.25, tp_r=1.5, max_bars=12),
        desc='EDGE-02 Stage-A only: prior-bar RSI2 exhaustion/reclaim, local trend, regime and cost floor.'),
    'squeeze_release.v1': dict(
        **_COMMON, name='[RESEARCH] EDGE-02 squeeze release', fn=sig_squeeze_release,
        params=dict(SQUEEZE), mgmt=dict(stop_atr=1.5, tp_r=2.0, max_bars=16),
        desc='EDGE-02 Stage-A only: prior compression to volume-confirmed Donchian release with trend/regime/cost gates.'),
}


def signals(key, d, ctx):
    """Evaluate one research candidate without modifying strategies.STRATEGIES."""
    st = CANDIDATES[key]
    s = st['fn'](d, st.get('params') or {}, ctx)
    out = {k: np.array(_x(s[k]).values, dtype=bool, copy=True) for k in ('le', 'se', 'lx', 'sx')}
    if 'atr' in s:
        out['atr'] = np.asarray(s['atr'], dtype=float).copy()
    return out
