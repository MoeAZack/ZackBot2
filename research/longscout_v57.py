"""LONGSCOUT-V57: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. Family: converging trend-line patterns (triangle, wedges) on completed daily bars. Trend lines are
least-squares lines through the daily highs and lows of the 20 days BEFORE day j, projected one day forward to day j.

R57A r57_triangle_break_4h   both  highs line falling and lows line rising (symmetric triangle). Day j closes above the
                                   projected highs line and day j-1 closed at or below its own projection -> long if the coin
                                   is rising or sideways (R1); closes below the projected lows line (first time) -> short if
                                   falling or sideways. First 4h bar of the next day (modeled fill 04:00 UTC).
                                   Stop 2.5 ATR, 2R, 30 bars.
S57A s57_rising_wedge_4h     short both lines rising, lows line steeper (converging up); first close below the projected lows
                                   line; coin falling or sideways. Same management.
L57A l57_falling_wedge_4h    long  both lines falling, highs line steeper (converging down); first close above the projected
                                   highs line; coin rising or sideways. Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _lines(D, n):
    """Per day j: (slope_hi, proj_hi, slope_lo, proj_lo) from a least-squares fit on days j-n..j-1, projected to j."""
    x = np.arange(n, dtype=float); xm = x.mean(); sxx = ((x - xm) ** 2).sum()
    def fit(y):
        ys = y.shift()                                                    # window ends at day j-1
        slope = ys.rolling(n, min_periods=n).apply(lambda a: ((x - xm) * (a - a.mean())).sum() / sxx, raw=True)
        mean = ys.rolling(n, min_periods=n).mean()
        return slope, mean + slope * (n - xm)                             # value at x = n (day j)
    sh, ph = fit(D.h); sl, pl = fit(D.l)
    return sh, ph, sl, pl


def _shapes(D, p):
    sh, ph, sl, pl = _lines(D, p['n'])
    full = L27._consec(D, p['n'] + 1)
    up = (D.c > ph) & (D.c.shift() <= ph.shift())
    dn = (D.c < pl) & (D.c.shift() >= pl.shift())
    tri = (sh < 0) & (sl > 0)
    rw = (sh > 0) & (sl > sh)
    fw = (sl < 0) & (sh < sl)
    f = lambda s: (s & full).fillna(False)
    return dict(tri_up=f(tri & up), tri_dn=f(tri & dn), rw=f(rw & dn), fw=f(fw & up))


def _pick(d, p, name):
    D = L27._days_v(d)
    return L27._next_day(_shapes(D, p)[name], D, d)


def sig_tri(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_pick(d, p, 'tri_up') & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=S.X(pd.Series(_pick(d, p, 'tri_dn') & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


def sig_rw(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_pick(d, p, 'rw') & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


def sig_fw(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_pick(d, p, 'fw') & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r57_triangle_break_4h': dict(
        name='[RESEARCH] R57A Daily 20-day symmetric triangle break L/S 4h', style='trend', fn=sig_tri,
        sides='both', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
    's57_rising_wedge_4h': dict(
        name='[RESEARCH] S57A Daily 20-day rising wedge break short 4h', style='reversal', fn=sig_rw,
        sides='short', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
    'l57_falling_wedge_4h': dict(
        name='[RESEARCH] L57A Daily 20-day falling wedge break long 4h', style='reversal', fn=sig_fw,
        sides='long', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
