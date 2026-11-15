"""LONGSCOUT-V77: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family members: the BEAR flag (V55 tested only the bull flag) and the FAILED bull flag (the
flag breaks down instead of up).

S77A s77_bear_flag_4h        short days j-20..j present. Pole bottom q = the day with the lowest close in j-10..j-4 (earliest if
                                   tied); the pole fell >= 20% from the highest high of days q-5..q to close q; flag = days
                                   q+1..j-1 never below day q's low and never above 112% of its close; day j closes below day q's
                                   low, day j-1 did not; coin falling (R1). First 4h bar of the next day (modeled fill 04:00
                                   UTC). Stop 2.5 ATR, 2R, 30 bars.
S77B s77_failed_bull_flag_4h short days j-20..j present. Bull pole as in V55: q = the day with the highest close in j-10..j-4
                                   (earliest if tied), close q >= 1.20 x the lowest low of days q-5..q; days q+1..j-1 never
                                   above day q's high; day j closes below the lowest low of days q+1..j-1 and day j-1 did not
                                   close below the lowest low of days q+1..j-2 (j-1 > q+1); coin falling or sideways (R1).
                                   Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _bear_v(D, p):
    h, l, c = D.h.values, D.l.values, D.c.values
    full = L27._consec(D, 20).values
    out = np.zeros(len(c), bool)
    for j in range(20, len(c)):
        if not full[j]:
            continue
        q = j - 10 + int(np.argmin(c[j - 10:j - 3]))
        pole = c[q] <= (1 - p['pole']) * h[q - 5:q + 1].max()
        fl = l[q + 1:j].min() >= l[q] and h[q + 1:j].max() <= (1 + p['flag']) * c[q]
        out[j] = pole and fl and c[j] < l[q] and c[j - 1] >= l[q]
    return pd.Series(out, index=D.index)


def _fail_v(D, p):
    h, l, c = D.h.values, D.l.values, D.c.values
    full = L27._consec(D, 20).values
    out = np.zeros(len(c), bool)
    for j in range(20, len(c)):
        if not full[j]:
            continue
        q = j - 10 + int(np.argmax(c[j - 10:j - 3]))
        pole = c[q] >= (1 + p['pole']) * l[q - 5:q + 1].min()
        if not (pole and h[q + 1:j].max() <= h[q] and j - 1 > q + 1):
            continue
        out[j] = c[j] < l[q + 1:j].min() and not c[j - 1] < l[q + 1:j - 1].min()
    return pd.Series(out, index=D.index)


def _on(fn):
    def f(d, p):
        D = L27._days_v(d)
        return L27._next_day(fn(D, p), D, d)
    return f


_bear, _fail = _on(_bear_v), _on(_fail_v)


def sig_bear(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_bear(d, p) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def sig_fail(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_fail(d, p) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's77_bear_flag_4h': dict(
        name='[RESEARCH] S77A Daily bear flag breakdown in falling regime short 4h', style='momentum', fn=sig_bear,
        sides='short', tf='4h', research_only=True, params=dict(pole=0.20, flag=0.12), mgmt=dict(MG)),
    's77_failed_bull_flag_4h': dict(
        name='[RESEARCH] S77B Daily failed bull flag breakdown short 4h', style='reversal', fn=sig_fail,
        sides='short', tf='4h', research_only=True, params=dict(pole=0.20), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
