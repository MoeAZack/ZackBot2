"""LONGSCOUT-V69: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: the bullish MORNING STAR at a 20-day low (V61 tested only its bearish mirror), and
the failed DEAD-CAT BOUNCE (a 10% bounce off a 30-day closing low that gives way to a new closing low).

L69A l69_morning_star_4h   long  day j-2 red with body >= 60% of its range; day j-1 body <= 30% of j-2's body; day j green and
                                 closes above the midpoint of j-2's body; one of the three days made the 20-day low; coin
                                 rising or sideways (R1). First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR,
                                 2R, 30 bars.
S69A s69_dead_cat_fail_4h  short day j closes below the lowest close of days j-30..j-1. Let a = the most recent day in j-30..j-1
                                 holding that lowest close; some close in days a+1..j-1 was at least 10% above close a; coin
                                 falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v27 as L27
import longscout_v61 as L61

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _mstar_v(D, p):
    o, h, l, c = D.o, D.h, D.l, D.c
    b2 = (o - c).shift(2); r2 = (h - l).shift(2)
    b1 = (c - o).shift().abs()
    ok = (b2 > 0) & (b2 >= 0.6 * r2) & (b1 <= 0.3 * b2) & (c > o) & (c > (o.shift(2) + c.shift(2)) / 2)
    return (ok & L61._ext(D, p['n'], False, 3) & L27._consec(D, p['n'])).fillna(False)


def _dcat_v(D, p):
    n, k = p['n'], p['bounce']
    c = D.c.values
    full = L27._consec(D, n).values
    out = np.zeros(len(c), bool)
    for j in range(n, len(c)):
        if not full[j]:
            continue
        w = c[j - n:j]
        lo = w.min()
        if not c[j] < lo:
            continue
        a = j - n + int(np.flatnonzero(w == lo)[-1])
        out[j] = a + 1 < j and c[a + 1:j].max() >= (1 + k) * lo
    return pd.Series(out, index=D.index)


def sig_mstar(d, p, ctx):
    cr = L1.coin_regime(d); D = L17._days(d)
    return dict(le=S.X(pd.Series(L27._next_day(_mstar_v(D, p), D, d) & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=_z(d), lx=_z(d), sx=_z(d))


def sig_dcat(d, p, ctx):
    cr = L1.coin_regime(d); D = L27._days_v(d)
    return dict(le=_z(d), se=S.X(pd.Series(L27._next_day(_dcat_v(D, p), D, d) & (cr == -1), index=d.index)),
                lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l69_morning_star_4h': dict(
        name='[RESEARCH] L69A Daily morning star at a 20-day low long 4h', style='reversal', fn=sig_mstar,
        sides='long', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(MG)),
    's69_dead_cat_fail_4h': dict(
        name='[RESEARCH] S69A Failed 10% dead-cat bounce to a new 30-day closing low short 4h', style='trend',
        fn=sig_dcat, sides='short', tf='4h', research_only=True, params=dict(n=30, bounce=0.10), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
