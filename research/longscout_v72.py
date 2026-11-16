"""LONGSCOUT-V72: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families from O'Neil-style tape reading: the FOLLOW-THROUGH day after a closing low, and a
cluster of DISTRIBUTION days (earlier volume rules used single shocks, climaxes, pocket pivots or balance ratios).

L72A l72_follow_through_4h  long  days j-30..j present. The lowest close of days j-30..j was set on a day a with j-10 <= a <= j-4
                                  (latest such day). Day j closes at least 3% above day j-1 on volume above day j-1's, and no day
                                  in a+1..j-1 did the same (first follow-through). Coin rising or sideways (R1). First 4h bar of
                                  the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S72A s72_distribution_4h    short a distribution day closes at least 1% below the prior close on volume above the prior day's. Days
                                  j-25..j present; at least 6 of days j-24..j are distribution days, day j is one, and fewer than 6
                                  of days j-25..j-1 were (first day). Coin falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _ftd_v(D, p):
    n, a0, a1, up = p['n'], p['min_age'], p['max_age'], p['up']
    c, v = D.c.values, D.v.values
    hit = np.r_[False, (c[1:] >= (1 + up) * c[:-1]) & (v[1:] > v[:-1])]
    full = L27._consec(D, n).values
    out = np.zeros(len(c), bool)
    for j in range(n, len(c)):
        if not (full[j] and hit[j]):
            continue
        w = c[j - n:j + 1]
        a = j - n + int(np.flatnonzero(w == w.min())[-1])
        out[j] = j - a1 <= a <= j - a0 and not hit[a + 1:j].any()
    return pd.Series(out, index=D.index)


def _dist_v(D, p):
    n, k, dn = p['n'], p['k'], p['down']
    dd = ((D.c <= (1 - dn) * D.c.shift()) & (D.v > D.v.shift())).astype(float)
    cnt = dd.rolling(n, min_periods=n).sum()
    ok = (dd > 0) & (cnt >= k) & (cnt.shift() < k)
    return (ok & L27._consec(D, n)).fillna(False)


def sig_ftd(d, p, ctx):
    cr = L1.coin_regime(d); D = L27._days_v(d)
    return dict(le=S.X(pd.Series(L27._next_day(_ftd_v(D, p), D, d) & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=_z(d), lx=_z(d), sx=_z(d))


def sig_dist(d, p, ctx):
    cr = L1.coin_regime(d); D = L27._days_v(d)
    return dict(le=_z(d), se=S.X(pd.Series(L27._next_day(_dist_v(D, p), D, d) & (cr == -1), index=d.index)),
                lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l72_follow_through_4h': dict(
        name='[RESEARCH] L72A Daily follow-through day 4-10 days after a 30-day closing low long 4h', style='reversal',
        fn=sig_ftd, sides='long', tf='4h', research_only=True, params=dict(n=30, min_age=4, max_age=10, up=0.03),
        mgmt=dict(MG)),
    's72_distribution_4h': dict(
        name='[RESEARCH] S72A Six distribution days in 25 in falling regime short 4h', style='trend', fn=sig_dist,
        sides='short', tf='4h', research_only=True, params=dict(n=25, k=6, down=0.01), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
