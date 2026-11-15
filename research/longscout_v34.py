"""LONGSCOUT-V34: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families (round-number levels, half-retracement of a big swing).

L34A l34_round_number_cross_4h   long  the last completed daily close moved into a higher round-number band than the day before
                                       (bands start at 1, 2 and 5 times a power of ten), coin rising (R1); first 4h bar of
                                       the next day. Stop 2.5 ATR, 2.5R, 36 bars.
L34B l34_half_retrace_up_4h      long  within the last 90 completed days the close fell >= 30% from its high to a later low; the
                                       last completed close crosses above the midpoint of that swing, coin rising (R1).
                                       First 4h bar of the next day. Stop 3 ATR, 2.5R, 60 bars.
S34A s34_half_retrace_down_4h    short mirror: a >= 30% rise from a low to a later high within 90 days; the close crosses below the
                                       midpoint, coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _band(x):
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    ok = x > 0
    e = np.floor(np.log10(x[ok]))
    m = x[ok] / 10.0 ** e
    step = np.where(m >= 5, 2, np.where(m >= 2, 1, 0))
    out[ok] = e * 3 + step
    return out


def _round_cross(d, p):
    D = L27._days_v(d)
    b = pd.Series(_band(D.c.values), index=D.index)
    ok = (b > b.shift()) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_round_cross(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_round_cross(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _swing_mid(c, n, up):
    """Per day: midpoint of the swing (high then later low if up, low then later high if not) inside the last n closes,
    and its size; NaN when the extreme does not come after the opposite extreme."""
    v = c.values
    mid = np.full(len(v), np.nan); size = np.full(len(v), np.nan)
    for i in range(n - 1, len(v)):
        w = v[i - n + 1:i + 1]
        if np.isnan(w).any():
            continue
        if up:                                   # decline: high first, low after
            a = int(np.argmax(w)); b = a + int(np.argmin(w[a:]))
            hi, lo = w[a], w[b]
            if b > a and hi > 0:
                mid[i] = (hi + lo) / 2; size[i] = (hi - lo) / hi
        else:                                    # rise: low first, high after
            a = int(np.argmin(w)); b = a + int(np.argmax(w[a:]))
            lo, hi = w[a], w[b]
            if b > a and lo > 0:
                mid[i] = (hi + lo) / 2; size[i] = (hi - lo) / lo
    return pd.Series(mid, index=c.index), pd.Series(size, index=c.index)


def _half_retrace(d, p, up):
    D = L27._days_v(d)
    mid, size = _swing_mid(D.c.shift(), p['n'], up)            # swing measured on the days before the signal day
    big = size >= p['move']
    cross = (D.c > mid) & (D.c.shift() <= mid) if up else (D.c < mid) & (D.c.shift() >= mid)
    ok = big & cross & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_half_up(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_half_retrace(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_half_down(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_half_retrace(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


HR = dict(n=90, move=0.30)
CANDIDATES = {
    'l34_round_number_cross_4h': dict(
        name='[RESEARCH] L34A Round-number band cross in rising regime long 4h', style='breakout', fn=sig_round_cross,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'l34_half_retrace_up_4h': dict(
        name='[RESEARCH] L34B Half-retracement of a 30% decline long 4h', style='reversal', fn=sig_half_up,
        sides='long', tf='4h', research_only=True, params=dict(HR), mgmt=dict(stop_atr=3.0, tp_r=2.5, max_bars=60)),
    's34_half_retrace_down_4h': dict(
        name='[RESEARCH] S34A Half-retracement of a 30% rise short 4h', style='reversal', fn=sig_half_down,
        sides='short', tf='4h', research_only=True, params=dict(HR), mgmt=dict(stop_atr=3.0, tp_r=2.5, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
