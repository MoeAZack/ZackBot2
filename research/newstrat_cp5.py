"""NEWSTRAT-CP5: two more hourly long/short families for a frequent account-4 basket (Claude Projects, 2026-11-12).

FROZEN before any CP5 run. Every parameter below was fixed a priori; none were tuned. All default-OFF, research_only,
registered only into this process's strategies.STRATEGIES copy. Same runner and gate as CP4 (research/newstrat_cp4_run.py
with this module); periods are consumedDEV early (<2025) and consumedDEV later (>=2025), neither is a holdout.

Not redone: Codex C1H H1-H6, U1-U3 cross-asset, flow candidates; PR #334 hourly ideas; CP4 Z1-Z5; the 1h registry
sleeves (incl. session inventory and weekend liquidity, which are session/calendar-based but fixed-window, not learned).

CP-V1 value_area_reentry_1h   both   Market-profile "80% rule": the prior UTC day's volume profile (24 hourly bars, volume
                                     spread evenly over each bar's high-low range, 24 price bins) gives POC and a 70%
                                     value area. Today, after trading outside the value area, two consecutive hourly
                                     closes back inside it -> trade toward the opposite edge (long from below VAL,
                                     short from above VAH). Fixed 3 ATR stop, 2R target, 24 h cap.
CP-V2 hour_seasonality_1h     both   Walk-forward hour-of-day seasonality, per coin. At hour h, the mean and t-stat of
                                     the coin's own next-6-hour log return starting at hour h, over ALL completed past
                                     windows that started at hour h (expanding, at least 200 of them). Long when
                                     t >= 2.5, short when t <= -2.5. Held 6 hours (time exit), 3 ATR stop, no target.
"""
import numpy as np
import pandas as pd
import strategies as S

FIXED = dict(be_r=0, trail_atr=0)


def _z(d):
    return pd.Series(False, index=d.index)


def _value_area(h, l, v, bins, frac):
    lo, hi = float(np.min(l)), float(np.max(h))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo: return np.nan, np.nan
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    for a, b, w in zip(l, h, v):
        if not (np.isfinite(a) and np.isfinite(b) and np.isfinite(w)) or w <= 0: continue
        i0 = min(max(np.searchsorted(edges, a, side='right') - 1, 0), bins - 1)
        i1 = min(max(np.searchsorted(edges, b, side='right') - 1, 0), bins - 1)
        vol[i0:i1 + 1] += w / (i1 - i0 + 1)
    if vol.sum() <= 0: return np.nan, np.nan
    poc = int(np.argmax(vol)); a = b = poc; tot = vol[poc]
    while tot < frac * vol.sum():
        up = vol[b + 1] if b + 1 < bins else -1
        dn = vol[a - 1] if a - 1 >= 0 else -1
        if up >= dn: b += 1; tot += vol[b]
        else: a -= 1; tot += vol[a]
    return edges[a], edges[b + 1]


def sig_value_area(d, p, ctx):
    t = pd.to_datetime(d.t)
    day = t.dt.floor('D')
    h, l, c, v = d.h.values, d.l.values, d.c.values, d.v.values
    n = len(d); le = np.zeros(n, bool); se = np.zeros(n, bool)
    days = day.values
    starts = np.flatnonzero(np.r_[True, days[1:] != days[:-1]])
    bounds = list(starts) + [n]
    for k in range(1, len(bounds) - 1):
        p0, p1, s0, s1 = bounds[k - 1], bounds[k], bounds[k], bounds[k + 1]
        if p1 - p0 != 24: continue                          # need a complete prior day
        val, vah = _value_area(h[p0:p1], l[p0:p1], v[p0:p1], p['bins'], p['frac'])
        if not np.isfinite(val): continue
        below = above = False; streak_in = 0; done = False
        for i in range(s0, s1):
            if done: break
            inside = val < c[i] < vah
            if c[i] <= val: below = True
            if c[i] >= vah: above = True
            streak_in = streak_in + 1 if inside else 0
            if streak_in >= 2:
                if below and not above: le[i] = True; done = True
                elif above and not below: se[i] = True; done = True
    return dict(le=pd.Series(le, d.index), se=pd.Series(se, d.index), lx=_z(d), sx=_z(d))


def sig_hour_season(d, p, ctx):
    t = pd.to_datetime(d.t)
    hr = t.dt.hour.values
    lc = np.log(d.c.values)
    H = p['hold']
    fwd = np.full(len(lc), np.nan)
    fwd[:-H] = lc[H:] - lc[:-H]                              # return of the window starting at bar j (known at j+H)
    n = len(lc); tstat = np.full(n, np.nan)
    s1 = np.zeros(24); s2 = np.zeros(24); cnt = np.zeros(24)
    for i in range(n):
        j = i - H                                           # window starting at j completes at bar i: now known
        if j >= 0 and np.isfinite(fwd[j]):
            k = hr[j]; s1[k] += fwd[j]; s2[k] += fwd[j] ** 2; cnt[k] += 1
        k = hr[i]
        if cnt[k] >= p['min_n']:
            m = s1[k] / cnt[k]; var = max(s2[k] / cnt[k] - m * m, 0.0) * cnt[k] / (cnt[k] - 1)
            if var > 0: tstat[i] = m / np.sqrt(var / cnt[k])
    ts = pd.Series(tstat, index=d.index)
    return dict(le=S.X(ts >= p['t']), se=S.X(ts <= -p['t']), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'cp_v1_value_area_reentry_1h': dict(
        name='[RESEARCH] CP-V1 Value-area re-entry (80% rule) 1h', style='mean_reversion', fn=sig_value_area,
        sides='both', tf='1h', research_only=True, params=dict(bins=24, frac=0.70),
        mgmt=dict(stop_atr=3.0, tp_r=2.0, max_bars=24, **FIXED)),
    'cp_v2_hour_seasonality_1h': dict(
        name='[RESEARCH] CP-V2 Walk-forward hour-of-day seasonality 1h', style='calendar', fn=sig_hour_season,
        sides='both', tf='1h', research_only=True, params=dict(hold=6, min_n=200, t=2.5),
        mgmt=dict(stop_atr=3.0, tp_r=50.0, max_bars=6, **FIXED)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
