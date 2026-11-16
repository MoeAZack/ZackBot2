"""LONGSCOUT-V1: eight new research-only 4h / daily candidates, long side first (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules. Every parameter below was fixed a priori from the hypothesis; none
was tuned. A changed hypothesis is a new candidate. All default-OFF, research_only, registered only into this process's
strategies.STRATEGIES copy. Hourly is excluded (the 1h lane is closed by evidence).

REGIME R1 (frozen, shared by every gated rule and by the exp_trend regime check):
  on COMPLETED daily closes, sma = 100-day simple mean of the close, slope = sma - sma 20 days earlier;
  rising   = close > sma and slope > 0;   falling = close < sma and slope < 0;   sideways = anything else;
  unknown  = fewer than 120 daily closes. A day's value is usable from that day's close (the next day's 00:00 UTC).
  A daily row decides at its own close and sees its own day; a 4h row decides at its close and sees every day that
  ended at or before that close. Daily closes are built from complete 4h bins only (6 of 6), so no day is invented.

LC1 lc_dual_regime_breakout_1d   long   close crosses above the prior 20-day high while BOTH the coin and BTC are rising;
                                        exit on a close below the prior 10-day low. Changed hypothesis vs CP-N3 turtle
                                        (that one is both-sided and ungated).
LC2 lc_rs_rsi2_dip_4h            long   4h RSI(2) < 10 while the coin is rising (R1) and its 30-day return beats BTC's;
                                        exit when RSI(2) > 70. Short-horizon dip inside a relative-strength leader.
LC3 lc_washout_reclaim_1d        long   yesterday printed a new 20-day low, today closes green above yesterday's high,
                                        coin above its 200-day EMA; stop under the washout low, 2R target, 10 days.
LC4 lc_mss_higher_low_4h         long   market-structure shift: last two confirmed 4h swing lows rising (3/3 fractal),
                                        first close above the last confirmed swing high; coin and BTC not falling;
                                        stop under the higher low.
LC5 lc_volume_accum_1d           long   daily volume >= 2.5x its prior 50-day median, green day closing in the top
                                        quarter of its range, above the 50-day EMA, BTC not falling.
LC6 lc_trend_pullback_1d         long   daily EMA20 > EMA50 > EMA100 with EMA50 rising; the day dips to EMA20 and closes
                                        green above it (first such day); BTC not falling; exit on a close below EMA50.
RS1 rs_regime_gated_trend_4h     both   exp_trend's own signals, longs only while the coin is rising (R1), shorts only
                                        while it is falling. Changed hypothesis of exp_trend (new candidate).
SS1 ss_ma_rejection_short_1d     short  coin falling (R1), BTC not rising; the day rallies into EMA20 and closes red
                                        below it; stop over the day's high, 2R target, 15 days; exit on a close above EMA50.

Signal-defined stops return 'atr' = stop distance / mgmt.stop_atr (stop_atr = 1.0), measured from the signal close.
"""
import numpy as np
import pandas as pd
import strategies as S

_BTC = {}          # tf frame of BTC set by the runner hook (set_btc); used only for the BTC regime


def set_btc(frame):
    _BTC['f'] = frame.drop_duplicates('t').sort_values('t').reset_index(drop=True)


def _bar(t):
    t = pd.to_datetime(pd.Series(t)).reset_index(drop=True)
    step = t.diff().median() if len(t) > 1 else pd.Timedelta(hours=4)
    return t, (step if pd.notna(step) and step > pd.Timedelta(0) else pd.Timedelta(hours=4))


def daily_closes(t, c):
    """Completed daily closes from a 4h or 1d frame (4h: a day needs all 6 bins)."""
    t, step = _bar(t)
    s = pd.Series(np.asarray(c, float), index=pd.DatetimeIndex(t))
    if step >= pd.Timedelta(days=1):
        return s
    g = s.resample('1D')
    last, cnt = g.last(), g.count()
    return last[cnt == int(pd.Timedelta(days=1) / step)].dropna()


def regime_daily(dc):
    """R1 on a daily close series: +1 rising, -1 falling, 0 sideways, NaN unknown."""
    sma = dc.rolling(100, min_periods=100).mean()
    slope = sma - sma.shift(20)
    r = pd.Series(0.0, index=dc.index)
    r[(dc > sma) & (slope > 0)] = 1.0
    r[(dc < sma) & (slope < 0)] = -1.0
    r[np.arange(len(dc)) < 119] = np.nan
    r[sma.isna() | slope.isna()] = np.nan
    return r


def on_rows(daily_vals, t):
    """Map a per-day series onto bar rows: a row sees every day whose close (day + 1d) is <= the row's close."""
    t, step = _bar(t)
    left = pd.DataFrame({'ct': (t + step).astype('datetime64[ns]'), 'row': np.arange(len(t))})
    right = pd.DataFrame({'avail': (pd.DatetimeIndex(daily_vals.index) + pd.Timedelta(days=1)).astype('datetime64[ns]'),
                          'v': daily_vals.values}).sort_values('avail')
    out = pd.merge_asof(left, right, left_on='ct', right_on='avail', direction='backward').sort_values('row')
    return out['v'].values


def coin_regime(d):
    return on_rows(regime_daily(daily_closes(d.t, d.c)), d.t)


def btc_regime(d):
    f = _BTC.get('f')
    if f is None: raise RuntimeError('BTC frame not set (set_btc)')
    return on_rows(regime_daily(daily_closes(f.t, f.c)), d.t)


def ret_days(d, n, frame=None):
    f = d if frame is None else frame
    dc = daily_closes(f.t, f.c)
    return on_rows(dc / dc.shift(n) - 1, d.t)


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def sig_dual_regime_breakout(d, p, ctx):
    cr, br = coin_regime(d), btc_regime(d)
    le = S.xup(d.c, d.h.rolling(p['entry']).max().shift()) & (cr == 1) & (br == 1)
    lx = d.c < d.l.rolling(p['exit']).min().shift()
    return dict(le=S.X(le), se=_z(d), lx=S.X(lx), sx=_z(d))


def sig_rs_rsi2_dip(d, p, ctx):
    cr = coin_regime(d)
    rs = ret_days(d, p['rs_days']) > ret_days(d, p['rs_days'], _BTC['f'])
    le = (d.rsi2 < p['rsi_in']) & (cr == 1) & rs
    lx = d.rsi2 > p['rsi_out']
    return dict(le=_first(le), se=_z(d), lx=S.X(lx), sx=_z(d))


def sig_washout_reclaim(d, p, ctx):
    lo = d.l.rolling(p['look']).min().shift()
    wash = (d.l.shift() <= lo.shift())                       # yesterday made a new 20-day low
    le = wash & (d.c > d.h.shift()) & (d.c > d.o) & (d.c > d.e200)
    stop = np.minimum(d.l.values, d.l.shift().values) - p['buf_atr'] * d.atr.values
    dist = np.where(le, np.maximum(d.c.values - stop, p['min_atr'] * d.atr.values), np.nan)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d), atr=dist)


def sig_mss_higher_low(d, p, ctx):
    h, l, c, a = d.h.values, d.l.values, d.c.values, d.atr.values
    k, n = p['k'], len(d)
    cr, br = coin_regime(d), btc_regime(d)
    le = np.zeros(n, bool); dist = np.full(n, np.nan)
    lows, sh, sh_used = [], np.nan, True
    for i in range(2 * k, n):
        j = i - k                                            # pivot candidate, confirmed at bar i (k bars after it)
        if l[j] < l[j - k:j].min() and l[j] <= l[j + 1:i + 1].min():
            lows.append(l[j])
        if h[j] > h[j - k:j].max() and h[j] >= h[j + 1:i + 1].max():
            sh, sh_used = h[j], False
        if (not sh_used and len(lows) >= 2 and lows[-1] > lows[-2] and c[i] > sh and c[i - 1] <= sh
                and cr[i] in (0.0, 1.0) and br[i] in (0.0, 1.0)):
            dd = c[i] - (lows[-1] - p['buf_atr'] * a[i])
            if p['min_atr'] * a[i] <= dd <= p['max_atr'] * a[i]:
                le[i] = True; dist[i] = dd
            sh_used = True
        elif not sh_used and c[i] > sh:
            sh_used = True                                   # the level broke without a valid setup: wait for a new one
    return dict(le=pd.Series(le, d.index), se=_z(d), lx=_z(d), sx=_z(d), atr=dist)


def sig_volume_accum(d, p, ctx):
    br = btc_regime(d)
    med = d.v.rolling(p['vol_n'], min_periods=p['vol_n']).median().shift()
    rng = (d.h - d.l).replace(0, np.nan)
    le = (d.v >= p['vol_mult'] * med) & ((d.c - d.l) / rng >= p['close_pos']) & (d.c > d.o) & (d.c > d.e50) & (br >= 0)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_trend_pullback(d, p, ctx):
    br = btc_regime(d)
    stack = (d.e20 > d.e50) & (d.e50 > d.e100) & (d.e50 > d.e50.shift(p['slope']))
    le = stack & (d.l <= d.e20) & (d.c > d.e20) & (d.c > d.o) & (br >= 0)
    return dict(le=_first(le), se=_z(d), lx=S.X(d.c < d.e50), sx=_z(d))


def sig_regime_gated_trend(d, p, ctx):
    cr = coin_regime(d)
    s = S.sig_exp_trend(d, p, ctx)
    return dict(s, le=S.X(s['le']) & (cr == 1), se=S.X(s['se']) & (cr == -1))


def sig_ma_rejection_short(d, p, ctx):
    cr, br = coin_regime(d), btc_regime(d)
    se = (cr == -1) & (br <= 0) & (d.h >= d.e20) & (d.c < d.e20) & (d.c < d.o)
    dist = np.where(se, np.clip((d.h.values + p['buf_atr'] * d.atr.values) - d.c.values,
                                p['min_atr'] * d.atr.values, p['max_atr'] * d.atr.values), np.nan)
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=S.X(d.c > d.e50), atr=dist)


CANDIDATES = {
    'lc_dual_regime_breakout_1d': dict(
        name='[RESEARCH] LC1 Dual-regime 20-day breakout long daily', style='trend', fn=sig_dual_regime_breakout,
        sides='long', tf='1d', research_only=True, params=dict(entry=20, exit=10),
        mgmt=dict(stop_atr=2.0, max_bars=60)),
    'lc_rs_rsi2_dip_4h': dict(
        name='[RESEARCH] LC2 Relative-strength RSI2 dip long 4h', style='pullback', fn=sig_rs_rsi2_dip,
        sides='long', tf='4h', research_only=True, params=dict(rsi_in=10.0, rsi_out=70.0, rs_days=30),
        mgmt=dict(stop_atr=2.5, max_bars=18)),
    'lc_washout_reclaim_1d': dict(
        name='[RESEARCH] LC3 Washout reclaim long daily', style='reversal', fn=sig_washout_reclaim,
        sides='long', tf='1d', research_only=True, params=dict(look=20, buf_atr=0.25, min_atr=1.0),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=10)),
    'lc_mss_higher_low_4h': dict(
        name='[RESEARCH] LC4 Higher-low structure shift long 4h', style='structure', fn=sig_mss_higher_low,
        sides='long', tf='4h', research_only=True, params=dict(k=3, buf_atr=0.25, min_atr=1.0, max_atr=4.0),
        mgmt=dict(stop_atr=1.0, tp_r=2.5, max_bars=60)),
    'lc_volume_accum_1d': dict(
        name='[RESEARCH] LC5 Volume accumulation day long daily', style='momentum', fn=sig_volume_accum,
        sides='long', tf='1d', research_only=True, params=dict(vol_n=50, vol_mult=2.5, close_pos=0.75),
        mgmt=dict(stop_atr=2.0, tp_r=3.0, max_bars=15)),
    'lc_trend_pullback_1d': dict(
        name='[RESEARCH] LC6 EMA-stack pullback long daily', style='pullback', fn=sig_trend_pullback,
        sides='long', tf='1d', research_only=True, params=dict(slope=5),
        mgmt=dict(stop_atr=2.0, tp_r=3.0, max_bars=30)),
    'rs_regime_gated_trend_4h': dict(
        name='[RESEARCH] RS1 Regime-gated Trend L/S 4h', style='trend', fn=sig_regime_gated_trend,
        sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=180)),
    'ss_ma_rejection_short_1d': dict(
        name='[RESEARCH] SS1 EMA20 rejection short daily', style='trend', fn=sig_ma_rejection_short,
        sides='short', tf='1d', research_only=True, params=dict(buf_atr=0.25, min_atr=1.0, max_atr=3.0),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=15)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
