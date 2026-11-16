"""LONGSCOUT-V39: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New indicator families on completed daily bars (Vortex, Mass Index, Elder force).

R39A r39_daily_vortex_4h      both  daily Vortex(14): VI+ crosses above VI- with the coin rising (R1) -> long; VI- crosses above
                                    VI+ with the coin falling -> short. First 4h bar of the next day. Stop 2.5 ATR, 2.5R, 36 bars.
L39A l39_mass_index_bulge_4h  long  daily Mass Index (9/9 EMA of range, 25-day sum) rose above 27 within the last 10 days and
                                    now falls below 26.5 (reversal bulge), with the daily close above its 9-day EMA and the coin
                                    rising (R1). First 4h bar of the next day. Stop 2.5 ATR, 2.5R, 36 bars.
R39B r39_daily_force_index_4h both  13-day EMA of the daily force index (close change x volume) crosses above zero with the coin
                                    rising -> long; below zero with the coin falling -> short. Stop 2.5 ATR, 2R, 30 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _vortex(d, p, up):
    D = L27._days_v(d)
    n = p['n']
    pc = D.c.shift()
    tr = pd.concat([D.h - D.l, (D.h - pc).abs(), (D.l - pc).abs()], axis=1).max(axis=1)
    vp = (D.h - D.l.shift()).abs().rolling(n, min_periods=n).sum() / tr.rolling(n, min_periods=n).sum()
    vm = (D.l - D.h.shift()).abs().rolling(n, min_periods=n).sum() / tr.rolling(n, min_periods=n).sum()
    a, b = (vp, vm) if up else (vm, vp)
    ok = (a > b) & (a.shift() <= b.shift()) & L27._consec(D, n + 1)
    return L27._next_day(ok, D, d)


def sig_vortex(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_vortex(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_vortex(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _mass(d, p):
    D = L27._days_v(d)
    rng = D.h - D.l
    e1 = _ema(rng, 9); e2 = _ema(e1, 9)
    mi = (e1 / e2).rolling(p['sum'], min_periods=p['sum']).sum()
    was = (mi > p['hi']).astype(float).rolling(p['look'], min_periods=p['look']).sum() > 0
    ok = was & (mi < p['lo']) & (mi.shift() >= p['lo']) & (D.c > _ema(D.c, 9)) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_mass(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_mass(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _force(d, p, up):
    D = L27._days_v(d)
    fi = _ema(D.c.diff() * D.v, p['n'])
    ok = ((fi > 0) & (fi.shift() <= 0)) if up else ((fi < 0) & (fi.shift() >= 0))
    ok = ok & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_force(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_force(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_force(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r39_daily_vortex_4h': dict(
        name='[RESEARCH] R39A Daily Vortex(14) cross with regime L/S 4h', style='trend', fn=sig_vortex,
        sides='both', tf='4h', research_only=True, params=dict(n=14), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'l39_mass_index_bulge_4h': dict(
        name='[RESEARCH] L39A Daily Mass Index reversal bulge in rising regime long 4h', style='reversal', fn=sig_mass,
        sides='long', tf='4h', research_only=True, params=dict(sum=25, hi=27.0, lo=26.5, look=10),
        mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'r39_daily_force_index_4h': dict(
        name='[RESEARCH] R39B Daily 13-EMA force index zero cross with regime L/S 4h', style='momentum', fn=sig_force,
        sides='both', tf='4h', research_only=True, params=dict(n=13), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
