"""NEWSTRAT-CP4: hourly long/short candidates for account 4 (Claude Projects, 2026-11-12).

FROZEN before any CP4 run. Every parameter below was fixed a priori; none were tuned. A changed hypothesis is a new
candidate. All default-OFF, research_only, registered only into this process's strategies.STRATEGIES copy.

Brief (moe via Codex, 12 Nov 14:55 Cairo): genuinely different 1h L/S candidates with complementary trade frequency and
positive economics after 7/15/23 bp per executed side, slippage and funding. Not redone (already tested and failed or
selected): Codex C1H H1 EMA pullback, H2 compression breakout, H3 Keltner reclaim, H4 RSI2 reset, H5 volume impulse
(selected, not tuned here), H6 BTC-relative momentum, cross-asset U1-U3, the flow candidates; PR #334 FVG, sweep,
Asia-range breakout, flags; the 1h registry sleeves (session inventory, volume pressure, weekend, skew, jump, aggressor
flow, BTC order-flow lead, large-trade impulse, daily-range participation, illiquidity reversion, inside-bar breakout,
RSI divergence, three-bar exhaustion, OBV lead, MFI, Keltner lower, Williams %R, EMA spread, autocorrelation, volatility
acceleration, sign entropy, range overlap, state persistence, pullback RSI short, Kalman, STARC, evening star).

Lesson carried over: at 1h, gross edges are near zero and costs dominate tight stops. Every CP4 candidate therefore uses
a 3 ATR(1h) stop and holds for 24-48 hours, with fixed exits (stop / target / time, no protection mover). Codex's fixed-
exit control found protection was not helping on 1h.

CP-Z1 lowvol_impulse_fade_1h   both  a large 1h candle (body 1.5-4 ATR) on volume under 1.5x its 24h median is faded
                                     (pre-run design fix: the first draft, volume under 1.0x median, fired about 2 times
                                     per coin in 4 years. Only signal counts were seen, no results.)
CP-Z2 trend_tstat_1h           both  t-statistic of the 48-hour log-price regression slope first crosses +/-4, with the
                                     close on the same side of EMA200
CP-Z3 ichimoku_cloud_1h        both  close crosses out of the Ichimoku cloud (9/26/52), Tenkan beyond Kijun, lagging span
                                     confirms (close beyond the close 26 bars back)
CP-Z4 psar_adx_1h              both  Parabolic SAR (0.02/0.2) flips with ADX >= 25 and the matching DI leading
CP-Z5 daily_tsmom_1h           both  once a day at 00:00 UTC: long if the 7-day return and the 30-day return are both > 0,
                                     short if both < 0; held 24 hours (a daily rebalance run on hourly bars)
"""
import numpy as np
import pandas as pd
import strategies as S

FIXED = dict(be_r=0, trail_atr=0)


def _z(d):
    return pd.Series(False, index=d.index)


def sig_lowvol_fade(d, p, ctx):
    body = d.c - d.o
    big = (body.abs() >= p['lo_atr'] * d.atr.shift()) & (body.abs() <= p['hi_atr'] * d.atr.shift())
    quiet = d.v < p['vol_mult'] * d.v.rolling(24, min_periods=24).median().shift()
    le = big & quiet & (body < 0)
    se = big & quiet & (body > 0)
    return dict(le=S.X(le), se=S.X(se), lx=_z(d), sx=_z(d))


def _tstat(y, n):
    x = np.arange(n, dtype=float); x -= x.mean(); sxx = (x * x).sum()
    yv = y.values; out = np.full(len(yv), np.nan)
    for i in range(n - 1, len(yv)):
        w = yv[i - n + 1:i + 1]
        if not np.isfinite(w).all(): continue
        b = (x * (w - w.mean())).sum() / sxx
        res = w - w.mean() - b * x
        se = np.sqrt((res * res).sum() / (n - 2) / sxx)
        out[i] = b / se if se > 0 else np.nan
    return pd.Series(out, index=y.index)


def sig_trend_tstat(d, p, ctx):
    t = _tstat(np.log(d.c), p['n'])
    k = p['k']
    le = (t >= k) & (t.shift() < k) & (d.c > d.e200)
    se = (t <= -k) & (t.shift() > -k) & (d.c < d.e200)
    return dict(le=S.X(le), se=S.X(se), lx=_z(d), sx=_z(d))


def sig_ichimoku(d, p, ctx):
    mid = lambda n: (d.h.rolling(n).max() + d.l.rolling(n).min()) / 2
    ten, kij = mid(9), mid(26)
    sa = ((ten + kij) / 2).shift(26)
    sb = mid(52).shift(26)
    top, bot = np.maximum(sa, sb), np.minimum(sa, sb)
    le = (d.c > top) & (d.c.shift() <= top.shift()) & (ten > kij) & (d.c > d.c.shift(26))
    se = (d.c < bot) & (d.c.shift() >= bot.shift()) & (ten < kij) & (d.c < d.c.shift(26))
    return dict(le=S.X(le), se=S.X(se), lx=_z(d), sx=_z(d))


def _psar_dir(h, l, step, mx):
    n = len(h); dr = np.zeros(n, int)
    if n < 2: return dr
    up = True; af = step; ep = h[0]; sar = l[0]; dr[0] = 1
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar: up = False; sar = ep; ep = l[i]; af = step
            elif h[i] > ep: ep = h[i]; af = min(af + step, mx)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar: up = True; sar = ep; ep = h[i]; af = step
            elif l[i] < ep: ep = l[i]; af = min(af + step, mx)
        dr[i] = 1 if up else -1
    return dr


def sig_psar_adx(d, p, ctx):
    dr = pd.Series(_psar_dir(d.h.values, d.l.values, p['step'], p['max']), index=d.index)
    up, dn = d.h.diff(), -d.l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), d.index).ewm(alpha=1 / 14, adjust=False).mean()
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), d.index).ewm(alpha=1 / 14, adjust=False).mean()
    strong = d.adx >= p['adx']
    le = (dr == 1) & (dr.shift() == -1) & strong & (pdm > ndm)
    se = (dr == -1) & (dr.shift() == 1) & strong & (ndm > pdm)
    return dict(le=S.X(le), se=S.X(se), lx=_z(d), sx=_z(d))


def sig_daily_tsmom(d, p, ctx):
    t = pd.to_datetime(d.t)
    midnight = pd.Series((t.dt.hour == 0).values, index=d.index)
    r7 = d.c / d.c.shift(24 * 7) - 1
    r30 = d.c / d.c.shift(24 * 30) - 1
    le = midnight & (r7 > 0) & (r30 > 0)
    se = midnight & (r7 < 0) & (r30 < 0)
    return dict(le=S.X(le), se=S.X(se), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'cp_z1_lowvol_impulse_fade_1h': dict(
        name='[RESEARCH] CP-Z1 Low-volume impulse fade 1h', style='mean_reversion', fn=sig_lowvol_fade, sides='both',
        tf='1h', research_only=True, params=dict(lo_atr=1.5, hi_atr=4.0, vol_mult=1.5),
        mgmt=dict(stop_atr=3.0, tp_r=2.0, max_bars=24, **FIXED)),
    'cp_z2_trend_tstat_1h': dict(
        name='[RESEARCH] CP-Z2 Regression t-stat trend 1h', style='trend', fn=sig_trend_tstat, sides='both', tf='1h',
        research_only=True, params=dict(n=48, k=4.0),
        mgmt=dict(stop_atr=3.0, tp_r=4.0, max_bars=48, **FIXED)),
    'cp_z3_ichimoku_cloud_1h': dict(
        name='[RESEARCH] CP-Z3 Ichimoku cloud breakout 1h', style='trend', fn=sig_ichimoku, sides='both', tf='1h',
        research_only=True, params={},
        mgmt=dict(stop_atr=3.0, tp_r=4.0, max_bars=48, **FIXED)),
    'cp_z4_psar_adx_1h': dict(
        name='[RESEARCH] CP-Z4 Parabolic SAR flip with ADX 1h', style='trend', fn=sig_psar_adx, sides='both', tf='1h',
        research_only=True, params=dict(step=0.02, max=0.2, adx=25.0),
        mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=36, **FIXED)),
    'cp_z5_daily_tsmom_1h': dict(
        name='[RESEARCH] CP-Z5 Daily time-series momentum on 1h', style='momentum', fn=sig_daily_tsmom, sides='both',
        tf='1h', research_only=True, params={},
        mgmt=dict(stop_atr=3.0, tp_r=4.0, max_bars=24, **FIXED)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
