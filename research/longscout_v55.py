"""LONGSCOUT-V55: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook shapes); none tuned.
Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the
unit-safe longscout_v26 helper. Family: classical chart patterns on completed daily bars, continued from V54 with
DIFFERENT patterns (head-and-shoulders, bull flag). Counted as new trials; V54's s54 is not changed or re-tested here.

S55A s55_head_shoulders_4h  short days j-60..j present. Left shoulder = highest high of j-60..j-41, head = highest high of
                                  j-40..j-21, right shoulder = highest high of j-20..j-3. Head >= 5% above the higher
                                  shoulder; shoulders within 5% of each other. Neckline = the lower of the two troughs (lowest
                                  low between left shoulder and head, and between head and right shoulder). Day j closes
                                  below the neckline for the first time; coin falling or sideways (R1). First 4h bar of the
                                  next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L55A l55_inverse_head_shoulders_4h long mirror on lows; neckline = the higher of the two peaks; close above it for the first
                                  time; coin rising or sideways.
L55B l55_bull_flag_4h       long  days j-20..j present. Pole top p = the day with the highest close in j-10..j-4; the pole
                                  rose >= 20% from the lowest low of days p-5..p; flag = days p+1..j-1 never above the pole
                                  day's high and never below 88% of its close; day j closes above the pole day's high, day
                                  j-1 did not; coin rising (R1). Stop 2.5 ATR, 2R, 30 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _hs_v(D, p, top):
    h, l, c = D.h.values, D.l.values, D.c.values
    n = len(c)
    full = L27._consec(D, 60).values
    out = np.zeros(n, bool)
    s = 1 if top else -1
    hi, lo = (h, l) if top else (-l, -h)                                 # mirror: work on negated lows/highs
    cc = c if top else -c
    for j in range(61, n):
        if not full[j]:
            continue
        a = j - 60 + int(np.argmax(hi[j - 60:j - 40]))
        b = j - 40 + int(np.argmax(hi[j - 40:j - 20]))
        r = j - 20 + int(np.argmax(hi[j - 20:j - 2]))
        LS, H, RS = hi[a], hi[b], hi[r]
        # ratios on the real price scale
        ls, hd, rs = s * LS, s * H, s * RS
        if top:
            shape = hd >= (1 + p['head']) * max(ls, rs) and abs(rs / ls - 1) <= p['tol']
        else:
            shape = hd <= (1 - p['head']) * min(ls, rs) and abs(rs / ls - 1) <= p['tol']
        if not shape:
            continue
        neck = min(lo[a:b + 1].min(), lo[b:r + 1].min())                 # the lower trough (top) / higher peak (bottom)
        out[j] = cc[j] < neck and cc[j - 1] >= neck
    return pd.Series(out, index=D.index)


def _flag_v(D, p):
    h, l, c = D.h.values, D.l.values, D.c.values
    n = len(c)
    full = L27._consec(D, 20).values
    out = np.zeros(n, bool)
    for j in range(21, n):
        if not full[j]:
            continue
        q = j - 10 + int(np.argmax(c[j - 10:j - 3]))
        pole = c[q] / l[q - 5:q + 1].min() - 1 >= p['pole']
        fl = h[q + 1:j].max() <= h[q] and l[q + 1:j].min() >= (1 - p['flag']) * c[q]
        out[j] = pole and fl and c[j] > h[q] and c[j - 1] <= h[q]
    return pd.Series(out, index=D.index)


def _hs(d, p, top):
    D = L27._days_v(d)
    return L27._next_day(_hs_v(D, p, top), D, d)


def _flag(d, p):
    D = L27._days_v(d)
    return L27._next_day(_flag_v(D, p), D, d)


def sig_hs(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_hs(d, p, True) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d),
                sx=_z(d))


def sig_ihs(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_hs(d, p, False) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d),
                sx=_z(d))


def sig_flag(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_flag(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


HS = dict(head=0.05, tol=0.05)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's55_head_shoulders_4h': dict(
        name='[RESEARCH] S55A Daily head-and-shoulders neckline break short 4h', style='reversal', fn=sig_hs,
        sides='short', tf='4h', research_only=True, params=dict(HS), mgmt=dict(MG)),
    'l55_inverse_head_shoulders_4h': dict(
        name='[RESEARCH] L55A Daily inverse head-and-shoulders neckline break long 4h', style='reversal', fn=sig_ihs,
        sides='long', tf='4h', research_only=True, params=dict(HS), mgmt=dict(MG)),
    'l55_bull_flag_4h': dict(
        name='[RESEARCH] L55B Daily bull flag breakout in rising regime long 4h', style='momentum', fn=sig_flag,
        sides='long', tf='4h', research_only=True, params=dict(pole=0.20, flag=0.12), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
