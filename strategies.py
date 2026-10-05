"""ZackBot strategy library — shared by the backtester and the live engine.

Every strategy = a signal function + a trade-management recipe.
Signal functions return boolean arrays on CLOSED candles:
    le / se : open long / short on the next candle
    lx / sx : close long / short at this candle's close (signal exit)
Management keys (all optional):
    stop_atr      initial stop distance in ATR(14)
    trail_atr     chandelier trailing stop: highest-high - k*ATR (long) once in profit
    be_r          move stop to breakeven after price moved this many R
    tp1_r, tp1_frac   take partial profit (fraction of position) at tp1_r R
    tp_r          take full profit at this many R
    max_bars      time exit
    pyramid       dict(n=adds, step_r=R between adds, frac=size of each add vs first unit)
    dca           dict(n=safety orders, step_atr, scale, tp_atr, stop_atr) — averaging down, basket TP/stop
"""
import numpy as np
import pandas as pd


# ------------------------------------------------------------------ indicators
def ema(x, n):
    return x.ewm(span=n, adjust=False).mean()


def rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def supertrend_dir(h, l, c, n=10, mult=3.0):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    hl2 = (h + l) / 2
    ub, lb, cv = (hl2 + mult * a).values, (hl2 - mult * a).values, c.values
    fu, fl, d = ub.copy(), lb.copy(), np.ones(len(c))
    for i in range(1, len(c)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or cv[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or cv[i - 1] < fl[i - 1]) else fl[i - 1]
        d[i] = 1 if cv[i] > fu[i - 1] else (-1 if cv[i] < fl[i - 1] else d[i - 1])
    return pd.Series(d, index=c.index)


def indicators(df):
    """Adds every column any strategy needs. df: t,o,h,l,c,v (closed candles)."""
    if 'atr' in df: return df
    df = df.copy()
    c, h, l, v = df.c, df.h, df.l, df.v
    for n in (5, 9, 20, 21, 50, 55, 100, 200):
        df[f'e{n}'] = ema(c, n)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df['atr'] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    for n in (10, 20, 55, 100):
        df[f'hh{n}'] = h.rolling(n).max().shift()
        df[f'll{n}'] = l.rolling(n).min().shift()
    df['st'] = supertrend_dir(h, l, c)
    df['rsi14'] = rsi(c, 14); df['rsi2'] = rsi(c, 2)
    m, s = c.rolling(20).mean(), c.rolling(20).std()
    df['bbm'], df['bbu'], df['bbl'] = m, m + 2 * s, m - 2 * s
    df['bbw_pct'] = (4 * s / m).rolling(120).rank(pct=True)
    df['ret42'] = c / c.shift(42) - 1
    df['ret180'] = c / c.shift(180) - 1
    df['vol_ratio'] = v / v.rolling(42).mean()
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), df.index).ewm(alpha=1 / 14, adjust=False).mean()
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), df.index).ewm(alpha=1 / 14, adjust=False).mean()
    pdi, ndi = 100 * pdm / df.atr, 100 * ndm / df.atr
    df['adx'] = (100 * (pdi - ndi).abs() / (pdi + ndi)).ewm(alpha=1 / 14, adjust=False).mean()
    return df


def X(s):
    return s.fillna(False).astype(bool)


def xup(a, b):
    return (a > b) & (a.shift() <= b.shift())


NEVER = None   # placeholder meaning "no signal"


# ------------------------------------------------------------------ signal functions
# ctx: dict(btc=<btc indicator df aligned>, rank=<cross-sectional momentum rank Series for this symbol>)
def sig_ema_st(d, p, ctx):
    trend_l, trend_s = d.st > 0, d.st < 0
    return dict(le=xup(d.e20, d.e50) & (d.c > d.e200) & trend_l, se=xup(d.e50, d.e20) & (d.c < d.e200) & trend_s,
                lx=(d.e20 < d.e50) | ~trend_l, sx=(d.e20 > d.e50) | ~trend_s)


def sig_ema_mom(d, p, ctx):
    ml, ms = (d.ret42 > 0) & (d.ret180 > 0), (d.ret42 < 0) & (d.ret180 < 0)
    return dict(le=xup(d.e20, d.e50) & (d.c > d.e200) & ml, se=xup(d.e50, d.e20) & (d.c < d.e200) & ms,
                lx=(d.e20 < d.e50) | ~ml, sx=(d.e20 > d.e50) | ~ms)


def sig_donchian_ens(d, p, ctx):
    """Ensemble of 20/55/100 breakouts (Concretum-style): enter when 2 of 3 channels agree."""
    ups = (d.c > d.hh20).astype(int) + (d.c > d.hh55).astype(int) + (d.c > d.hh100).astype(int)
    dns = (d.c < d.ll20).astype(int) + (d.c < d.ll55).astype(int) + (d.c < d.ll100).astype(int)
    return dict(le=(ups >= 2) & (ups.shift() < 2), se=(dns >= 2) & (dns.shift() < 2),
                lx=d.c < d.ll20, sx=d.c > d.hh20)


def sig_breakout_pyramid(d, p, ctx):
    return dict(le=(d.c > d.hh20) & (d.e50 > d.e200), se=(d.c < d.ll20) & (d.e50 < d.e200),
                lx=d.c < -1, sx=d.c < -1)


def sig_squeeze(d, p, ctx):
    sq = d.bbw_pct.shift() < 0.2
    return dict(le=sq & (d.c > d.bbu) & (d.c > d.e200), se=sq & (d.c < d.bbl) & (d.c < d.e200),
                lx=d.c < -1, sx=d.c < -1)


def sig_trend_pullback_rsi(d, p, ctx):
    """High win-rate style: buy the dip in an uptrend when RSI turns back up from oversold."""
    up, dn = (d.e50 > d.e200) & (d.c > d.e200), (d.e50 < d.e200) & (d.c < d.e200)
    return dict(le=up & xup(d.rsi14, d.rsi14 * 0 + 40), se=dn & xup(d.rsi14 * 0 + 60, d.rsi14),
                lx=d.c < -1, sx=d.c < -1)


def sig_dca_dip(d, p, ctx):
    """Bot-style DCA: buy a stretched dip below EMA20 in an uptrend, average down with safety orders."""
    up = (d.e50 > d.e200)
    stretch = (d.e20 - d.c) / d.atr
    return dict(le=up & (stretch > 1.5) & (stretch.shift() <= 1.5), se=d.c < -1, lx=d.c < -1, sx=d.c < -1)


def sig_bear_breakdown(d, p, ctx):
    """Short-only: BTC in a downtrend and the coin breaks its 20-candle low."""
    btc_down = ctx['btc_down']
    return dict(le=d.c < -1, se=btc_down & (d.c < d.ll20) & (d.e20 < d.e50),
                lx=d.c < -1, sx=(d.c > d.hh10) | ~btc_down)


def sig_rotation(d, p, ctx):
    """Momentum rotation: hold coins ranked in the top K by 30-day risk-adjusted return (long),
    optionally short the bottom K. Re-checked every candle; entries only on rank changes."""
    k = p.get('k', 5)
    r = ctx['rank']               # 1 = strongest
    nb = ctx['n_symbols']
    in_top = (r <= k) & (d.c > d.e50)
    in_bot = (r > nb - k) & (d.c < d.e50) & ctx['btc_down']
    return dict(le=in_top & ~in_top.shift(fill_value=False), se=(in_bot & ~in_bot.shift(fill_value=False)) if p.get('shorts') else d.c < -1,
                lx=(r > k + 3) | (d.c < d.e50), sx=(r <= nb - k - 3) | (d.c > d.e50))


def sig_hot_coin(d, p, ctx):
    """Aggressive momentum chase like top lead traders: coin in the top-3 7-day movers, volume surge, breakout."""
    hot = ctx['rank7'] <= 3
    return dict(le=hot & (d.vol_ratio > 1.5) & (d.c > d.hh20) & (d.c > d.e50), se=d.c < -1,
                lx=d.c < d.e20, sx=d.c < -1)


# ------------------------------------------------------------------ registry
# style: calm / balanced / aggressive  ·  sides: long / short / both
STRATEGIES = {
    'ema_st': dict(name='EMA cross + Supertrend', style='balanced', fn=sig_ema_st, sides='long',
                   desc='EMA20 crosses EMA50 above EMA200 with Supertrend(10,3) confirming. Was slot A of the first bot.',
                   mgmt=dict(stop_atr=2.5)),
    'ema_mom': dict(name='EMA cross + Momentum', style='calm', fn=sig_ema_mom, sides='long',
                    desc='Same cross, only when 7d and 30d returns are positive. Was slot B of the first bot.',
                    mgmt=dict(stop_atr=2.5)),
    'donchian_ens': dict(name='Donchian ensemble (20/55/100)', style='calm', fn=sig_donchian_ens, sides='long',
                         desc='Research-backed trend model: enters when 2 of 3 breakout channels agree, exits on 20-candle low.',
                         mgmt=dict(stop_atr=3.0, trail_atr=4.0)),
    'breakout_pyramid': dict(name='Breakout + pyramiding', style='aggressive', fn=sig_breakout_pyramid, sides='long',
                             desc='20-candle breakout in an uptrend, adds 2 more units every +1R, chandelier trail 3 ATR.',
                             mgmt=dict(stop_atr=2.0, trail_atr=3.0, pyramid=dict(n=2, step_r=1.0, frac=0.5))),
    'squeeze_tp': dict(name='Squeeze breakout + partial TP', style='balanced', fn=sig_squeeze, sides='long',
                       desc='Bollinger squeeze breakout; takes 50% at 1.5R, moves stop to breakeven, trails the rest.',
                       mgmt=dict(stop_atr=2.0, tp1_r=1.5, tp1_frac=0.5, be_r=1.5, trail_atr=3.0)),
    'pullback_rsi': dict(name='Trend pullback (RSI) + scale-out', style='balanced', fn=sig_trend_pullback_rsi, sides='long',
                         desc='Buys RSI dips in an uptrend like many lead traders; 50% off at 1R, breakeven, rest at 3R.',
                         mgmt=dict(stop_atr=2.0, tp1_r=1.0, tp1_frac=0.5, be_r=1.0, tp_r=3.0, max_bars=60)),
    'dca_dip': dict(name='DCA dip buyer (safety orders)', style='aggressive', fn=sig_dca_dip, sides='long',
                    desc='3Commas-style: buys a stretched dip, up to 3 safety orders (1.5x each, 1 ATR apart), basket TP +1 ATR, hard basket stop.',
                    mgmt=dict(dca=dict(n=3, step_atr=1.0, scale=1.5, tp_atr=1.0, stop_atr=2.0), max_bars=60)),
    'bear_breakdown': dict(name='Bear-market breakdown (shorts)', style='aggressive', fn=sig_bear_breakdown, sides='short',
                           desc='Shorts 20-candle breakdowns only while BTC is below its EMA200; exits on 10-candle high.',
                           mgmt=dict(stop_atr=2.0, trail_atr=3.0)),
    'rotation': dict(name='Momentum rotation (top 5)', style='balanced', fn=sig_rotation, sides='long',
                     desc='Holds the 5 strongest coins by 30-day risk-adjusted momentum above their EMA50; rotates as ranks change.',
                     mgmt=dict(stop_atr=3.0), params=dict(k=5)),
    'hot_coin': dict(name='Hot-coin momentum chase', style='aggressive', fn=sig_hot_coin, sides='long',
                     desc='Lead-trader style: top-3 7-day movers breaking out on a volume surge; tight trail.',
                     mgmt=dict(stop_atr=2.0, trail_atr=2.5, tp1_r=2.0, tp1_frac=0.5)),
}


def build_context(dfs, btc_key='BTCUSDT'):
    """Cross-sectional data for rotation / regime strategies, aligned by candle time (coins may have different history)."""
    btc = dfs[btc_key].set_index('t')
    btc_down = (btc.c < btc.e200)
    ra = pd.DataFrame({s: pd.Series((d.ret180 / (d.c.pct_change().rolling(180).std() * np.sqrt(180))).values, index=d.t)
                       for s, d in dfs.items()})
    rank = ra.rank(axis=1, ascending=False)
    r7 = pd.DataFrame({s: pd.Series(d.ret42.values, index=d.t) for s, d in dfs.items()}).rank(axis=1, ascending=False)
    out = {}
    for s, d in dfs.items():
        t = d.t
        out[s] = dict(btc_down=btc_down.reindex(t).fillna(False).astype(bool).values,
                      rank=pd.Series(rank[s].reindex(t).values, index=d.index).fillna(999),
                      rank7=pd.Series(r7[s].reindex(t).values, index=d.index).fillna(999),
                      n_symbols=len(dfs))
    return out


SUB_DEFAULTS = dict(pyramid=dict(n=1, step_r=1.5, frac=0.5),
                    dca=dict(n=3, step_atr=1.0, scale=1.5, tp_atr=1.0, stop_atr=2.0),
                    ttp=dict(at_r=2.0, dev_pct=3.0), runner={})


def merge_mgmt(key, override=None):
    """Effective management settings of a slot: strategy defaults + the slot's overrides, merged one level DEEP for the
    nested blocks (pyramid / dca / ttp / runner). A partial override such as {'dca': {'n': 4}} keeps every other dca
    value from the strategy default (or the generic default), so the engine never meets a half-filled block."""
    base = STRATEGIES[key]['mgmt'] if key in STRATEGIES else {}
    out = dict(base)
    for k, v in (override or {}).items():
        if k in SUB_DEFAULTS and isinstance(v, dict):
            out[k] = {**SUB_DEFAULTS[k], **(base.get(k) or {}), **v}
        else:
            out[k] = v
    return out


def norm_tps(tps):
    """Take-profit ladder: up to 8 [r, frac] pairs, r > 0, 0 < frac <= 1, sorted by r. Bad entries are dropped."""
    out = []
    for x in (tps or [])[:8]:
        try:
            r_, f_ = float(x[0]), float(x[1])
        except Exception:
            continue
        if r_ > 0 and 0 < f_ <= 1: out.append((r_, f_))
    return sorted(out)


def regime(d, ema_days=200, adx_max=20.0, bbw_max=0.35, min_days=30):
    """Market regime of BTC for every candle of d (t,o,h,l,c,v; any timeframe up to 4h), shared by the live engine and
    the backtester. Uses only data that has CLOSED at each candle's close (no lookahead):
      bull  = the last completed daily close is above its 200-day EMA (daily candles resampled from d)
      bear  = below it
      range = 4h ADX < adx_max and 4h Bollinger-width percentile < bbw_max (4h resampled from d when d is finer)
    bull/bear are both False until min_days daily closes exist. Returns DataFrame(bull, bear, range) aligned to d's rows."""
    t = pd.to_datetime(d['t']).reset_index(drop=True)
    n = len(t)
    if n == 0: return pd.DataFrame(dict(bull=[], bear=[], range=[]), dtype=bool)
    step = t.diff().median() if n > 1 else pd.Timedelta(hours=4)
    step = step if pd.notna(step) and step > pd.Timedelta(0) else pd.Timedelta(hours=4)
    left = pd.DataFrame({'ct': t + step, 'row': np.arange(n)})
    s = pd.Series(np.asarray(d['c'], float), index=t)
    daily = s.resample('1D').last().dropna()
    e = ema(daily, ema_days)
    ok = np.arange(1, len(daily) + 1) >= min_days
    dd = pd.DataFrame({'avail': daily.index + pd.Timedelta(days=1), 'bull': (daily.values > e.values) & ok,
                       'bear': (daily.values < e.values) & ok})
    raw = pd.DataFrame({'t': t, 'o': np.asarray(d['o'], float), 'h': np.asarray(d['h'], float), 'l': np.asarray(d['l'], float),
                        'c': np.asarray(d['c'], float), 'v': np.asarray(d['v'], float) if 'v' in d else 0.0})
    if step < pd.Timedelta(hours=4):
        h4 = raw.set_index('t').resample('4h').agg(dict(o='first', h='max', l='min', c='last', v='sum')).dropna().reset_index()
        h4 = indicators(h4)
    elif 'adx' in d and 'bbw_pct' in d:
        h4 = pd.DataFrame({'t': t, 'adx': np.asarray(d['adx'], float), 'bbw_pct': np.asarray(d['bbw_pct'], float)})
    else:
        h4 = indicators(raw)
    rr = pd.DataFrame({'avail': pd.to_datetime(h4['t']).values + (step if step >= pd.Timedelta(hours=4) else pd.Timedelta(hours=4)),
                       'range': ((h4['adx'] < adx_max) & (h4['bbw_pct'] < bbw_max)).values})
    left['ct'] = pd.to_datetime(left['ct']).astype('datetime64[ns]')
    dd['avail'] = pd.to_datetime(dd['avail']).astype('datetime64[ns]'); rr['avail'] = pd.to_datetime(rr['avail']).astype('datetime64[ns]')
    out = pd.merge_asof(left, dd.sort_values('avail'), left_on='ct', right_on='avail', direction='backward')
    out = pd.merge_asof(out.drop(columns='avail'), rr.sort_values('avail'), left_on='ct', right_on='avail', direction='backward')
    out = out.sort_values('row')
    return pd.DataFrame({k: out[k].fillna(False).astype(bool).values for k in ('bull', 'bear', 'range')})


def regime_label(r):
    """Text for a missed-signal reason: 'bull' / 'bear' / 'range' / 'unknown' (r: a row/dict of regime())."""
    if r.get('range'): return 'range'
    return 'bull' if r.get('bull') else ('bear' if r.get('bear') else 'unknown')


def signals(key, d, ctx, params=None, mask_sides=True):
    st = STRATEGIES[key]
    p = dict(st.get('params', {}), **(params or {}))
    ctx = dict(ctx); ctx['btc_down'] = pd.Series(ctx['btc_down'], index=d.index)
    s = st['fn'](d, p, ctx)
    out = {k: np.array(X(s[k]).values, dtype=bool, copy=True) for k in ('le', 'se', 'lx', 'sx')}
    if mask_sides and st['sides'] == 'long': out['se'][:] = False
    if mask_sides and st['sides'] == 'short': out['le'][:] = False
    return out
