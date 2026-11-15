"""LONGSCOUT-V59: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: weekly candle engulfing patterns, and persistence of a coin's position in its
50-day range (time spent near the top/bottom, not a breakout).

R59A r59_weekly_engulfing_4h  both  complete Mon-Sun weeks only (two consecutive). Bullish: the prior week was red, the last week
                                    is green, opened at or below the prior close and closed above the prior open -> long if the
                                    coin is rising (R1). Bearish mirror -> short if falling. First 4h bar of the following
                                    Monday (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L59A l59_range_top_persist_4h long  the last completed day is the 10th consecutive day whose close sits in the top 25% of its own
                                    50-day high-low range (days j-49..j), coin rising (R1). First 4h bar of the next day.
                                    Stop 2.5 ATR, 2R, 30 bars.
S59A s59_range_low_persist_4h short mirror: 10th consecutive day in the bottom 25%, coin falling (R1).
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


def _weekly_v(D, bull):
    """Flag on the Sunday of a complete week that engulfs the previous complete week."""
    mon = D.index - pd.to_timedelta(D.index.dayofweek, unit='D')
    g = D.groupby(mon)
    W = pd.DataFrame({'o': g.o.first(), 'c': g.c.last(), 'n': g.size(), 'last': g.apply(lambda x: x.index[-1])})
    W = W[W.n == 7]
    step = W.index.to_series().diff() == pd.Timedelta(days=7)
    po, pc = W.o.shift(), W.c.shift()
    if bull:
        ok = (pc < po) & (W.c > W.o) & (W.o <= pc) & (W.c > po)
    else:
        ok = (pc > po) & (W.c < W.o) & (W.o >= pc) & (W.c < po)
    ok = ok & step
    out = pd.Series(False, index=D.index)
    out.loc[W['last'][ok].values] = True
    return out


def _weekly(d, p, bull):
    D = L17._days(d)
    return L27._next_day(_weekly_v(D, bull), D, d)


def _persist_v(D, p, top):
    n, k, q = p['n'], p['k'], p['q']
    hi = D.h.rolling(n, min_periods=n).max(); lo = D.l.rolling(n, min_periods=n).min()
    pos = (D.c - lo) / (hi - lo).replace(0, np.nan)
    inz = (pos >= 1 - q) if top else (pos <= q)
    inz = inz.fillna(False).astype(float)
    full = L27._consec(D, n + k)
    run = inz.rolling(k, min_periods=k).sum() == k
    prev = inz.shift(k).fillna(0.0) == 0.0                                # day j-k was outside -> exactly the 10th day
    return (run & prev & full).fillna(False)


def _persist(d, p, top):
    D = L27._days_v(d)
    return L27._next_day(_persist_v(D, p, top), D, d)


def sig_weekly(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_weekly(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_weekly(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def sig_top(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_persist(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_low(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_persist(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


PP = dict(n=50, k=10, q=0.25)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r59_weekly_engulfing_4h': dict(
        name='[RESEARCH] R59A Weekly engulfing candle with regime L/S 4h', style='reversal', fn=sig_weekly,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(MG)),
    'l59_range_top_persist_4h': dict(
        name='[RESEARCH] L59A Ten days in the top quarter of the 50-day range long 4h', style='momentum', fn=sig_top,
        sides='long', tf='4h', research_only=True, params=dict(PP), mgmt=dict(MG)),
    's59_range_low_persist_4h': dict(
        name='[RESEARCH] S59A Ten days in the bottom quarter of the 50-day range short 4h', style='momentum', fn=sig_low,
        sides='short', tf='4h', research_only=True, params=dict(PP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
