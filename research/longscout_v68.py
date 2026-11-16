"""LONGSCOUT-V68: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: breakout after a long DORMANT spell (no 20-day closing high for 60 days), and the
breakout RETEST (price comes back to the broken level and holds).

L68A l68_dormant_high_4h     long  day j closes above every close of days j-20..j-1, and no day in j-60..j-1 did the same;
                                   coin rising or sideways (R1). First 4h bar of the next day (modeled fill 04:00 UTC).
                                   Stop 2.5 ATR, 2R, 30 bars.
S68A s68_dormant_low_4h      short mirror: first 20-day closing low after 60 days without one; coin falling or sideways.
L68B l68_breakout_retest_4h  long  a breakout day b in j-10..j-1 closed above the highest high of days b-30..b-1 (level L). Day
                                   j is the first day after b whose low comes within 1% of L (low <= 1.01 L) while it still
                                   closes above L; coin rising (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _dormant_v(D, p, up):
    n, q = p['n'], p['quiet']
    new = (D.c > D.c.shift().rolling(n, min_periods=n).max()) if up else (D.c < D.c.shift().rolling(n, min_periods=n).min())
    new = new.fillna(False)
    recent = new.astype(float).shift().rolling(q, min_periods=q).sum() > 0
    return (new & ~recent & L27._consec(D, n + q)).fillna(False)


def _retest_v(D, p):
    n, w, tol = p['n'], p['w'], p['tol']
    h, l, c = D.h.values, D.l.values, D.c.values
    lvl = D.h.shift().rolling(n, min_periods=n).max().values
    brk = c > lvl
    full = L27._consec(D, n + w).values
    out = np.zeros(len(c), bool)
    for j in range(n + w, len(c)):
        if not full[j]:
            continue
        for b in range(j - 1, j - w - 1, -1):                             # most recent breakout first
            if not brk[b]:
                continue
            L = lvl[b]
            touched_before = any(l[k] <= (1 + tol) * L and c[k] > L for k in range(b + 1, j))
            out[j] = (not touched_before) and l[j] <= (1 + tol) * L and c[j] > L
            break
    return pd.Series(out, index=D.index)


def _on(fn):
    def f(d, p, *a):
        D = L27._days_v(d)
        return L27._next_day(fn(D, p, *a), D, d)
    return f


_dormant, _retest = _on(_dormant_v), _on(_retest_v)


def sig_high(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_dormant(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_low(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_dormant(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


def sig_retest(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_retest(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


DP = dict(n=20, quiet=60)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l68_dormant_high_4h': dict(
        name='[RESEARCH] L68A First 20-day closing high after 60 dormant days long 4h', style='trend', fn=sig_high,
        sides='long', tf='4h', research_only=True, params=dict(DP), mgmt=dict(MG)),
    's68_dormant_low_4h': dict(
        name='[RESEARCH] S68A First 20-day closing low after 60 dormant days short 4h', style='trend', fn=sig_low,
        sides='short', tf='4h', research_only=True, params=dict(DP), mgmt=dict(MG)),
    'l68_breakout_retest_4h': dict(
        name='[RESEARCH] L68B Daily 30-day breakout retest that holds long 4h', style='trend', fn=sig_retest,
        sides='long', tf='4h', research_only=True, params=dict(n=30, w=10, tol=0.01), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
