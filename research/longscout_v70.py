"""LONGSCOUT-V70: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: the calendar-MONTH open as a pivot (V35 used the UTC-day opening range, V12/V35 the
prior week), and a short-side volume DRY-UP near the 20-day low (V27 tested only the long side near the high).

R70A r70_month_open_cross_4h      both  MO = open of the first UTC day of day j's calendar month; every day from that first day
                                        to day j is present and day j is not the first day. Close j > MO and close j-1 <= MO ->
                                        long if the coin is rising (R1); close j < MO and close j-1 >= MO -> short if falling.
                                        First 4h bar of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S70A s70_volume_dryup_near_low_4h short days j-32..j present; days j-2, j-1 and j each had volume below half the median volume
                                        of the 30 days before it, and close j is within 5% of the lowest close of days
                                        j-19..j; coin falling (R1). Same management.
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


def _mopen_v(D, up):
    o, c = D.o.values, D.c.values
    idx = pd.DatetimeIndex(D.index)
    out = np.zeros(len(c), bool)
    for j in range(1, len(c)):
        k = idx[j].day - 1                                               # days since the 1st of the month
        if k < 1 or j - k < 0 or idx[j - k] != idx[j] - pd.Timedelta(days=k):
            continue
        mo = o[j - k]
        out[j] = (c[j] > mo and c[j - 1] <= mo) if up else (c[j] < mo and c[j - 1] >= mo)
    return pd.Series(out, index=D.index)


def _dry_v(D, p):
    base, days, q, near, n = p['base'], p['days'], p['quiet'], p['near'], p['lo_n']
    med = D.v.rolling(base, min_periods=base).median().shift()
    quiet = (D.v < q * med).astype(float).rolling(days, min_periods=days).sum() == days
    close = D.c <= (1 + near) * D.c.rolling(n, min_periods=n).min()
    return (quiet & close & L27._consec(D, base + days - 1)).fillna(False)


def sig_mopen(d, p, ctx):
    cr = L1.coin_regime(d); D = L17._days(d)
    return dict(le=S.X(pd.Series(L27._next_day(_mopen_v(D, True), D, d) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(L27._next_day(_mopen_v(D, False), D, d) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def sig_dry(d, p, ctx):
    cr = L1.coin_regime(d); D = L27._days_v(d)
    return dict(le=_z(d), se=S.X(pd.Series(L27._next_day(_dry_v(D, p), D, d) & (cr == -1), index=d.index)), lx=_z(d),
                sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r70_month_open_cross_4h': dict(
        name='[RESEARCH] R70A Calendar-month open cross with regime L/S 4h', style='trend', fn=sig_mopen,
        sides='both', tf='4h', research_only=True, params=dict(), mgmt=dict(MG)),
    's70_volume_dryup_near_low_4h': dict(
        name='[RESEARCH] S70A Volume dry-up near 20-day low in falling regime short 4h', style='trend', fn=sig_dry,
        sides='short', tf='4h', research_only=True, params=dict(base=30, quiet=0.5, days=3, near=0.05, lo_n=20),
        mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
