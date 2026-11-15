"""LONGSCOUT-V61: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: multi-day DAILY candle reversal patterns at a 20-day extreme (engulfing, evening star).
Earlier rounds used single-day hammer/star (V19, V37), outside days (V17) and weekly engulfing (V59), not these.

S61A s61_bear_engulf_high_4h  short day j-1 green, day j red, j opens at/above j-1's close and closes below j-1's open; day j or
                                    j-1 made the highest high of days j-19..j (20-day high); coin falling or sideways (R1).
                                    First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L61A l61_bull_engulf_low_4h   long  mirror at a 20-day low; coin rising or sideways.
S61B s61_evening_star_4h      short day j-2 green with body >= 60% of its range; day j-1 body <= 30% of j-2's body; day j red and
                                    closes below the midpoint of j-2's body; one of the three days made the 20-day high; coin
                                    falling or sideways. Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _ext(D, n, top, k):
    """Day j or one of the k-1 days before it set the n-day extreme (window j-n+1..j)."""
    if top:
        e = D.h.rolling(n, min_periods=n).max()
        hit = D.h.rolling(k, min_periods=k).max() >= e
    else:
        e = D.l.rolling(n, min_periods=n).min()
        hit = D.l.rolling(k, min_periods=k).min() <= e
    return hit


def _engulf_v(D, p, bear):
    o, c = D.o, D.c
    po, pc = o.shift(), c.shift()
    if bear:
        ok = (pc > po) & (c < o) & (o >= pc) & (c < po)
    else:
        ok = (pc < po) & (c > o) & (o <= pc) & (c > po)
    return (ok & _ext(D, p['n'], bear, 2) & L27._consec(D, p['n'])).fillna(False)


def _star_v(D, p):
    o, h, l, c = D.o, D.h, D.l, D.c
    b2 = (c - o).shift(2); r2 = (h - l).shift(2)
    b1 = (c - o).shift().abs()
    ok = (b2 > 0) & (b2 >= 0.6 * r2) & (b1 <= 0.3 * b2) & (c < o) & (c < (o.shift(2) + c.shift(2)) / 2)
    return (ok & _ext(D, p['n'], True, 3) & L27._consec(D, p['n'])).fillna(False)


def _on(D, flag, d):
    return L27._next_day(flag, D, d)


def sig_bear(d, p, ctx):
    cr = L1.coin_regime(d); D = L17._days(d)
    return dict(le=_z(d), se=S.X(pd.Series(_on(D, _engulf_v(D, p, True), d) & np.isin(cr, (0.0, -1.0)), index=d.index)),
                lx=_z(d), sx=_z(d))


def sig_bull(d, p, ctx):
    cr = L1.coin_regime(d); D = L17._days(d)
    return dict(le=S.X(pd.Series(_on(D, _engulf_v(D, p, False), d) & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=_z(d), lx=_z(d), sx=_z(d))


def sig_star(d, p, ctx):
    cr = L1.coin_regime(d); D = L17._days(d)
    return dict(le=_z(d), se=S.X(pd.Series(_on(D, _star_v(D, p), d) & np.isin(cr, (0.0, -1.0)), index=d.index)),
                lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's61_bear_engulf_high_4h': dict(
        name='[RESEARCH] S61A Daily bearish engulfing at a 20-day high short 4h', style='reversal', fn=sig_bear,
        sides='short', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
    'l61_bull_engulf_low_4h': dict(
        name='[RESEARCH] L61A Daily bullish engulfing at a 20-day low long 4h', style='reversal', fn=sig_bull,
        sides='long', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
    's61_evening_star_4h': dict(
        name='[RESEARCH] S61B Daily evening star at a 20-day high short 4h', style='reversal', fn=sig_star,
        sides='short', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
