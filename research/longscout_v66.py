"""LONGSCOUT-V66: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: moving-average COMPRESSION (EMA20/50/100 bunched together) before a break, and
the "pocket pivot" volume signature (up-day volume larger than every down-day volume of the prior 10 days).

R66A r66_ma_compression_break_4h both  daily EMA20, EMA50 and EMA100 all within 3% of their mean on day j-1; day j closes
                                         above all three (first day, j-1 did not) -> long if coin rising or sideways (R1);
                                         below all three (first day) -> short if falling or sideways. First 4h bar of the
                                         next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L66A l66_pocket_pivot_4h         long  day j is up (close above day j-1) with volume above the largest volume of the down
                                         days among j-10..j-1, closes above daily EMA10; coin rising (R1). Same management.
S66A s66_pocket_pivot_short_4h   short mirror: down day with volume above every up-day volume of the prior 10 days, closes
                                         below EMA10; coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _comp_v(D, p, up):
    e = pd.concat([S.ema(D.c, 20), S.ema(D.c, 50), S.ema(D.c, 100)], axis=1)
    m = e.mean(axis=1)
    tight = ((e.max(axis=1) / m - 1) <= p['band']) & ((1 - e.min(axis=1) / m) <= p['band'])
    if up:
        side = D.c > e.max(axis=1)
    else:
        side = D.c < e.min(axis=1)
    ok = side & ~side.shift(fill_value=False).astype(bool) & tight.shift(fill_value=False).astype(bool)
    return (ok & L27._consec(D, 2)).fillna(False)


def _pivot_v(D, p, up):
    n = p['n']
    ch = D.c.diff()
    other = (ch < 0) if up else (ch > 0)
    vmax = D.v.where(other, 0.0).shift().rolling(n, min_periods=n).max()
    e10 = S.ema(D.c, 10)
    ok = ((ch > 0) & (D.c > e10)) if up else ((ch < 0) & (D.c < e10))
    return (ok & (D.v > vmax) & (vmax > 0) & L27._consec(D, n + 1)).fillna(False)


def _on(fn):
    def f(d, p, up):
        D = L27._days_v(d)
        return L27._next_day(fn(D, p, up), D, d)
    return f


_comp, _pivot = _on(_comp_v), _on(_pivot_v)


def sig_comp(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_comp(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=S.X(pd.Series(_comp(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


def sig_pivot(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_pivot(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_pivot_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_pivot(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r66_ma_compression_break_4h': dict(
        name='[RESEARCH] R66A Daily EMA20/50/100 compression break L/S 4h', style='trend', fn=sig_comp,
        sides='both', tf='4h', research_only=True, params=dict(band=0.03), mgmt=dict(MG)),
    'l66_pocket_pivot_4h': dict(
        name='[RESEARCH] L66A Daily pocket pivot in rising regime long 4h', style='momentum', fn=sig_pivot,
        sides='long', tf='4h', research_only=True, params=dict(n=10), mgmt=dict(MG)),
    's66_pocket_pivot_short_4h': dict(
        name='[RESEARCH] S66A Daily inverse pocket pivot in falling regime short 4h', style='momentum', fn=sig_pivot_short,
        sides='short', tf='4h', research_only=True, params=dict(n=10), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
