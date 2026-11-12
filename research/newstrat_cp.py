"""NEWSTRAT-CP: five genuinely new research-only candidates (Claude Projects, 2026-11-12).

FROZEN before any evaluation run. Every parameter below was fixed a priori; none were tuned. A changed hypothesis is a
new candidate. All default-OFF, research_only, registered only into this process's strategies.STRATEGIES copy.

CP-N1 fvg_continuation_1h        both   ICT fair-value-gap retest in the 1h trend direction
CP-N2 liquidity_sweep_reversal_1h both  ICT sweep of the prior 24h extreme that closes back inside, on a volume spike
CP-N3 turtle_daily               both   20-day Donchian breakout / 10-day channel exit on daily candles (first daily short
                                        besides bear_breakdown; first daily long/short pair)
CP-N4 session_orb_1h             both   Asia (00-08 UTC) range breakout during 08-16 UTC, flat by end of UTC day
CP-N5 flag_continuation_1h       both   tight flag after an impulse, broken in the impulse direction (1h shorts gap)

Stops are signal-defined: each fn returns 'atr' = stop distance / mgmt.stop_atr (stop_atr=1.0), measured from the
signal close; the backtester places the stop that distance from the next open.
"""
import numpy as np
import pandas as pd
import strategies as S

PROTECT = dict(tp1_r=1.0, tp1_frac=0.5, be_r=1.0)   # half off at +1R, rest to breakeven: the owner's giveback rule


def _vol_med(v, n):
    return v.rolling(n, min_periods=n).median().shift()


def sig_fvg_continuation(d, p, ctx):
    o, h, l, c, a = (d[k].values for k in ('o', 'h', 'l', 'c', 'atr'))
    e50, e200 = d.e50.values, d.e200.values
    n = len(d); le = np.zeros(n, bool); se = np.zeros(n, bool); dist = np.full(n, np.nan)
    zones = []                                       # [side, top, bot, born, used]
    for i in range(2, n):
        # retests of live zones (formed on earlier bars, at most p.win bars old, first touch only)
        for z in zones:
            sd, top, bot, born = z[0], z[1], z[2], z[3]
            if z[4] or i - born > p['win'] or i == born: continue
            if sd == 1 and l[i] <= top and c[i] > bot:
                z[4] = True
                if c[i] > o[i] and c[i] > e200[i] and e50[i] > e200[i]:
                    le[i] = True; dist[i] = max(c[i] - (bot - p['buf_atr'] * a[i]), p['min_atr'] * a[i])
            elif sd == 1 and c[i] <= bot:
                z[4] = True                          # gap fully filled on close: invalidated
            if sd == -1 and h[i] >= bot and c[i] < top:
                z[4] = True
                if c[i] < o[i] and c[i] < e200[i] and e50[i] < e200[i]:
                    se[i] = True; dist[i] = max((top + p['buf_atr'] * a[i]) - c[i], p['min_atr'] * a[i])
            elif sd == -1 and c[i] >= top:
                z[4] = True
        zones = [z for z in zones if not z[4] and i - z[3] <= p['win']]
        # new gaps completed at bar i (displacement candle is i-1)
        body = c[i - 1] - o[i - 1]
        if l[i] > h[i - 2] and body >= p['disp_atr'] * a[i - 1] and (l[i] - h[i - 2]) >= p['gap_atr'] * a[i]:
            zones.append([1, l[i], h[i - 2], i, False])
        if h[i] < l[i - 2] and -body >= p['disp_atr'] * a[i - 1] and (l[i - 2] - h[i]) >= p['gap_atr'] * a[i]:
            zones.append([-1, l[i - 2], h[i], i, False])
    z = pd.Series(False, index=d.index)
    return dict(le=pd.Series(le, d.index), se=pd.Series(se, d.index), lx=z, sx=z, atr=dist)


def sig_liquidity_sweep(d, p, ctx):
    k = p['look']
    H = d.h.rolling(k).max().shift(); L = d.l.rolling(k).min().shift()
    rng = (d.h - d.l).replace(0, np.nan)
    vol_ok = d.v >= p['vol_mult'] * _vol_med(d.v, k)
    up_wick = (d.h - d[['o', 'c']].max(axis=1)) / rng
    dn_wick = (d[['o', 'c']].min(axis=1) - d.l) / rng
    se = (d.h >= H + p['poke_atr'] * d.atr) & (d.c < H) & (up_wick >= p['wick']) & vol_ok & (d.rsi14.shift() >= p['rsi_hi'])
    le = (d.l <= L - p['poke_atr'] * d.atr) & (d.c > L) & (dn_wick >= p['wick']) & vol_ok & (d.rsi14.shift() <= p['rsi_lo'])
    dist = np.where(se, (d.h + p['buf_atr'] * d.atr) - d.c, np.where(le, d.c - (d.l - p['buf_atr'] * d.atr), np.nan))
    dist = np.maximum(dist, p['min_atr'] * d.atr.values)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z, atr=dist)


def sig_turtle(d, p, ctx):
    hi = d.h.rolling(p['entry']).max().shift(); lo = d.l.rolling(p['entry']).min().shift()
    xhi = d.h.rolling(p['exit']).max().shift(); xlo = d.l.rolling(p['exit']).min().shift()
    return dict(le=S.X(S.xup(d.c, hi)), se=S.X(S.xup(-d.c, -lo)), lx=S.X(d.c < xlo), sx=S.X(d.c > xhi))


def sig_session_orb(d, p, ctx):
    t = pd.to_datetime(d.t); hr = t.dt.hour.values; day = t.dt.normalize()
    asia = (hr >= 0) & (hr < p['asia_end'])
    ah = d.h.where(asia).groupby(day).transform('max'); al = d.l.where(asia).groupby(day).transform('min')
    trade_win = (hr >= p['asia_end']) & (hr < p['last_hour'])
    width = (ah - al) / d.atr
    ok = trade_win & (width >= p['w_min']) & (width <= p['w_max'])
    up = ok & (d.c > ah + p['clear_atr'] * d.atr); dn = ok & (d.c < al - p['clear_atr'] * d.atr)
    # first breakout of the day per direction only
    up = up & (up.groupby(day).cumsum() == 1); dn = dn & (dn.groupby(day).cumsum() == 1)
    mid = (ah + al) / 2
    dist = np.where(up, d.c - mid, np.where(dn, mid - d.c, np.nan))
    dist = np.maximum(dist, p['min_atr'] * d.atr.values)
    late = pd.Series(hr >= p['flat_hour'], index=d.index)              # flat before the UTC day ends
    return dict(le=S.X(up), se=S.X(dn), lx=late, sx=late, atr=dist)


def sig_flag(d, p, ctx):
    f, k = p['flag'], p['impulse']
    fh = d.h.rolling(f).max().shift(); fl = d.l.rolling(f).min().shift()
    tight = (fh - fl) <= p['tight_atr'] * d.atr.shift()
    imp = (d.c.shift(f) - d.c.shift(f + k)) / d.atr.shift(f)
    trend_dn = (d.e50 < d.e200) & (d.c < d.e200); trend_up = (d.e50 > d.e200) & (d.c > d.e200)
    se = tight & (imp <= -p['imp_atr']) & trend_dn & (d.c < fl) & (d.c < d.o)
    le = tight & (imp >= p['imp_atr']) & trend_up & (d.c > fh) & (d.c > d.o)
    dist = np.where(se, (fh + p['buf_atr'] * d.atr) - d.c, np.where(le, d.c - (fl - p['buf_atr'] * d.atr), np.nan))
    dist = np.maximum(dist, p['min_atr'] * d.atr.values)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z, atr=dist)


CANDIDATES = {
    'cp_n1_fvg_continuation_1h': dict(
        name='[RESEARCH] CP-N1 FVG continuation 1h', style='ict_continuation', fn=sig_fvg_continuation, sides='both',
        tf='1h', research_only=True,
        params=dict(disp_atr=1.0, gap_atr=0.25, win=12, buf_atr=0.25, min_atr=0.5),
        mgmt=dict(stop_atr=1.0, tp_r=2.5, max_bars=48, **PROTECT)),
    'cp_n2_liquidity_sweep_reversal_1h': dict(
        name='[RESEARCH] CP-N2 Liquidity sweep reversal 1h', style='ict_reversal', fn=sig_liquidity_sweep, sides='both',
        tf='1h', research_only=True,
        params=dict(look=24, poke_atr=0.1, wick=0.5, vol_mult=1.5, rsi_hi=60, rsi_lo=40, buf_atr=0.1, min_atr=0.5),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24, **PROTECT)),
    'cp_n3_turtle_daily': dict(
        name='[RESEARCH] CP-N3 Turtle 20/10 daily', style='trend', fn=sig_turtle, sides='both', tf='1d',
        research_only=True, params=dict(entry=20, exit=10),
        mgmt=dict(stop_atr=2.0, tp_r=50.0, max_bars=120, be_r=0, trail_atr=0)),
    'cp_n4_session_orb_1h': dict(
        name='[RESEARCH] CP-N4 Asia-range session breakout 1h', style='intraday_breakout', fn=sig_session_orb,
        sides='both', tf='1h', research_only=True,
        params=dict(asia_end=8, last_hour=16, flat_hour=23, w_min=1.5, w_max=6.0, clear_atr=0.1, min_atr=0.5),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=16, **PROTECT)),
    'cp_n5_flag_continuation_1h': dict(
        name='[RESEARCH] CP-N5 Flag continuation 1h', style='trend_continuation', fn=sig_flag, sides='both', tf='1h',
        research_only=True,
        params=dict(flag=8, impulse=12, tight_atr=2.5, imp_atr=3.0, buf_atr=0.1, min_atr=0.5),
        mgmt=dict(stop_atr=1.0, tp_r=3.0, max_bars=36, **PROTECT)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
