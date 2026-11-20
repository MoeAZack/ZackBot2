"""NEWSTRAT-CP wave 3: replacements for account 4's six 15m scalp sleeves (Claude Projects, 2026-11-12).

FROZEN before any wave-3 run. Every parameter below was fixed a priori; none were tuned. A changed hypothesis is a new
candidate. All default-OFF, research_only, registered only into this process's strategies.STRATEGIES copy.

Design brief (moe, 12 Nov): long and short, any duration, must survive 7/15/23 bp per side plus funding, with the stop
wide relative to costs (round-trip cost under ~10% of R). Skipped as already tested or live: flags, opening-range /
session breakouts, ICT FVG / sweep, Turtle 20/10, capitulation fade, BTC-lead catch-up, squeeze breakdown, range / VWAP
reversion, 15m scalps, rotation long, rank-breadth reversal, ATR-shock snapback, pullback RSI, EMA/Supertrend trend.

CP-X1 xs_laggard_short_1d      short  coin newly enters the bottom 2 of 8 by 28-day return, below its 50d EMA, BTC weak
CP-X2 alt_btc_ratio_break_4h   both   alt/BTC price ratio breaks its 60-bar range, USD trend agrees (relative strength)
CP-X3 wide_range_cont_1d       both   wide-range day (>=1.8 ATR) closing in its outer 20%, traded in the 50d trend direction
CP-X4 break_retest_4h          both   20-bar breakout, then a retest of the broken level that holds within 12 bars
CP-X5 nr7_break_1d             both   narrowest-range day of 7, then a close beyond it in the 50d trend direction
CP-X6 anchor180_break_1d       both   first close beyond the 180-day extreme, with BTC on the same side of its 50d EMA

Stops are wide by construction (daily ATR multiples, or structure with an ATR floor). Where a candidate returns 'atr' it
is the stop distance itself (mgmt stop_atr=1.0), measured from the signal close.
"""
import numpy as np
import pandas as pd
import strategies as S

PROTECT = dict(tp1_r=1.0, tp1_frac=0.5, be_r=1.0)   # half off at +1R, rest to breakeven: the owner's giveback rule
PANEL = {}           # set by the runner: {'c': DataFrame of closes (t x symbol), 'btc': BTC indicator frame indexed by t}


def set_panel(raw):
    """raw: {symbol: candles t,o,h,l,c,v} for one timeframe. Builds the cross-sectional close panel and BTC's frame."""
    PANEL['c'] = pd.DataFrame({s: pd.Series(d.c.values, index=pd.to_datetime(d.t)) for s, d in raw.items()}).sort_index()
    b = S.indicators(raw['BTCUSDT'].drop_duplicates('t').sort_values('t').reset_index(drop=True))
    PANEL['btc'] = b.set_index(pd.to_datetime(b.t))


def _btc(d, col):
    return pd.Series(PANEL['btc'][col].reindex(pd.to_datetime(d.t)).values, index=d.index)


def _sym(d):
    c = PANEL['c']
    t = pd.to_datetime(d.t)
    hit = [s for s in c.columns if np.allclose(c[s].reindex(t).values[-50:], d.c.values[-50:], equal_nan=True)]
    return hit[0] if hit else None


def sig_xs_laggard_short(d, p, ctx):
    c = PANEL['c']
    r = c / c.shift(p['look']) - 1
    n = r.notna().sum(axis=1)
    rk = r.rank(axis=1, ascending=True)                     # 1 = worst performer
    s = _sym(d)
    t = pd.to_datetime(d.t)
    my = pd.Series(rk[s].reindex(t).values, index=d.index) if s else pd.Series(np.nan, index=d.index)
    nn = pd.Series(n.reindex(t).values, index=d.index)
    inb = (my <= p['bottom']) & (nn >= p['min_n'])
    weak = _btc(d, 'c') < _btc(d, 'e50')
    se = inb & ~inb.shift().fillna(False).astype(bool) & (d.c < d.e50) & weak
    z = pd.Series(False, index=d.index)
    return dict(le=z, se=S.X(se), lx=z, sx=z)


def sig_ratio_break(d, p, ctx):
    ratio = d.c / _btc(d, 'c')
    hi = ratio.rolling(p['n']).max().shift()
    lo = ratio.rolling(p['n']).min().shift()
    le = S.xup(ratio, hi) & (d.c > d.e200) & (d.e50 > d.e200)
    se = S.xup(-ratio, -lo) & (d.c < d.e200) & (d.e50 < d.e200)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


def sig_wide_range_cont(d, p, ctx):
    rng = d.h - d.l
    pos = (d.c - d.l) / rng.replace(0, np.nan)
    wide = rng >= p['range_atr'] * d.atr.shift()
    le = wide & (pos >= 1 - p['edge']) & (d.c > d.e50)
    se = wide & (pos <= p['edge']) & (d.c < d.e50)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


def sig_break_retest(d, p, ctx):
    o, h, l, c, a = (d[k].values for k in ('o', 'h', 'l', 'c', 'atr'))
    hh, ll, e200 = d.hh20.values, d.ll20.values, d.e200.values
    n = len(d); le = np.zeros(n, bool); se = np.zeros(n, bool); dist = np.full(n, np.nan)
    up = dn = None                                   # (level, bar of breakout)
    for i in range(1, n):
        if not np.isfinite(a[i]) or not np.isfinite(hh[i]) or not np.isfinite(e200[i]): continue
        if up and i - up[1] > p['win']: up = None
        if dn and i - dn[1] > p['win']: dn = None
        if up and i > up[1]:
            L = up[0]
            if c[i] < L - p['tol_atr'] * a[i]: up = None                # failed breakout: level lost on close
            elif l[i] <= L + p['tol_atr'] * a[i] and c[i] > L and c[i] > o[i] and c[i] > e200[i]:
                le[i] = True; dist[i] = max(c[i] - (min(l[i], L) - p['buf_atr'] * a[i]), p['min_atr'] * a[i]); up = None
        if dn and i > dn[1]:
            L = dn[0]
            if c[i] > L + p['tol_atr'] * a[i]: dn = None
            elif h[i] >= L - p['tol_atr'] * a[i] and c[i] < L and c[i] < o[i] and c[i] < e200[i]:
                se[i] = True; dist[i] = max((max(h[i], L) + p['buf_atr'] * a[i]) - c[i], p['min_atr'] * a[i]); dn = None
        if c[i] > hh[i] and c[i - 1] <= hh[i - 1]: up = (hh[i], i)
        if c[i] < ll[i] and c[i - 1] >= ll[i - 1]: dn = (ll[i], i)
    z = pd.Series(False, index=d.index)
    return dict(le=pd.Series(le, d.index), se=pd.Series(se, d.index), lx=z, sx=z, atr=dist)


def sig_nr7_break(d, p, ctx):
    rng = d.h - d.l
    nr7 = rng <= rng.rolling(7).min()
    prev = nr7.shift().fillna(False).astype(bool)
    le = prev & (d.c > d.h.shift()) & (d.c > d.e50)
    se = prev & (d.c < d.l.shift()) & (d.c < d.e50)
    dist = np.where(le, d.c - d.l.shift(), np.where(se, d.h.shift() - d.c, np.nan))
    dist = np.maximum(dist, p['min_atr'] * d.atr.values)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z, atr=dist)


def sig_anchor180(d, p, ctx):
    hi = d.h.rolling(p['n']).max().shift()
    lo = d.l.rolling(p['n']).min().shift()
    bu = _btc(d, 'c') > _btc(d, 'e50')
    le = S.xup(d.c, hi) & bu
    se = S.xup(-d.c, -lo) & ~bu
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


CANDIDATES = {
    'cp_x1_xs_laggard_short_1d': dict(
        name='[RESEARCH] CP-X1 Cross-sectional laggard short daily', style='relative_strength', fn=sig_xs_laggard_short,
        sides='short', tf='1d', research_only=True, params=dict(look=28, bottom=2, min_n=6),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=20, **PROTECT)),
    'cp_x2_alt_btc_ratio_break_4h': dict(
        name='[RESEARCH] CP-X2 Alt/BTC ratio breakout 4h', style='relative_strength', fn=sig_ratio_break, sides='both',
        tf='4h', research_only=True, params=dict(n=60), exclude=['BTCUSDT'],
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=30, **PROTECT)),
    'cp_x3_wide_range_cont_1d': dict(
        name='[RESEARCH] CP-X3 Wide-range day continuation daily', style='momentum', fn=sig_wide_range_cont, sides='both',
        tf='1d', research_only=True, params=dict(range_atr=1.8, edge=0.2),
        mgmt=dict(stop_atr=2.0, tp_r=3.0, max_bars=10, **PROTECT)),
    'cp_x4_break_retest_4h': dict(
        name='[RESEARCH] CP-X4 Breakout-retest 4h', style='breakout', fn=sig_break_retest, sides='both', tf='4h',
        research_only=True, params=dict(win=12, tol_atr=0.25, buf_atr=0.5, min_atr=1.5),
        mgmt=dict(stop_atr=1.0, tp_r=3.0, max_bars=30, **PROTECT)),
    'cp_x5_nr7_break_1d': dict(
        name='[RESEARCH] CP-X5 NR7 breakout daily', style='volatility_contraction', fn=sig_nr7_break, sides='both',
        tf='1d', research_only=True, params=dict(min_atr=1.0),
        mgmt=dict(stop_atr=1.0, tp_r=3.0, max_bars=10, **PROTECT)),
    'cp_x6_anchor180_break_1d': dict(
        name='[RESEARCH] CP-X6 180-day extreme breakout daily', style='anchoring', fn=sig_anchor180, sides='both',
        tf='1d', research_only=True, params=dict(n=180),
        mgmt=dict(stop_atr=3.0, tp_r=4.0, max_bars=30, **PROTECT)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])


# ---------------------------------------------------------------------------------------------------------------------
# Wave 3b, FROZEN 2026-11-12 after the wave-3a results were known (only CP-X2, alt/BTC relative strength, came close).
# Deliberately different families, all cross-asset; none is a retune of X1-X6. Same gate, same runner.
#
# CP-Y1 xs_leader_pullback_4h   both   top-2 (bottom-2) of 8 by 7-day return, BTC on the same side of EMA200, RSI pullback ends
#                                      (pre-run design fix: trend filter EMA200, not EMA50; the EMA50 draft fired ~2x per coin
#                                      in 5 years by construction. Only signal counts were seen, no results.)
# CP-Y2 breadth_thrust_1d       both   share of the 8 coins above their 20d EMA swings from <=25% to >=75% within 5 days
#                                      (Zweig-style thrust); each coin already above its EMA20 is bought; mirror for shorts
# CP-Y3 weekly_rel_mom_1d       both   every Monday: long if the 28-day return is > 0 and beats BTC's, short if < 0 and
#                                      trails BTC's; held at most one week
# CP-Y4 vol_feedback_short_1d   short  BTC below its 200d EMA; the coin's 7-day realised volatility first rises above
#                                      the 80th percentile of its last 90 days while it closes below EMA20
# ---------------------------------------------------------------------------------------------------------------------

def _panel_series(d, frame):
    s = _sym(d)
    t = pd.to_datetime(d.t)
    return pd.Series(frame[s].reindex(t).values, index=d.index) if s else pd.Series(np.nan, index=d.index)


def sig_xs_leader_pullback(d, p, ctx):
    c = PANEL['c']
    r = c / c.shift(p['look']) - 1
    n = r.notna().sum(axis=1)
    top = _panel_series(d, r.rank(axis=1, ascending=False))
    bot = _panel_series(d, r.rank(axis=1, ascending=True))
    ok = pd.Series(n.reindex(pd.to_datetime(d.t)).values, index=d.index) >= p['min_n']
    bull = _btc(d, 'c') > _btc(d, 'e200')
    le = ok & bull & (top <= 2) & S.xup(d.rsi14, pd.Series(p['rsi_lo'], index=d.index)) & (d.c > d.e200)
    se = ok & ~bull & (bot <= 2) & S.xup(-d.rsi14, pd.Series(-p['rsi_hi'], index=d.index)) & (d.c < d.e200)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


def sig_breadth_thrust(d, p, ctx):
    c = PANEL['c']
    above = (c > c.ewm(span=20, adjust=False, min_periods=20).mean()).where(c.notna())
    br = above.sum(axis=1) / c.notna().sum(axis=1).replace(0, np.nan)
    up = (br >= p['hi']) & (br.rolling(p['win']).min().shift() <= p['lo'])
    dn = (br <= p['lo']) & (br.rolling(p['win']).max().shift() >= p['hi'])
    up = up & ~up.shift().fillna(False).astype(bool); dn = dn & ~dn.shift().fillna(False).astype(bool)
    t = pd.to_datetime(d.t)
    u = pd.Series(up.reindex(t).fillna(False).values, index=d.index)
    w = pd.Series(dn.reindex(t).fillna(False).values, index=d.index)
    le = u & (d.c > d.e21)
    se = w & (d.c < d.e21)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


def sig_weekly_rel_mom(d, p, ctx):
    t = pd.to_datetime(d.t)
    mon = pd.Series((t.dt.dayofweek == 0).values, index=d.index)
    r = d.c / d.c.shift(p['look']) - 1
    bc = _btc(d, 'c'); br = bc / bc.shift(p['look']) - 1
    le = mon & (r > 0) & (r > br)
    se = mon & (r < 0) & (r < br)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


def sig_vol_feedback_short(d, p, ctx):
    lr = np.log(d.c).diff()
    rv = lr.rolling(7).std()
    thr = rv.rolling(90).quantile(p['q']).shift()
    hot = rv > thr
    bear = _btc(d, 'c') < _btc(d, 'e200')
    se = bear & hot & ~hot.shift().fillna(False).astype(bool) & (d.c < d.e21)
    z = pd.Series(False, index=d.index)
    return dict(le=z, se=S.X(se), lx=z, sx=z)


CANDIDATES.update({
    'cp_y1_xs_leader_pullback_4h': dict(
        name='[RESEARCH] CP-Y1 Cross-sectional leader pullback 4h', style='relative_strength', fn=sig_xs_leader_pullback,
        sides='both', tf='4h', research_only=True, params=dict(look=42, min_n=6, rsi_lo=40.0, rsi_hi=60.0),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=30, **PROTECT)),
    'cp_y2_breadth_thrust_1d': dict(
        name='[RESEARCH] CP-Y2 Breadth thrust daily', style='breadth', fn=sig_breadth_thrust, sides='both', tf='1d',
        research_only=True, params=dict(lo=0.25, hi=0.75, win=5),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=20, **PROTECT)),
    'cp_y3_weekly_rel_mom_1d': dict(
        name='[RESEARCH] CP-Y3 Weekly relative momentum daily', style='momentum', fn=sig_weekly_rel_mom, sides='both',
        tf='1d', research_only=True, params=dict(look=28), exclude=['BTCUSDT'],
        mgmt=dict(stop_atr=3.0, tp_r=5.0, max_bars=7, **PROTECT)),
    'cp_y4_vol_feedback_short_1d': dict(
        name='[RESEARCH] CP-Y4 Volatility-feedback short daily', style='volatility', fn=sig_vol_feedback_short,
        sides='short', tf='1d', research_only=True, params=dict(q=0.80),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=15, **PROTECT)),
})
