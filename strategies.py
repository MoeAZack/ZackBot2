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
from decimal import Decimal
from fractions import Fraction

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



# ------------------------------------------------------------------ sprint families (EXPERIMENTAL / TESTNET-ONLY, frozen v0)
# Four deterministic long+short entry families for the 48h testnet sprint. Each is one simple frozen rule on CLOSED
# candles, symmetric by construction (a price reflection maps every long decision onto the short one), and never raises
# a long and a short entry on the same candle. An opposite entry always implies the held side's exit signal, so a
# reversal closes first and the engine's one-lot-per-coin-per-slot rule does the rest. No tuning; no profitability claim.
EXP_TAG = 'EXPERIMENTAL / testnet-only'


def _one_side(s):
    """Never both entries on one candle (an ambiguous candle takes neither)."""
    le, se = X(s['le']), X(s['se'])
    return dict(s, le=le & ~se, se=se & ~le)


def sig_exp_trend(d, p, ctx):
    """Trend v0 = trend_ema_mom.v1 (EMA20/50 cross on the EMA200 side, 7d+30d momentum agreeing) and its exact mirror."""
    return _one_side(sig_ema_mom(d, p, ctx))


def sig_exp_range(d, p, ctx):
    """Range / mean reversion v0 = RANGE-EXIT-01 v0 on closed candles (frozen; 4h intended).
    VWAP42 = sum(typical price * volume) / sum(volume) over 42 candles (the engine's candles carry no quote volume, so
    typical price * volume stands in for it), ATR = simple 14-candle mean of the true range, z = (close - VWAP) / ATR, k = 2.
    Range gate: |VWAP_i - VWAP_(i-6)| <= 1.0 ATR. Long when z crosses back up through -k below VWAP inside the gate; short
    is the exact mirror. Exit at the mean (close back at VWAP) or when the range gate breaks; stop / time cap in mgmt."""
    k = float(p.get('k', 2.0))
    h, l, c, v = d.h, d.l, d.c, d.v
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    vw = (((h + l + c) / 3) * v).rolling(42).sum() / v.rolling(42).sum()
    z = (c - vw) / atr
    gate = (vw - vw.shift(6)).abs() <= 1.0 * atr
    le = gate & (z.shift() <= -k) & (z > -k) & (c < vw)
    se = gate & (z.shift() >= k) & (z < k) & (c > vw)
    ok = vw.notna() & atr.notna()
    return _one_side(dict(le=le, se=se, lx=ok & ((c >= vw) | ~gate), sx=ok & ((c <= vw) | ~gate)))


def sig_exp_breakout(d, p, ctx):
    """Breakout v0 = symmetric 20-candle Donchian (the donchian_ens / bear_breakdown channel): long on the first close above
    the prior 20-candle high, short on the first close below the prior 20-candle low; exit on a close through the opposite
    10-candle channel (reversal / failed breakout)."""
    up, dn = X(d.c > d.hh20), X(d.c < d.ll20)
    return _one_side(dict(le=up & ~up.shift(fill_value=False), se=dn & ~dn.shift(fill_value=False),
                          lx=d.c < d.ll10, sx=d.c > d.hh10))


def sig_exp_scalp(d, p, ctx):
    """Scalp v0 (15m intended) = EMA-pullback continuation: in an EMA20 > EMA50 trend, the candle dips to EMA20 (low <= EMA20)
    and closes back above it after a close above it -> long; exact mirror for shorts. Exit when the EMA20/50 trend flips."""
    up, dn = d.e20 > d.e50, d.e20 < d.e50
    le = up & (d.l <= d.e20) & (d.c > d.e20) & (d.c.shift() > d.e20.shift())
    se = dn & (d.h >= d.e20) & (d.c < d.e20) & (d.c.shift() < d.e20.shift())
    return _one_side(dict(le=le, se=se, lx=dn, sx=up))


def sig_exp_structure(d, p, ctx):
    """Session-agnostic structure break v0 (15m).

    A swing is usable only after the candle to its right has closed. Longs require
    the close to cross the latest confirmed swing high while EMA50 is above
    EMA200; shorts are the exact mirror. This keeps the first version causal and
    compatible with the engine's existing single-timeframe contract. A later
    research revision may replace the EMA regime proxy with native 1h context.
    """
    ph = d.h.shift(1).where((d.h.shift(1) > d.h.shift(2)) & (d.h.shift(1) >= d.h))
    pl = d.l.shift(1).where((d.l.shift(1) < d.l.shift(2)) & (d.l.shift(1) <= d.l))
    last_hi, last_lo = ph.ffill(), pl.ffill()
    up, dn = d.e50 > d.e200, d.e50 < d.e200
    broke_hi = (d.c > last_hi) & (d.c.shift() <= last_hi.shift())
    broke_lo = (d.c < last_lo) & (d.c.shift() >= last_lo.shift())
    return _one_side(dict(le=up & broke_hi, se=dn & broke_lo,
                          lx=(d.c < last_lo) | dn, sx=(d.c > last_hi) | up))


# ------------------------------------------------------------------ BREADTH-01 B3: short-only mirrors (EXPERIMENTAL / testnet-only)
# Codex ruling 6186050896 pt 4. Each is the exact price mirror of an existing long-only strategy (same thresholds, same
# mgmt, no new parameters, no tuning). Short side only; the long columns never fire.
def sig_exp_mom_short(d, p, ctx):
    """Short mirror of hot_coin: a 'cold coin' (bottom-3 7-day mover = mirror of top-3) dumping on a volume surge
    (vol_ratio > 1.5) through its prior 20-candle low and below EMA50. Exit on a close back above EMA20 (mirror of
    hot_coin's close below EMA20). Unranked coins (rank7 = 999 fill) are never cold. 'Bottom 3' is among the symbols
    ranked at that candle (rank7_n), not the full universe, so late listings / gaps don't hide the true bottom-3."""
    r7 = ctx['rank7']
    nb = ctx['rank7_n'] if 'rank7_n' in ctx else ctx['n_symbols']
    cold = (r7 > nb - 3) & (r7 <= nb)
    never = d.c < -1
    return _one_side(dict(le=never, se=cold & (d.vol_ratio > 1.5) & (d.c < d.ll20) & (d.c < d.e50),
                          lx=never, sx=d.c > d.e20))


def sig_exp_rip_sell_dca(d, p, ctx):
    """Short mirror of dca_dip: in a downtrend (EMA50 < EMA200) sell a stretched rip above EMA20 (first close more than
    1.5 ATR above EMA20); the basket adds safety orders UP and takes profit below (mgmt dca, side-aware)."""
    dn = (d.e50 < d.e200)
    stretch = (d.c - d.e20) / d.atr
    never = d.c < -1
    return _one_side(dict(le=never, se=dn & (stretch > 1.5) & (stretch.shift() <= 1.5), lx=never, sx=never))


# ------------------------------------------------------------------ VWAPREV: REX-01 runtime adapter (EXPERIMENTAL / PAPER)
# The sixth PAPER family: `range_vwap_rev.v1` X1 (frozen contract RANGE-EXIT-01, tools/research/range_vwap_rev.py) on
# closed 4h candles. The research decision logic (`indicators` / `entry_x1` / `mean_close` / `invalidated`) is mirrored
# here in the SAME exact decimal-fraction arithmetic, so the frozen equality boundaries (z == -k, drift == 1 ATR) decide
# identically; tests/test_vwaprev_runtime.py proves bar-for-bar parity against the research module. The runtime does not
# import tools/research (not shipped with the app). Fail closed: no quote volume, a hole / non-4h step in the 48-bar
# decision window, zero volume or ATR <= 0 -> no entry and no indicator exit (stop + time cap still apply).
VWAPREV = dict(n=42, k=2.0, rg=1.0, rg_lag=6, stop_atr=1.5, cap=30, tf_ms=4 * 3_600_000)


def _fr(x):
    """The research module's `fr`: the exact decimal value of the float's repr."""
    return Fraction(Decimal(repr(float(x))))


def vwaprev_eval(d, n=42, k=2.0, rg=1.0, rg_lag=6, tf_ms=4 * 3_600_000):
    """Per closed candle i of d (t,o,h,l,c,v,qv): dict of numpy arrays le / se (X1 entries), lx / sx (mean close or range
    invalidation), atr (TR-SMA14 as float, NaN when undefined), z (float, NaN when undefined). A decision reads exactly
    n + rg_lag contiguous candles i-n-rg_lag+1..i (research `Cfg.lookback`), keyed on a fixed tf_ms step."""
    m = len(d)
    z0 = lambda: np.zeros(m, bool)
    out = dict(le=z0(), se=z0(), lx=z0(), sx=z0(), atr=np.full(m, np.nan), z=np.full(m, np.nan))
    if m == 0 or 'qv' not in d:
        return out
    look = n + rg_lag
    t = pd.to_datetime(d['t']).astype('datetime64[ns]').astype('int64').to_numpy() // 1_000_000
    cols = [np.asarray(d[c], float) for c in ('h', 'l', 'c', 'v', 'qv')]
    bad = ~np.isfinite(np.vstack(cols)).all(axis=0)
    brk = np.r_[False, np.diff(t) != tf_ms]                       # candle j does not follow j-1 by exactly one step
    nbad, nbrk = np.r_[0, np.cumsum(bad)], np.r_[0, np.cumsum(brk)]
    h, l, c, v, qv = ([None if b else _fr(x) for x, b in zip(col, bad)] for col in cols)
    pv, pq = [Fraction(0)], [Fraction(0)]
    for j in range(m):
        pv.append(pv[-1] + (v[j] or 0)); pq.append(pq[-1] + (qv[j] or 0))
    ptr = [Fraction(0), Fraction(0)]                               # TR of candle 0 is never used
    for j in range(1, m):
        ok = h[j] is not None and c[j - 1] is not None
        tr = max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1])) if ok else Fraction(0)
        ptr.append(ptr[-1] + tr)

    def vwap(i):                                                   # candles i-n+1..i
        s = pv[i + 1] - pv[i + 1 - n]
        return None if s == 0 else (pq[i + 1] - pq[i + 1 - n]) / s

    def atr(i):                                                    # TR-SMA14 of candles i-14..i
        a = (ptr[i + 1] - ptr[i - 13]) / 14
        return a if a > 0 else None

    kk, rgk = _fr(k), _fr(rg)
    for i in range(look - 1, m):
        lo = i - look + 1
        if nbad[i + 1] - nbad[lo] or nbrk[i + 1] - nbrk[lo + 1]:
            continue                                               # hole / bad value / wrong step in the window
        vw, vw_p, vw_l = vwap(i), vwap(i - 1), vwap(i - rg_lag)
        a, a_p = atr(i), atr(i - 1)
        zz = None if vw is None or a is None else (c[i] - vw) / a
        zp = None if vw_p is None or a_p is None else (c[i - 1] - vw_p) / a_p
        rgv = None if vw is None or vw_l is None or a is None else abs(vw - vw_l) <= rgk * a
        if a is not None:
            out['atr'][i] = float(a)
        if zz is not None:
            out['z'][i] = float(zz)
            out['lx'][i] = c[i] >= vw                              # research mean_close (long)
            out['sx'][i] = c[i] <= vw
        if rgv is False:                                           # research invalidated
            out['lx'][i] = out['sx'][i] = True
        if zz is not None and zp is not None and rgv is True:      # research entry_x1
            out['le'][i] = zp <= -kk and zz > -kk and c[i] < vw
            out['se'][i] = zp >= kk and zz < kk and c[i] > vw
    return out


def sig_exp_vwaprev(d, p, ctx):
    """VWAPREV = range_vwap_rev.v1 X1 on closed 4h candles: VWAP42 = sum quote volume / sum volume, ATR = TR-SMA14,
    z = (close - VWAP) / ATR, RG = |VWAP_i - VWAP_(i-6)| <= 1 ATR. Long when z crosses back above -2 below VWAP inside
    RG; short the exact mirror. Exit at the mean (close at / through VWAP) or on range invalidation; stop 1.5 x the
    decision candle's TR-SMA14 (returned as `atr`, used by the engine for the stop), 30-candle cap."""
    r = vwaprev_eval(d, n=int(p.get('n', 42)), k=float(p.get('k', 2.0)), rg=float(p.get('rg', 1.0)))
    idx = d.index
    s = _one_side({k_: pd.Series(r[k_], index=idx) for k_ in ('le', 'se', 'lx', 'sx')})
    s['atr'] = r['atr']
    return s



# ------------------------------------------------------------------ SCALP-4: PAPER scalp package (EXPERIMENTAL / PAPER-only)
# Four simple 15m scalps (the engine's shortest timeframe: engine.TF_SEC has 15m / 1h / 4h; there is no 5m candle path).
# Each is a frozen rule on CLOSED candles, symmetric by construction (the short rule is the price mirror of the long rule),
# wrapped in _one_side (an ambiguous candle takes neither side). Constants are documented defaults, NOT optimised; no edge
# is claimed. Reference ranges / averages exclude the decision candle (shift), so a decision reads only closed candles.
SCALP_TAG = 'EXPERIMENTAL / PAPER-only scalp'
SCALP_RT_COST = 2 * (0.0005 + 0.0002)   # round trip as a fraction of price: taker fee + slippage per fill (backtest FEE / SLIP)
RS = dict(n=32, adx_max=20.0, w_min=2.0, w_max=6.0, edge=0.25, inval_atr=0.5)       # range_scalp
FB = dict(n=20, w=3, buf_atr=0.1, min_atr=0.5)                                      # failed_breakout_scalp
MB = dict(n=20, rng_x=2.0, vol_x=2.0, body=0.75, stop_atr=1.5, cost_x=3.0)          # momentum_burst_scalp
QB = dict(stop_atr=1.0, tp1_r=0.8, tp1_frac=0.7, be_r=0.8, tp_r=2.0, max_bars=12)   # quick_bank_runner management


def sig_range_scalp(d, p, ctx):
    """Confirmed-range edge reversion (15m). Range = high / low of the n candles BEFORE the decision candle; confirmed when
    ADX < adx_max and the range is w_min..w_max ATR wide. Long: close inside the bottom `edge` of the range (above the
    range low) on a bullish candle; short: the mirror at the top on a bearish candle. Invalidation: a close more than
    inval_atr ATR outside its range in the last n candles (this one included) blocks entries and exits an open trade.
    Target: exit at the range middle."""
    n = int(p.get('n', RS['n']))
    hi, lo, a = d.h.rolling(n).max().shift(), d.l.rolling(n).min().shift(), d.atr
    w, mid = hi - lo, (hi + lo) / 2
    out_ = X((d.c > hi + RS['inval_atr'] * a) | (d.c < lo - RS['inval_atr'] * a))
    broken = out_.astype(int).rolling(n, min_periods=1).max() > 0
    ok = (d.adx < RS['adx_max']) & (w >= RS['w_min'] * a) & (w <= RS['w_max'] * a) & ~broken
    le = ok & (d.c > lo) & (d.c <= lo + RS['edge'] * w) & (d.c > d.o)
    se = ok & (d.c < hi) & (d.c >= hi - RS['edge'] * w) & (d.c < d.o)
    return _one_side(dict(le=le, se=se, lx=(d.c >= mid) | broken, sx=(d.c <= mid) | broken))


def sig_failed_breakout_scalp(d, p, ctx):
    """Failed range break (15m). Reference range = high / low of the n candles ending w + 1 candles before the decision
    candle. Short: the previous candle closed above the range high, the close w + 1 candles back was still inside (the
    break is at most w candles old), and this candle closes back inside. Long = the mirror below the range low. The stop
    sits beyond the break extreme (highest high / lowest low of the last w + 1 candles incl. this one) + buf_atr ATR, at
    least min_atr ATR away: returned as the signal's `atr` (stop distance, mgmt stop_atr = 1). Exit: a close beyond the
    range again."""
    n, w = int(p.get('n', FB['n'])), int(p.get('w', FB['w']))
    hi, lo = d.h.rolling(n).max().shift(w + 1), d.l.rolling(n).min().shift(w + 1)
    a, c = d.atr, d.c
    se = (c.shift() > hi) & (c < hi) & (c > lo) & (c.shift(w + 1) <= hi)
    le = (c.shift() < lo) & (c > lo) & (c < hi) & (c.shift(w + 1) >= lo)
    buf, floor = FB['buf_atr'] * a, FB['min_atr'] * a
    stop_s = np.maximum(d.h.rolling(w + 1).max() - c + buf, floor)
    stop_l = np.maximum(c - d.l.rolling(w + 1).min() + buf, floor)
    s = _one_side(dict(le=le, se=se, lx=c < lo, sx=c > hi))
    dist = np.where(s['se'], stop_s, np.where(s['le'], stop_l, a)).astype(float)
    s['atr'] = np.where(np.isfinite(dist) & (dist > 0), dist, np.nan)
    return s


def scalp_cost_ok(d, stop_atr=None, cost_x=None):
    """Liquidity / cost gate: the stop distance (stop_atr x ATR) must be >= cost_x x the round-trip cost (fees + slippage)
    so a 1R loss is not mostly cost. Boolean Series (False where ATR is undefined)."""
    k = MB['stop_atr'] if stop_atr is None else stop_atr
    m = MB['cost_x'] if cost_x is None else cost_x
    return X(k * d.atr >= m * SCALP_RT_COST * d.c)


def sig_momentum_burst_scalp(d, p, ctx):
    """Price + volume impulse (15m). The candle's range >= rng_x x and its volume >= vol_x x their n-candle averages (over
    the candles BEFORE it), it closes in the outer `body` of its range in its own direction, and it passes scalp_cost_ok.
    Long on an up-impulse, short on the mirror. Exit on a close back through EMA9; mgmt time cap 4 candles (1 h)."""
    n = int(p.get('n', MB['n']))
    rng = d.h - d.l
    avg_v = d.v.rolling(n).mean().shift()
    big = (rng >= MB['rng_x'] * rng.rolling(n).mean().shift()) & (d.v > 0) & (avg_v > 0) & (d.v >= MB['vol_x'] * avg_v)  # 0 >= 2x0 is no burst
    pos = (d.c - d.l) / rng.where(rng > 0)
    gate = big & scalp_cost_ok(d)
    le = gate & (d.c > d.o) & (pos >= MB['body'])
    se = gate & (d.c < d.o) & (pos <= 1 - MB['body'])
    return _one_side(dict(le=le, se=se, lx=d.c < d.e9, sx=d.c > d.e9))


def sig_quick_bank_runner(d, p, ctx):
    """Fast EMA-pullback continuation (15m): with EMA20 above EMA50 and the close above EMA50, the candle dips to EMA9
    (low <= EMA9) and closes back above it as a bullish candle -> long; exact mirror for shorts. Management (QB): 70 %
    banked at 0.8R with the stop moved to breakeven, the 30 % runner capped at 2R / 12 candles. Exit on an EMA20/50 flip."""
    up, dn = d.e20 > d.e50, d.e20 < d.e50
    le = up & (d.c > d.e50) & (d.l <= d.e9) & (d.c > d.e9) & (d.c > d.o)
    se = dn & (d.c < d.e50) & (d.h >= d.e9) & (d.c < d.e9) & (d.c < d.o)
    return _one_side(dict(le=le, se=se, lx=dn, sx=up))


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
    # ---- sprint families: EXPERIMENTAL / testnet-only (frozen v0, long + short, no demonstrated edge)
    'exp_trend': dict(name='[TESTNET EXP] Trend L/S', style='balanced', fn=sig_exp_trend, sides='both', tf='4h',
                      desc=EXP_TAG + ': EMA20/50 cross on the EMA200 side with 7d+30d momentum agreeing (trend_ema_mom.v1), mirrored '
                           'for shorts. Stop 2.5 ATR, target 3R, exit on cross-back or momentum loss, 180-candle time cap. 4h.',
                      mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=180)),
    'exp_range': dict(name='[TESTNET EXP] Range L/S', style='balanced', fn=sig_exp_range,
                      sides='both', tf='4h', params=dict(k=2.0),
                      desc=EXP_TAG + ': RANGE-EXIT-01 v0. 42-candle VWAP (typical price x volume), ATR = 14-candle mean true range, '
                           'z = (close - VWAP)/ATR. Long when z crosses back above -2 below VWAP while VWAP moved <= 1 ATR over 6 '
                           'candles; short mirrored. Target = back at VWAP, exit if the range breaks, stop 1.5 ATR, 30-candle cap. 4h.',
                      mgmt=dict(stop_atr=1.5, max_bars=30)),
    'exp_breakout': dict(name='[TESTNET EXP] Breakout L/S', style='balanced', fn=sig_exp_breakout,
                         sides='both', tf='4h',
                         desc=EXP_TAG + ': first close above the prior 20-candle high (long) or below the prior 20-candle low '
                              '(short). Stop 2 ATR, target 3R, exit on a close through the opposite 10-candle channel, 60-candle cap. 4h.',
                         mgmt=dict(stop_atr=2.0, tp_r=3.0, max_bars=60)),
    'exp_scalp': dict(name='[TESTNET EXP] Scalp L/S 15m', style='aggressive', fn=sig_exp_scalp,
                      sides='both', tf='15m',
                      desc=EXP_TAG + ': with EMA20 above EMA50, a candle that dips to EMA20 and closes back above it (long); '
                           'mirrored for shorts. Tight stop 1 ATR, target 1.5R, exit when EMA20/50 flips, 16-candle (4h) cap. Run on 15m.',
                      mgmt=dict(stop_atr=1.0, tp_r=1.5, max_bars=16)),
    'exp_structure': dict(name='[TESTNET EXP] Structure L/S', style='aggressive', fn=sig_exp_structure,
                          sides='both', tf='15m',
                          desc=EXP_TAG + ': first close through the latest confirmed 3-candle swing in the EMA50/200 direction. '
                               'Confirmed pivots and closed-candle entries prevent lookahead. Stop 1 ATR, target 2R, opposite-structure '
                               'or regime-flip exit, 24-candle (6h) cap. Run on 15m.',
                          mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24)),
    # ---- BREADTH-01 B3: short-only mirrors of hot_coin / dca_dip (EXPERIMENTAL / testnet-only, no demonstrated edge)
    'exp_mom_short': dict(name='[TESTNET EXP] Mom Short (dump)', style='aggressive', fn=sig_exp_mom_short,
                          sides='short', tf='4h',
                          desc=EXP_TAG + ': short-only mirror of Hot-coin momentum chase. Bottom-3 7-day movers breaking below the '
                               'prior 20-candle low and EMA50 on a volume surge (> 1.5x the 42-candle average). Stop 2 ATR above, '
                               'half off at 2R, 2.5 ATR trail, exit on a close back above EMA20. 4h.',
                          mgmt=dict(stop_atr=2.0, trail_atr=2.5, tp1_r=2.0, tp1_frac=0.5)),
    'exp_rip_sell_dca': dict(name='[TESTNET EXP] Rip-sell DCA S', style='aggressive', fn=sig_exp_rip_sell_dca,
                             sides='short', tf='4h',
                             desc=EXP_TAG + ': UNVERIFIED DCA / martingale basket (never backtested). Short-only mirror of DCA dip '
                                  'buyer: with EMA50 below EMA200, sells the first close > 1.5 ATR above EMA20; up to 3 safety '
                                  'orders 1 ATR apart ABOVE (1.5x each), basket TP 1 ATR below the average, hard basket stop 2 ATR '
                                  'above the last order, 60-candle cap. 4h.',
                             mgmt=dict(dca=dict(n=3, step_atr=1.0, scale=1.5, tp_atr=1.0, stop_atr=2.0), max_bars=60)),
    'exp_vwaprev': dict(name='[TESTNET EXP] VWAP Rev L/S', style='balanced', fn=sig_exp_vwaprev, sides='both', tf='4h',
                        params=dict(n=42, k=2.0, rg=1.0), reentry_lock=True,
                        desc=EXP_TAG + ': VWAPREV = range_vwap_rev.v1 X1 (RANGE-EXIT-01). VWAP42 from quote volume / volume, ATR = '
                             'TR-SMA14, z = (close - VWAP)/ATR. Long when z crosses back above -2 below VWAP while VWAP moved <= 1 ATR '
                             'over 6 candles; short mirrored. Target = close back at VWAP, exit if the range breaks, stop 1.5 TR-SMA14 '
                             'ATR, 30-candle cap. No entry without quote volume or a gap-free 48-candle window. 4h only.',
                        mgmt=dict(stop_atr=1.5, max_bars=30)),
    # ---- SCALP-4: EXPERIMENTAL / PAPER-only scalps. Independent, always-eligible PAPER slots of the breadth rotation (never
    #      rotated, never in breadth_groups); refused in live mode (engine.scalp_block). No demonstrated edge.
    'range_scalp': dict(name='[PAPER EXP] Range scalp L/S 15m', style='aggressive', fn=sig_range_scalp, sides='both', tf='15m',
                        desc=SCALP_TAG + ': confirmed 32-candle range (ADX < 20, 2-6 ATR wide, no close > 0.5 ATR outside it in '
                             'the last 32 candles). Long from the bottom quarter on a bullish candle, short from the top quarter on '
                             'a bearish one. Exit at mid-range or on invalidation; stop 1.25 ATR, 2R cap, 16-candle (4h) cap.',
                        mgmt=dict(stop_atr=1.25, tp_r=2.0, max_bars=16)),
    'failed_breakout_scalp': dict(name='[PAPER EXP] Failed breakout L/S 15m', style='aggressive', fn=sig_failed_breakout_scalp,
                                  sides='both', tf='15m',
                                  desc=SCALP_TAG + ': a close back inside a 20-candle range after a break of at most 3 candles. '
                                       'Short after a failed upside break, long after a failed downside break. Stop just beyond the '
                                       'break extreme (+0.1 ATR, min 0.5 ATR), target 1.5R, exit on a close beyond the range again, '
                                       '12-candle (3h) cap.',
                                  mgmt=dict(stop_atr=1.0, tp_r=1.5, max_bars=12)),
    'momentum_burst_scalp': dict(name='[PAPER EXP] Momentum burst L/S 15m', style='aggressive', fn=sig_momentum_burst_scalp,
                                 sides='both', tf='15m',
                                 desc=SCALP_TAG + ': impulse candle (range and volume >= 2x their 20-candle averages, close in the '
                                      'outer quarter) in its own direction; skipped unless the 1.5 ATR stop is >= 3x the round-trip '
                                      'fee + slippage. Stop 1.5 ATR, target 1.5R, exit on a close back through EMA9, 4-candle (1h) cap.',
                                 mgmt=dict(stop_atr=MB['stop_atr'], tp_r=1.5, max_bars=4)),
    'quick_bank_runner': dict(name='[PAPER EXP] Quick bank + runner L/S 15m', style='aggressive', fn=sig_quick_bank_runner,
                              sides='both', tf='15m',
                              desc=SCALP_TAG + ': EMA9 pullback in an EMA20/50 trend. Banks 70% at 0.8R and moves the stop to '
                                   'breakeven; the 30% runner targets 2R, exits on an EMA20/50 flip or after 12 candles (3h). Stop 1 ATR.',
                              mgmt=dict(QB)),
}


def build_context(dfs, btc_key='BTCUSDT'):
    """Cross-sectional data for rotation / regime strategies, aligned by candle time (coins may have different history)."""
    btc = dfs[btc_key].set_index('t')
    btc_down = (btc.c < btc.e200)
    btc_return = btc.c.pct_change()
    ra = pd.DataFrame({s: pd.Series((d.ret180 / (d.c.pct_change().rolling(180).std() * np.sqrt(180))).values, index=d.t)
                       for s, d in dfs.items()})
    rank = ra.rank(axis=1, ascending=False)
    r42 = pd.DataFrame({s: pd.Series(d.ret42.values, index=d.t) for s, d in dfs.items()})
    r7 = r42.rank(axis=1, ascending=False)
    r7n = r42.notna().sum(axis=1)   # symbols actually ranked at each candle (late listings / gaps excluded)
    breadth7 = (r42 > 0).sum(axis=1) / r7n.replace(0, np.nan)
    rv7 = pd.DataFrame({s: pd.Series(d.c.pct_change().rolling(168, min_periods=168).std().values, index=d.t)
                        for s, d in dfs.items()})
    vol_rank7 = rv7.rank(axis=1, ascending=True)
    vol_rank7_n = rv7.notna().sum(axis=1)
    out = {}
    for s, d in dfs.items():
        t = d.t
        out[s] = dict(btc_down=btc_down.reindex(t).fillna(False).astype(bool).values,
                      btc_return=pd.Series(btc_return.reindex(t).values, index=d.index),
                      rank=pd.Series(rank[s].reindex(t).values, index=d.index).fillna(999),
                      rank7=pd.Series(r7[s].reindex(t).values, index=d.index).fillna(999),
                      rank7_n=pd.Series(r7n.reindex(t).values, index=d.index).fillna(0),
                      breadth7=pd.Series(breadth7.reindex(t).values, index=d.index),
                      vol_rank7=pd.Series(vol_rank7[s].reindex(t).values, index=d.index).fillna(999),
                      vol_rank7_n=pd.Series(vol_rank7_n.reindex(t).values, index=d.index).fillna(0),
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
    if 'atr' in s:                                     # a strategy-defined stop ATR (VWAPREV: TR-SMA14), NaN where undefined
        out['atr'] = np.asarray(s['atr'], dtype=float).copy()
    if mask_sides and st['sides'] == 'long': out['se'][:] = False
    if mask_sides and st['sides'] == 'short': out['le'][:] = False
    return out


# RBR-4H is the reversal counterpart to the existing rotation/relative-strength
# trend sleeve: it buys washed-out bottom ranks only during broad weakness and
# shorts crowded top ranks only during broad strength.
RBR4H = dict(rank_frac=0.20, breadth_low=0.35, breadth_high=0.65,
             rsi_low=30.0, rsi_high=70.0, volume_floor=0.80, cost_multiple=5.0)


def sig_rank_breadth_reversal_4h(d, p, ctx):
    rank = pd.Series(ctx['rank7'], index=d.index, dtype=float)
    n = pd.Series(ctx['rank7_n'], index=d.index, dtype=float)
    breadth = pd.Series(ctx['breadth7'], index=d.index, dtype=float)
    k = np.ceil(float(p['rank_frac']) * n)
    top, bottom = rank <= k, rank > (n - k)
    vol_ok = d.v >= float(p['volume_floor']) * d.v.rolling(42, min_periods=42).median().shift()
    stop_frac = 1.5 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    rlo, rhi = float(p['rsi_low']), float(p['rsi_high'])
    recover_l = (d.rsi2 > rlo) & (d.rsi2.shift() <= rlo) & (d.c > d.o)
    recover_s = (d.rsi2 < rhi) & (d.rsi2.shift() >= rhi) & (d.c < d.o)
    valid = n >= 8
    le = valid & bottom & (breadth <= float(p['breadth_low'])) & recover_l & vol_ok & cost_ok
    se = valid & top & (breadth >= float(p['breadth_high'])) & recover_s & vol_ok & cost_ok
    middle = np.ceil(0.5 * n)
    return dict(le=le, se=se, lx=(rank <= middle) | (breadth > 0.55),
                sx=(rank >= middle) | (breadth < 0.45))


STRATEGIES['rank_breadth_reversal_4h'] = dict(
    name='[RESEARCH] Rank-breadth reversal 4h', style='mean_reversion',
    fn=sig_rank_breadth_reversal_4h, sides='both', tf='4h', research_only=True,
    reentry_lock=True, params=RBR4H,
    desc='Fade 7-day cross-sectional extremes only during broad same-direction crowding.',
    mgmt=dict(stop_atr=1.5, tp1_r=1.0, tp1_frac=0.5, be_r=1.0, tp_r=2.0, max_bars=18))


# ASS-4H complements slow trend rather than chasing it: enter only after a
# completed opposite-direction ATR shock is reclaimed on the following bar.
ASS4H = dict(shock_atr=1.5, volume_floor=0.8, cost_multiple=5.0)


def sig_atr_shock_snapback_4h(d, p, ctx):
    body = d.c - d.o
    shock = float(p['shock_atr'])
    down_shock = body.shift() <= -shock * d.atr.shift()
    up_shock = body.shift() >= shock * d.atr.shift()
    midpoint = (d.o.shift() + d.c.shift()) / 2
    vol_ok = d.v >= float(p['volume_floor']) * d.v.rolling(42, min_periods=42).median().shift()
    stop_frac = 1.5 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    uptrend, downtrend = d.e50 > d.e200, d.e50 < d.e200
    le = uptrend & uptrend.shift(fill_value=False) & down_shock & (d.c > midpoint) & (d.c > d.o) & vol_ok & cost_ok
    se = downtrend & downtrend.shift(fill_value=False) & up_shock & (d.c < midpoint) & (d.c < d.o) & vol_ok & cost_ok
    return dict(le=le, se=se, lx=(d.c >= d.e20) | ~uptrend, sx=(d.c <= d.e20) | ~downtrend)


STRATEGIES['atr_shock_snapback_4h'] = dict(
    name='[RESEARCH] ATR shock snapback 4h', style='mean_reversion',
    fn=sig_atr_shock_snapback_4h, sides='both', tf='4h', research_only=True,
    reentry_lock=True, params=ASS4H,
    desc='One-bar 1.5 ATR counter-trend shock followed by a midpoint reclaim inside EMA50/200 trend.',
    mgmt=dict(stop_atr=1.5, tp1_r=0.8, tp1_frac=0.5, be_r=0.8, tp_r=1.5, max_bars=12))


# SIU-1H tests a clock-based inventory-unwind hypothesis at two fixed UTC
# liquidity handoffs, not another continuously-scanned price pattern.
SIU1H = dict(hours=(8, 16), displacement_atr=1.5, volume_floor=0.8, cost_multiple=5.0)


def sig_session_inventory_unwind_1h(d, p, ctx):
    hour = pd.to_datetime(d.t, utc=True).dt.hour
    handoff = hour.isin(tuple(p['hours']))
    displacement = d.c.shift(1) - d.c.shift(9)  # eight fully closed hours before the decision candle
    scale = float(p['displacement_atr']) * d.atr.shift(1)
    vol_ok = d.v >= float(p['volume_floor']) * d.v.rolling(24, min_periods=24).median().shift()
    stop_frac = 1.5 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = handoff & (displacement <= -scale) & (d.c > d.o) & (d.c > d.c.shift()) & vol_ok & cost_ok
    se = handoff & (displacement >= scale) & (d.c < d.o) & (d.c < d.c.shift()) & vol_ok & cost_ok
    return dict(le=le, se=se, lx=d.c >= d.e9, sx=d.c <= d.e9)


STRATEGIES['session_inventory_unwind_1h'] = dict(
    name='[RESEARCH] Session inventory unwind 1h', style='mean_reversion',
    fn=sig_session_inventory_unwind_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=SIU1H,
    desc='Fade an eight-hour displacement only after reversal at 08:00/16:00 UTC handoff candles.',
    mgmt=dict(stop_atr=1.5, tp1_r=0.8, tp1_frac=0.5, be_r=0.8, tp_r=1.5, max_bars=6))


# VPI-1H tests whether signed volume pressure leads price after a two-day
# absorption window.  It is sampled once daily to keep turnover and costs low.
VPI1H = dict(decision_hour=0, window=48, imbalance=0.10, max_displacement_atr=1.0,
             volume_floor=0.8, cost_multiple=5.0)


def sig_volume_pressure_inventory_1h(d, p, ctx):
    hour = pd.to_datetime(d.t, utc=True).dt.hour
    decision = hour == int(p['decision_hour'])
    w = int(p['window'])
    direction = np.sign(d.c.diff()).fillna(0.0)
    signed = (direction * d.v).rolling(w, min_periods=w).sum()
    total = d.v.rolling(w, min_periods=w).sum()
    pressure = signed / total.replace(0.0, np.nan)
    displacement = d.c - d.c.shift(w)
    absorbed = displacement.abs() <= float(p['max_displacement_atr']) * d.atr
    vol_ok = d.v.rolling(w, min_periods=w).mean() >= (
        float(p['volume_floor']) * d.v.rolling(7 * 24, min_periods=7 * 24).median())
    stop_frac = 2.0 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    threshold = float(p['imbalance'])
    valid = decision & absorbed & vol_ok & cost_ok
    return dict(le=valid & (pressure >= threshold),
                se=valid & (pressure <= -threshold),
                lx=pressure <= 0.0, sx=pressure >= 0.0)


STRATEGIES['volume_pressure_inventory_1h'] = dict(
    name='[RESEARCH] Volume-pressure inventory 1h', style='anticipatory',
    fn=sig_volume_pressure_inventory_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=VPI1H,
    desc='Daily signed-volume pressure after a 48h price-absorption window.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


# RSV-1H tests the cross-asset lottery/forced-selling skew anomaly once daily.
# The rolling return distribution is fully known at the decision close.
RSV1H = dict(decision_hour=0, window=168, skew_threshold=0.75, cost_multiple=5.0)


def sig_realized_skew_valuation_1h(d, p, ctx):
    hour = pd.to_datetime(d.t, utc=True).dt.hour
    decision = hour == int(p['decision_hour'])
    ret = d.c.pct_change()
    skew = ret.rolling(int(p['window']), min_periods=int(p['window'])).skew()
    stop_frac = 2.0 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    threshold = float(p['skew_threshold'])
    return dict(le=decision & cost_ok & (skew <= -threshold),
                se=decision & cost_ok & (skew >= threshold),
                lx=skew >= 0.0, sx=skew <= 0.0)


STRATEGIES['realized_skew_valuation_1h'] = dict(
    name='[RESEARCH] Realized-skew valuation 1h', style='factor_reversion',
    fn=sig_realized_skew_valuation_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=RSV1H,
    desc='Daily fade of extreme seven-day hourly-return skew.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


# MPD-1H uses independently archived Binance mark prices.  The runner injects
# the causal mark-vs-contract close basis into context; production has no key.
MPD1H = dict(hours=(0, 4, 8, 12, 16, 20), basis_threshold=0.0008, cost_multiple=5.0)


def sig_mark_price_dislocation_1h(d, p, ctx):
    basis = pd.Series(ctx['mark_basis'], index=d.index, dtype=float)
    hour = pd.to_datetime(d.t, utc=True).dt.hour
    decision = hour.isin(tuple(p['hours']))
    stop_frac = 1.5 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    threshold = float(p['basis_threshold'])
    return dict(le=decision & cost_ok & (basis <= -threshold),
                se=decision & cost_ok & (basis >= threshold),
                lx=basis >= 0.0, sx=basis <= 0.0)


STRATEGIES['mark_price_dislocation_1h'] = dict(
    name='[RESEARCH] Mark-price dislocation 1h', style='microstructure_reversion',
    fn=sig_mark_price_dislocation_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=MPD1H,
    desc='Fade fixed-time contract-close deviations from independently archived Binance mark price.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=8))


# WLP-1H tests the fixed weekend open/close liquidity-risk premium.  Both
# directions are separate cells; there is no return-conditioned selection.
WLP1H = dict(boundaries=((4, 23), (6, 23)), cost_multiple=5.0)


def sig_weekend_liquidity_premium_1h(d, p, ctx):
    t = pd.to_datetime(d.t, utc=True)
    boundary = pd.Series(False, index=d.index)
    for weekday, hour in tuple(p['boundaries']):
        boundary |= (t.dt.weekday == int(weekday)) & (t.dt.hour == int(hour))
    stop_frac = 2.5 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    entry = boundary & cost_ok
    never = pd.Series(False, index=d.index)
    return dict(le=entry, se=entry, lx=never, sx=never)


STRATEGIES['weekend_liquidity_premium_1h'] = dict(
    name='[RESEARCH] Weekend liquidity premium 1h', style='calendar_risk_premium',
    fn=sig_weekend_liquidity_premium_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=WLP1H,
    desc='Separate long/short cells at fixed UTC weekend open and close transitions.',
    mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=24))


# TJD-1H consumes runner-injected, checksum-verified 1m path features.  A large
# minute jump that is not retained by the hourly close is treated as transient.
TJD1H = dict(jump_threshold=0.006, max_retention=0.50, participation=2.0, cost_multiple=5.0)


def sig_transient_jump_dislocation_1h(d, p, ctx):
    jump = pd.Series(ctx['minute_jump'], index=d.index, dtype=float)
    retention = pd.Series(ctx['jump_retention'], index=d.index, dtype=float)
    participation = pd.Series(ctx['jump_participation'], index=d.index, dtype=float)
    stop_frac = 1.5 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    event = (jump.abs() >= float(p['jump_threshold'])) & (retention <= float(p['max_retention'])) & (
        participation >= float(p['participation'])) & cost_ok
    return dict(le=event & (jump < 0.0), se=event & (jump > 0.0),
                lx=d.c >= d.e9, sx=d.c <= d.e9)


STRATEGIES['transient_jump_dislocation_1h'] = dict(
    name='[RESEARCH] Transient jump dislocation 1h', style='microstructure_reversion',
    fn=sig_transient_jump_dislocation_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=TJD1H,
    desc='Fade a high-participation 1m jump not retained by its completed hour.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=8))


# AOF-1H uses archived taker-buy quote volume, an information field absent from
# the coarse runtime cache. Extreme aggressive flow with little price progress
# is treated as passive-side absorption and subsequent exhaustion.
AOF1H = dict(imbalance_threshold=0.20, max_hour_move=0.004, cost_multiple=5.0)


def sig_aggressor_flow_absorption_1h(d, p, ctx):
    imbalance = pd.Series(ctx['taker_imbalance'], index=d.index, dtype=float)
    hour_move = d.c / d.o - 1.0
    absorbed = hour_move.abs() <= float(p['max_hour_move'])
    stop_frac = 1.5 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    threshold = float(p['imbalance_threshold'])
    return dict(le=absorbed & cost_ok & (imbalance <= -threshold),
                se=absorbed & cost_ok & (imbalance >= threshold),
                lx=d.c >= d.e9, sx=d.c <= d.e9)


STRATEGIES['aggressor_flow_absorption_1h'] = dict(
    name='[RESEARCH] Aggressor-flow absorption 1h', style='orderflow_reversion',
    fn=sig_aggressor_flow_absorption_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=AOF1H,
    desc='Fade extreme verified hourly taker imbalance when price progress is muted.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=8))


# BOL-1H tests delayed cross-asset information diffusion from verified BTC
# aggressor flow into an alt that remained locally flat during the BTC impulse.
BOL1H = dict(btc_imbalance=0.15, btc_move=0.006, target_lag_band=0.002, cost_multiple=5.0)


def sig_btc_orderflow_leadlag_1h(d, p, ctx):
    flow = pd.Series(ctx['btc_taker_imbalance'], index=d.index, dtype=float)
    btc_move = pd.Series(ctx['btc_hour_move'], index=d.index, dtype=float)
    target_move = d.c / d.o - 1.0
    lagged = target_move.abs() <= float(p['target_lag_band'])
    stop_frac = 2.0 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    im, mv = float(p['btc_imbalance']), float(p['btc_move'])
    never = pd.Series(False, index=d.index)
    return dict(le=lagged & cost_ok & (flow >= im) & (btc_move >= mv),
                se=lagged & cost_ok & (flow <= -im) & (btc_move <= -mv),
                lx=never, sx=never)


STRATEGIES['btc_orderflow_leadlag_1h'] = dict(
    name='[RESEARCH] BTC order-flow lead-lag 1h', style='cross_asset_diffusion',
    fn=sig_btc_orderflow_leadlag_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=BOL1H,
    desc='Follow verified BTC aggressor-flow impulses only in locally lagging targets.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


# LTI-1H uses archived trade counts to distinguish large-ticket participation
# from ordinary volume. It follows only a large price impulse with a large
# average quote notional per trade relative to its prior seven-day baseline.
LTI1H = dict(notional_multiple=2.0, move_threshold=0.006, baseline_hours=168, cost_multiple=5.0)


def sig_large_trade_impulse_1h(d, p, ctx):
    avg_trade = pd.Series(ctx['avg_trade_notional'], index=d.index, dtype=float)
    baseline = avg_trade.rolling(int(p['baseline_hours']), min_periods=int(p['baseline_hours'])).median().shift()
    large = avg_trade >= float(p['notional_multiple']) * baseline
    move = d.c / d.o - 1.0
    stop_frac = 2.0 * d.atr / d.c
    cost_ok = stop_frac >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    threshold = float(p['move_threshold']); never = pd.Series(False, index=d.index)
    return dict(le=large & cost_ok & (move >= threshold),
                se=large & cost_ok & (move <= -threshold), lx=never, sx=never)


STRATEGIES['large_trade_impulse_1h'] = dict(
    name='[RESEARCH] Large-trade impulse 1h', style='informed_flow_continuation',
    fn=sig_large_trade_impulse_1h, sides='both', tf='1h', research_only=True,
    reentry_lock=True, params=LTI1H,
    desc='Follow a large directional hour only when average archived trade notional is anomalous.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


# Candidate 23 is the queued H1 re-evaluation of the existing pullback RSI
# short cell.  It intentionally reuses the exact signal and management rules
# under a research-only/default-off identity rather than altering runtime.
STRATEGIES['pullback_rsi_short_h1'] = dict(
    name='[RESEARCH] Pullback RSI short H1', style='balanced',
    fn=sig_trend_pullback_rsi, sides='short', tf='4h', research_only=True,
    reentry_lock=False, params={},
    desc='Queued H1: RSI recovery from overbought inside a completed 4h downtrend.',
    mgmt=dict(stop_atr=2.0, tp1_r=1.0, tp1_frac=0.5, be_r=1.0, tp_r=3.0, max_bars=60))


# Candidate 24 is the queued H2 exact re-evaluation of rotation long under a
# research-only/default-off identity. Runtime rotation remains unchanged.
STRATEGIES['rotation_long_h2'] = dict(
    name='[RESEARCH] Rotation long H2', style='balanced',
    fn=sig_rotation, sides='long', tf='4h', research_only=True,
    reentry_lock=False, params=dict(k=5),
    desc='Queued H2: top-five 30-day risk-adjusted momentum above EMA50.',
    mgmt=dict(stop_atr=3.0))


# Candidate 25 is the queued H3 exact re-evaluation of exp_trend long under a
# research-only/default-off identity. Runtime exp_trend remains unchanged.
STRATEGIES['exp_trend_long_h3'] = dict(
    name='[RESEARCH] Experimental trend long H3', style='balanced',
    fn=sig_exp_trend, sides='long', tf='4h', research_only=True,
    reentry_lock=False, params={},
    desc='Queued H3: EMA20/50 bullish cross above EMA200 with positive 7d and 30d momentum.',
    mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=180))


# Candidate 26 is the queued H4 exact re-evaluation of ema_mom long under a
# research-only/default-off identity. Its entry is shared with H3, but it keeps
# the original signal-exit + stop-only management rather than H3 management.
STRATEGIES['ema_mom_long_h4'] = dict(
    name='[RESEARCH] EMA momentum long H4', style='calm',
    fn=sig_ema_mom, sides='long', tf='4h', research_only=True,
    reentry_lock=False, params={},
    desc='Queued H4: EMA20/50 bullish cross above EMA200 with positive 7d and 30d momentum; original stop-only management.',
    mgmt=dict(stop_atr=2.5))


# Candidate 27 is the next unused existing board cell: exact exp_mom_short,
# isolated under a research-only/default-off identity. Runtime is unchanged.
STRATEGIES['exp_mom_short_c27'] = dict(
    name='[RESEARCH] Cold-coin momentum short C27', style='aggressive',
    fn=sig_exp_mom_short, sides='short', tf='4h', research_only=True,
    reentry_lock=False, params={},
    desc='Candidate 27: bottom-three 7d mover breaks the prior 20-bar low below EMA50 on >1.5x volume.',
    mgmt=dict(stop_atr=2.0, trail_atr=2.5, tp1_r=2.0, tp1_frac=0.5))


# Candidate 28: exact unused exp_rip_sell_dca cell under a research-only,
# default-off identity. Runtime remains unchanged.
STRATEGIES['rip_sell_dca_c28'] = dict(
    name='[RESEARCH] Rip-sell DCA short C28', style='aggressive',
    fn=sig_exp_rip_sell_dca, sides='short', tf='4h', research_only=True,
    reentry_lock=False, params={},
    desc='Candidate 28: downtrend rip >1.5 ATR above EMA20 with frozen short DCA basket management.',
    mgmt=dict(dca=dict(n=3, step_atr=1.0, scale=1.5, tp_atr=1.0, stop_atr=2.0), max_bars=60))


# Candidate 29: the already-emitted but previously off-registry short side of
# ema_st, isolated as research-only/default-off. Runtime remains unchanged.
STRATEGIES['ema_st_short_c29'] = dict(
    name='[RESEARCH] EMA-supertrend short C29', style='balanced',
    fn=sig_ema_st, sides='short', tf='4h', research_only=True,
    reentry_lock=False, params={},
    desc='Candidate 29: EMA20/50 bearish cross below EMA200 with Supertrend bearish confirmation.',
    mgmt=dict(stop_atr=2.5))


DRP1H = dict(channel=24, volume_window=48, volume_multiple=1.5, close_location=0.75)


def sig_daily_range_participation_long_1h(d, p, ctx):
    """C30: prior-day breakout with trend, close-location and causal volume participation gates."""
    channel, vw = int(p['channel']), int(p['volume_window'])
    prior_high = d.h.rolling(channel, min_periods=channel).max().shift()
    vol_base = d.v.rolling(vw, min_periods=vw).median().shift()
    rng = d.h - d.l
    location = (d.c - d.l) / rng.where(rng > 0)
    le = ((d.c > prior_high) & (d.e50 > d.e200) &
          (d.v >= float(p['volume_multiple']) * vol_base) &
          (location >= float(p['close_location'])))
    never = d.c < -1
    return dict(le=le, se=never, lx=(d.c < d.e20) | (d.e50 <= d.e200), sx=never)


STRATEGIES['daily_range_participation_long_1h'] = dict(
    name='[RESEARCH] Daily-range participation long 1h', style='balanced',
    fn=sig_daily_range_participation_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(DRP1H),
    desc='Candidate 30: completed 1h close above prior 24h high, EMA50>EMA200, top-quartile close and >=1.5x median volume.',
    mgmt=dict(stop_atr=2.0, trail_atr=3.0, max_bars=24))


IIR1H = dict(window=168, impact_multiple=3.0, shock_atr=1.5, volume_floor_quantile=0.50, cost_multiple=5.0)


def sig_illiquidity_impact_reversion_long_1h(d, p, ctx):
    """C31: fade a downside return whose return-per-quote-volume impact is anomalous."""
    n = int(p['window'])
    ret = d.c / d.c.shift() - 1.0
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    impact = ret.abs() / qv.where(qv > 0)
    impact_base = impact.rolling(n, min_periods=n).median().shift()
    qv_floor = qv.rolling(n, min_periods=n).quantile(float(p['volume_floor_quantile'])).shift()
    shock = ret <= -float(p['shock_atr']) * d.atr / d.c
    anomalous = impact >= float(p['impact_multiple']) * impact_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = shock & anomalous & (qv >= qv_floor) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c >= d.e20, sx=never)


STRATEGIES['illiquidity_impact_reversion_long_1h'] = dict(
    name='[RESEARCH] Illiquidity-impact reversion long 1h', style='balanced',
    fn=sig_illiquidity_impact_reversion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(IIR1H),
    desc='Candidate 31: fade downside 1h shocks when absolute return per quote-volume is >=3x its trailing median.',
    mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=8))


IBB1H = dict(mother_atr=1.25, inside_ratio=0.70, volume_window=48, volume_multiple=1.0, cost_multiple=5.0)


def sig_inside_bar_breakout_long_1h(d, p, ctx):
    """C32: trend-follow a confirmed breakout from a one-bar contraction."""
    mother_hi, mother_lo = d.h.shift(2), d.l.shift(2)
    mother_range = mother_hi - mother_lo
    inside_range = d.h.shift() - d.l.shift()
    mother_expanded = mother_range >= float(p['mother_atr']) * d.atr.shift(2)
    inside = ((d.h.shift() <= mother_hi) & (d.l.shift() >= mother_lo) &
              (inside_range <= float(p['inside_ratio']) * mother_range))
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    volume_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    participated = qv >= float(p['volume_multiple']) * volume_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = mother_expanded & inside & (d.c > mother_hi) & (d.c > d.e50) & participated & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c < d.e20, sx=never)


STRATEGIES['inside_bar_breakout_long_1h'] = dict(
    name='[RESEARCH] Inside-bar breakout long 1h', style='trend',
    fn=sig_inside_bar_breakout_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(IBB1H),
    desc='Candidate 32: confirm a breakout after one inside-bar contraction within an expanded mother bar.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


RDL1H = dict(window=48, separation=4, divergence_points=8.0, volume_floor=0.80, cost_multiple=5.0)


def sig_rsi_lower_low_divergence_long_1h(d, p, ctx):
    """C33: fade a fresh price low when RSI does not confirm the lower low."""
    n, gap = int(p['window']), int(p['separation'])
    prior_price_low = d.c.shift(gap).rolling(n - gap, min_periods=n - gap).min()
    prior_rsi_low = d.rsi14.shift(gap).rolling(n - gap, min_periods=n - gap).min()
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(n, min_periods=n).median().shift()
    diverged = (d.c < prior_price_low) & (d.rsi14 >= prior_rsi_low + float(p['divergence_points']))
    liquid = qv >= float(p['volume_floor']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = diverged & (d.c > d.o) & liquid & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c >= d.e20, sx=never)


STRATEGIES['rsi_lower_low_divergence_long_1h'] = dict(
    name='[RESEARCH] RSI lower-low divergence long 1h', style='mean_reversion',
    fn=sig_rsi_lower_low_divergence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RDL1H),
    desc='Candidate 33: price makes a fresh 48h low while RSI stays at least eight points above its prior-window low.',
    mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=12))


TBER1H = dict(streak=3, drop_atr=1.5, volume_window=48, volume_floor=0.80, cost_multiple=5.0)


def sig_three_bar_exhaustion_reversal_long_1h(d, p, ctx):
    """C34: fade distributed downside exhaustion after a confirmed bullish reversal."""
    n = int(p['streak'])
    red_streak = pd.Series(True, index=d.index)
    descending = pd.Series(True, index=d.index)
    for k in range(1, n + 1):
        red_streak &= d.c.shift(k) < d.o.shift(k)
        if k < n:
            descending &= d.c.shift(k) < d.c.shift(k + 1)
    drop = d.o.shift(n) - d.c.shift()
    exhausted = drop >= float(p['drop_atr']) * d.atr.shift()
    reversal = (d.c > d.o) & (d.c > d.h.shift())
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    liquid = qv >= float(p['volume_floor']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = red_streak & descending & exhausted & reversal & liquid & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c >= d.e20, sx=never)


STRATEGIES['three_bar_exhaustion_reversal_long_1h'] = dict(
    name='[RESEARCH] Three-bar exhaustion reversal long 1h', style='mean_reversion',
    fn=sig_three_bar_exhaustion_reversal_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(TBER1H),
    desc='Candidate 34: three descending red bars lose >=1.5 ATR before a bullish close above the prior high.',
    mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=12))


OBVL1H = dict(window=48, volume_multiple=1.0, cost_multiple=5.0)


def sig_obv_lead_breakout_long_1h(d, p, ctx):
    """C35: follow a cumulative signed-volume breakout that leads price."""
    n = int(p['window'])
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    signed = pd.Series(np.sign(d.c.diff()), index=d.index).fillna(0.0) * qv
    obv = signed.cumsum()
    obv_high = obv.rolling(n, min_periods=n).max().shift()
    price_high = d.c.rolling(n, min_periods=n).max().shift()
    qv_base = qv.rolling(n, min_periods=n).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (obv > obv_high) & (d.c < price_high) & (d.c > d.e50) & (d.c > d.o) & volume_ok & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c < d.e20, sx=never)


STRATEGIES['obv_lead_breakout_long_1h'] = dict(
    name='[RESEARCH] OBV lead breakout long 1h', style='trend',
    fn=sig_obv_lead_breakout_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(OBVL1H),
    desc='Candidate 35: cumulative signed quote-volume makes a 48h high before price does, above EMA50.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


MFIR1H = dict(window=14, oversold=20.0, exit_level=60.0, volume_window=48, volume_floor=0.80, cost_multiple=5.0)


def sig_mfi_oversold_recovery_long_1h(d, p, ctx):
    """C36: buy a causal volume-weighted money-flow recovery from oversold."""
    n = int(p['window'])
    typical = (d.h + d.l + d.c) / 3.0
    raw_flow = typical * d.v
    direction = typical.diff()
    positive = raw_flow.where(direction > 0, 0.0).rolling(n, min_periods=n).sum()
    negative = raw_flow.where(direction < 0, 0.0).rolling(n, min_periods=n).sum()
    ratio = positive / negative.replace(0, np.nan)
    mfi = 100.0 - 100.0 / (1.0 + ratio)
    level = float(p['oversold'])
    recovered = (mfi > level) & (mfi.shift() <= level)
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    liquid = qv >= float(p['volume_floor']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = recovered & (d.c > d.o) & liquid & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(mfi >= float(p['exit_level'])) | (d.c >= d.e20), sx=never)


STRATEGIES['mfi_oversold_recovery_long_1h'] = dict(
    name='[RESEARCH] MFI oversold recovery long 1h', style='mean_reversion',
    fn=sig_mfi_oversold_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(MFIR1H),
    desc='Candidate 36: 14-bar Money Flow Index crosses above 20 with liquidity and cost admission.',
    mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=12))


KCR1H = dict(ema_window=20, band_atr=2.0, volume_window=48, volume_floor=0.80, cost_multiple=5.0)


def sig_keltner_lower_reclaim_long_1h(d, p, ctx):
    """C37: buy a close back inside the lower EMA/ATR channel."""
    lower = d.e20 - float(p['band_atr']) * d.atr
    reclaimed = (d.c > lower) & (d.c.shift() <= lower.shift())
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    liquid = qv >= float(p['volume_floor']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = reclaimed & (d.c > d.o) & liquid & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c >= d.e20, sx=never)


STRATEGIES['keltner_lower_reclaim_long_1h'] = dict(
    name='[RESEARCH] Keltner lower reclaim long 1h', style='mean_reversion',
    fn=sig_keltner_lower_reclaim_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(KCR1H),
    desc='Candidate 37: bullish close reclaims the EMA20 minus 2 ATR lower channel.',
    mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=12))


WRR1H = dict(window=14, oversold=-80.0, exit_level=-20.0, volume_window=48, volume_floor=0.80, cost_multiple=5.0)


def sig_williams_r_recovery_long_1h(d, p, ctx):
    """C38: buy a Williams %R recovery from the bottom of its rolling range."""
    n = int(p['window'])
    high = d.h.rolling(n, min_periods=n).max()
    low = d.l.rolling(n, min_periods=n).min()
    width = (high - low).replace(0, np.nan)
    wr = -100.0 * (high - d.c) / width
    level = float(p['oversold'])
    recovered = (wr > level) & (wr.shift() <= level)
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    liquid = qv >= float(p['volume_floor']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = recovered & (d.c > d.o) & liquid & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(wr >= float(p['exit_level'])) | (d.c >= d.e20), sx=never)


STRATEGIES['williams_r_recovery_long_1h'] = dict(
    name='[RESEARCH] Williams %R recovery long 1h', style='mean_reversion',
    fn=sig_williams_r_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(WRR1H),
    desc='Candidate 38: Williams %R(14) crosses above -80 with liquidity and cost admission.',
    mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=12))


ESEL1H = dict(compression_window=12, compression_atr=0.25, expansion_atr=0.50,
              volume_window=48, volume_multiple=1.0, cost_multiple=5.0)


def sig_ema_spread_expansion_long_1h(d, p, ctx):
    """C39: follow directional EMA spread expansion after recent convergence."""
    spread = (d.e20 - d.e50) / d.atr.replace(0, np.nan)
    recent_min = spread.abs().shift().rolling(int(p['compression_window']),
                                              min_periods=int(p['compression_window'])).min()
    compressed = recent_min <= float(p['compression_atr'])
    expanded = (spread > float(p['expansion_atr'])) & (spread.shift() <= float(p['expansion_atr']))
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = compressed & expanded & (d.e50 > d.e50.shift(8)) & (d.c > d.e20) & volume_ok & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=spread <= 0.0, sx=never)


STRATEGIES['ema_spread_expansion_long_1h'] = dict(
    name='[RESEARCH] EMA spread expansion long 1h', style='trend',
    fn=sig_ema_spread_expansion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ESEL1H),
    desc='Candidate 39: EMA20/EMA50 spread expands above 0.5 ATR after recent <=0.25 ATR convergence.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


RAPL1H = dict(autocorr_window=24, autocorr_min=0.15, impulse_atr=0.35,
              volume_window=48, volume_multiple=1.0, trend_lag=8, cost_multiple=5.0)


def sig_return_autocorr_persistence_long_1h(d, p, ctx):
    """C40: follow an upside impulse only in a positive serial-dependence state."""
    ret = d.c.pct_change()
    n = int(p['autocorr_window'])
    autocorr = ret.rolling(n, min_periods=n).corr(ret.shift())
    impulse = d.c.diff() / d.atr.replace(0, np.nan) >= float(p['impulse_atr'])
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((autocorr >= float(p['autocorr_min'])) & impulse & (d.c > d.e20) &
          (d.e50 > d.e50.shift(int(p['trend_lag']))) & volume_ok & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=autocorr <= 0.0, sx=never)


STRATEGIES['return_autocorr_persistence_long_1h'] = dict(
    name='[RESEARCH] Return-autocorrelation persistence long 1h', style='trend',
    fn=sig_return_autocorr_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RAPL1H),
    desc='Candidate 40: follow an upside impulse only while lag-1 return autocorrelation is positive.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


VACL1H = dict(fast_tr=6, slow_tr=48, ratio_min=1.20, body_fraction=0.25,
              volume_window=48, volume_multiple=1.0, trend_lag=8, cost_multiple=5.0)


def sig_volatility_acceleration_long_1h(d, p, ctx):
    """C41: follow bullish participation while short-horizon range accelerates."""
    tr = pd.concat([d.h-d.l, (d.h-d.c.shift()).abs(), (d.l-d.c.shift()).abs()], axis=1).max(axis=1)
    fast = tr.rolling(int(p['fast_tr']), min_periods=int(p['fast_tr'])).mean()
    slow = tr.rolling(int(p['slow_tr']), min_periods=int(p['slow_tr'])).mean().shift()
    ratio = fast / slow.replace(0, np.nan)
    body = (d.c-d.o) / (d.h-d.l).replace(0, np.nan)
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((ratio >= float(p['ratio_min'])) & (body >= float(p['body_fraction'])) &
          (d.c > d.e20) & (d.e50 > d.e50.shift(int(p['trend_lag']))) & volume_ok & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=ratio < 1.0, sx=never)


STRATEGIES['volatility_acceleration_long_1h'] = dict(
    name='[RESEARCH] Volatility acceleration long 1h', style='trend',
    fn=sig_volatility_acceleration_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(VACL1H),
    desc='Candidate 41: follow bullish participation when 6h true range exceeds 1.2x the prior 48h baseline.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


DSEL1H = dict(window=24, entropy_max=0.90, positive_share_min=0.625, impulse_atr=0.20,
              volume_window=48, volume_multiple=1.0, cost_multiple=5.0)


def sig_directional_sign_entropy_long_1h(d, p, ctx):
    """C42: follow bullish dominance only when return signs are unusually ordered."""
    up = (d.c.diff() > 0).astype(float)
    n = int(p['window']); share = up.rolling(n, min_periods=n).mean()
    q = share.clip(1e-12, 1 - 1e-12)
    entropy = -(q * np.log2(q) + (1-q) * np.log2(1-q))
    impulse = d.c.diff() / d.atr.replace(0, np.nan) >= float(p['impulse_atr'])
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((entropy <= float(p['entropy_max'])) & (share >= float(p['positive_share_min'])) &
          impulse & (d.c > d.e20) & volume_ok & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(share <= 0.5) | (entropy > 0.98), sx=never)


STRATEGIES['directional_sign_entropy_long_1h'] = dict(
    name='[RESEARCH] Directional sign entropy long 1h', style='trend',
    fn=sig_directional_sign_entropy_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(DSEL1H),
    desc='Candidate 42: follow bullish dominance when 24h binary return-sign entropy is <=0.90.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


ROAB1H = dict(overlap_window=12, overlap_min=0.55, breakout_window=24,
              acceptance_atr=0.10, volume_window=48, volume_multiple=1.0,
              cost_multiple=5.0)


def sig_range_overlap_acceptance_long_1h(d, p, ctx):
    """C43: dense adjacent-range overlap, then two closes accepted above balance."""
    pair_hi = pd.concat([d.h, d.h.shift()], axis=1).max(axis=1)
    pair_lo = pd.concat([d.l, d.l.shift()], axis=1).min(axis=1)
    intersection = (pd.concat([d.h, d.h.shift()], axis=1).min(axis=1) -
                    pd.concat([d.l, d.l.shift()], axis=1).max(axis=1)).clip(lower=0)
    pair_union = (pair_hi - pair_lo).replace(0, np.nan)
    pair_overlap = intersection / pair_union
    # At decision t the balance statistic and edge stop at t-2.  t-1 is the
    # breakout close and t is the independent price-acceptance close.
    overlap = pair_overlap.rolling(int(p['overlap_window']) - 1,
                                   min_periods=int(p['overlap_window']) - 1).mean().shift(2)
    edge = d.h.rolling(int(p['breakout_window']),
                       min_periods=int(p['breakout_window'])).max().shift(2)
    broke = (d.c.shift() > edge) & (d.c.shift(2) <= edge)
    accepted = d.c >= edge + float(p['acceptance_atr']) * d.atr
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']),
                         min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (broke & accepted & (overlap >= float(p['overlap_min'])) &
          (d.c > d.e50) & volume_ok & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c < d.e20, sx=never)


STRATEGIES['range_overlap_acceptance_long_1h'] = dict(
    name='[RESEARCH] Range-overlap acceptance long 1h', style='breakout',
    fn=sig_range_overlap_acceptance_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ROAB1H),
    desc='Candidate 43: dense 12h candle-range overlap followed by two closes accepted above a prior 24h edge.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


STPER1H = dict(state_bars=6, state_atr=1.0, transition_window=120,
               min_bull_origins=20, persistence_min=0.60,
               persistence_exit=0.50, volume_window=48,
               volume_multiple=1.0, cost_multiple=5.0)


def sig_state_persistence_long_1h(d, p, ctx):
    """C44: enter a bullish normalized-return state only when it historically persists."""
    move = (d.c - d.c.shift(int(p['state_bars']))) / d.atr.replace(0, np.nan)
    state = pd.Series(0, index=d.index, dtype=int)
    state.loc[move >= float(p['state_atr'])] = 1
    state.loc[move <= -float(p['state_atr'])] = -1
    bull_origin = (state.shift() == 1).astype(float)
    bull_to_bull = ((state.shift() == 1) & (state == 1)).astype(float)
    w = int(p['transition_window'])
    # Shift excludes the transition into the decision bar.  The conditional
    # frequency is learned only from the preceding w completed transitions.
    origins = bull_origin.rolling(w, min_periods=w).sum().shift()
    repeats = bull_to_bull.rolling(w, min_periods=w).sum().shift()
    persistence = repeats / origins.replace(0, np.nan)
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']),
                         min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    enough = origins >= int(p['min_bull_origins'])
    le = ((state == 1) & enough & (persistence >= float(p['persistence_min'])) &
          (d.c > d.e50) & volume_ok & cost_ok)
    never = d.c < -1
    lx = (state != 1) | (persistence < float(p['persistence_exit']))
    return dict(le=le, se=never, lx=lx, sx=never)


STRATEGIES['state_persistence_long_1h'] = dict(
    name='[RESEARCH] Empirical bull-state persistence long 1h', style='trend',
    fn=sig_state_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(STPER1H),
    desc='Candidate 44: enter a normalized bullish six-hour state when prior bull-to-bull persistence is >=60%.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


EHBR1H = dict(resistance_window=24, tolerance_atr=0.25, min_touches=2,
              min_separation=3, breakout_atr=0.10, volume_window=48,
              volume_multiple=1.25, cost_multiple=5.0)


def sig_equal_high_breakout_long_1h(d, p, ctx):
    """C45: break a causally observed cluster of separated near-equal highs."""
    n = int(p['resistance_window'])
    lagged = pd.concat({lag: d.h.shift(lag) for lag in range(1, n + 1)}, axis=1)
    edge = lagged.max(axis=1)
    near = lagged.ge(edge - float(p['tolerance_atr']) * d.atr, axis=0)
    lag_no = pd.DataFrame(np.tile(np.arange(1, n + 1), (len(d), 1)),
                          index=d.index, columns=lagged.columns)
    closest = lag_no.where(near).min(axis=1)
    furthest = lag_no.where(near).max(axis=1)
    cluster = ((near.sum(axis=1) >= int(p['min_touches'])) &
               ((furthest - closest) >= int(p['min_separation'])))
    breakout = ((d.c >= edge + float(p['breakout_atr']) * d.atr) &
                (d.c.shift() <= edge.shift()))
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']),
                         min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cluster & breakout & (d.c > d.e50) & volume_ok & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c < d.e20, sx=never)


STRATEGIES['equal_high_breakout_long_1h'] = dict(
    name='[RESEARCH] Equal-high cluster breakout long 1h', style='breakout',
    fn=sig_equal_high_breakout_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(EHBR1H),
    desc='Candidate 45: buffered breakout above two separated near-equal highs in the prior 24 completed bars.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


ELBD1H = dict(support_window=24, tolerance_atr=0.25, min_touches=2,
              min_separation=3, breakdown_atr=0.10, volume_window=48,
              volume_multiple=1.25, cost_multiple=5.0)


def sig_equal_low_breakdown_short_1h(d, p, ctx):
    """C46: break a causally observed cluster of separated near-equal lows."""
    n = int(p['support_window'])
    lagged = pd.concat({lag: d.l.shift(lag) for lag in range(1, n + 1)}, axis=1)
    edge = lagged.min(axis=1)
    near = lagged.le(edge + float(p['tolerance_atr']) * d.atr, axis=0)
    lag_no = pd.DataFrame(np.tile(np.arange(1, n + 1), (len(d), 1)),
                          index=d.index, columns=lagged.columns)
    closest = lag_no.where(near).min(axis=1)
    furthest = lag_no.where(near).max(axis=1)
    cluster = ((near.sum(axis=1) >= int(p['min_touches'])) &
               ((furthest - closest) >= int(p['min_separation'])))
    breakdown = ((d.c <= edge - float(p['breakdown_atr']) * d.atr) &
                 (d.c.shift() >= edge.shift()))
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']),
                         min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = cluster & breakdown & (d.c < d.e50) & volume_ok & cost_ok
    never = d.c < -1
    return dict(le=never, se=se, lx=never, sx=d.c > d.e20)


STRATEGIES['equal_low_breakdown_short_1h'] = dict(
    name='[RESEARCH] Equal-low cluster breakdown short 1h', style='breakout',
    fn=sig_equal_low_breakdown_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ELBD1H),
    desc='Candidate 46: buffered breakdown below two separated near-equal lows in the prior 24 completed bars.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


OBE1H = dict(range_atr=1.25, close_location=0.75, volume_window=48,
             volume_multiple=1.25, cost_multiple=5.0)


def sig_outside_bar_expansion_long_1h(d, p, ctx):
    """C47: continuation after a bullish range-engulfing outside bar."""
    rng = d.h - d.l
    location = (d.c - d.l) / rng.where(rng > 0)
    outside = (d.h > d.h.shift()) & (d.l < d.l.shift())
    accepted = d.c > d.h.shift()
    expanded = rng >= float(p['range_atr']) * d.atr
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']),
                         min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (outside & accepted & expanded &
          (location >= float(p['close_location'])) &
          (d.e50 > d.e200) & volume_ok & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(d.c < d.e20) | (d.e50 <= d.e200), sx=never)


STRATEGIES['outside_bar_expansion_long_1h'] = dict(
    name='[RESEARCH] Outside-bar expansion long 1h', style='breakout',
    fn=sig_outside_bar_expansion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(OBE1H),
    desc='Candidate 47: ATR-expanded outside bar engulfs the prior range and accepts above its high in an EMA50/200 uptrend.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


OBD1H = dict(range_atr=1.25, close_location=0.25, volume_window=48,
             volume_multiple=1.25, cost_multiple=5.0)


def sig_outside_bar_breakdown_short_1h(d, p, ctx):
    """C48: continuation after a bearish range-engulfing outside bar."""
    rng = d.h - d.l
    location = (d.c - d.l) / rng.where(rng > 0)
    outside = (d.h > d.h.shift()) & (d.l < d.l.shift())
    accepted = d.c < d.l.shift()
    expanded = rng >= float(p['range_atr']) * d.atr
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    qv_base = qv.rolling(int(p['volume_window']),
                         min_periods=int(p['volume_window'])).median().shift()
    volume_ok = qv >= float(p['volume_multiple']) * qv_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = (outside & accepted & expanded &
          (location <= float(p['close_location'])) &
          (d.e50 < d.e200) & volume_ok & cost_ok)
    never = d.c < -1
    return dict(le=never, se=se, lx=never, sx=(d.c > d.e20) | (d.e50 >= d.e200))


STRATEGIES['outside_bar_breakdown_short_1h'] = dict(
    name='[RESEARCH] Outside-bar breakdown short 1h', style='breakout',
    fn=sig_outside_bar_breakdown_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(OBD1H),
    desc='Candidate 48: ATR-expanded outside bar engulfs the prior range and accepts below its low in an EMA50/200 downtrend.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


def sig_inside_bar_breakdown_short_1h(d, p, ctx):
    """C49: short a confirmed breakdown from a one-bar contraction."""
    mother_hi, mother_lo = d.h.shift(2), d.l.shift(2)
    mother_range = mother_hi - mother_lo
    inside_range = d.h.shift() - d.l.shift()
    mother_expanded = mother_range >= float(p['mother_atr']) * d.atr.shift(2)
    inside = ((d.h.shift() <= mother_hi) & (d.l.shift() >= mother_lo) &
              (inside_range <= float(p['inside_ratio']) * mother_range))
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    volume_base = qv.rolling(int(p['volume_window']), min_periods=int(p['volume_window'])).median().shift()
    participated = qv >= float(p['volume_multiple']) * volume_base
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = mother_expanded & inside & (d.c < mother_lo) & (d.c < d.e50) & participated & cost_ok
    never = d.c < -1
    return dict(le=never, se=se, lx=never, sx=d.c > d.e20)


STRATEGIES['inside_bar_breakdown_short_1h'] = dict(
    name='[RESEARCH] Inside-bar breakdown short 1h', style='trend',
    fn=sig_inside_bar_breakdown_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(IBB1H),
    desc='Candidate 49: confirm a breakdown after one inside-bar contraction within an expanded mother bar.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=16))


HVS1H = dict(season_days=14, volume_multiple=2.0, impulse_atr=0.75,
             cost_multiple=5.0)


def sig_hourly_volume_surprise_long_1h(d, p, ctx):
    """C50: momentum after quote-volume surprise versus the same UTC hour."""
    days = int(p['season_days'])
    qv = pd.Series(d.qv, index=d.index, dtype=float)
    same_hour = pd.concat({day: qv.shift(24 * day)
                           for day in range(1, days + 1)}, axis=1)
    baseline = same_hour.median(axis=1, skipna=False)
    volume_surprise = qv >= float(p['volume_multiple']) * baseline
    impulse = (d.c - d.c.shift()) >= float(p['impulse_atr']) * d.atr
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = volume_surprise & impulse & (d.c > d.e50) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c < d.e20, sx=never)


STRATEGIES['hourly_volume_surprise_long_1h'] = dict(
    name='[RESEARCH] Same-hour volume surprise long 1h', style='momentum',
    fn=sig_hourly_volume_surprise_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(HVS1H),
    desc='Candidate 50: follow a 0.75 ATR upside impulse only when quote volume is >=2x the prior 14 same-UTC-hour median.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


RKR1H = dict(decision_hour=0, window=168, excess_kurtosis=5.0,
             cost_multiple=5.0)


def sig_realized_kurtosis_risk_short_1h(d, p, ctx):
    """C51: short persistent fat-tail/lottery demand once daily."""
    hour = pd.to_datetime(d.t, utc=True).dt.hour
    decision = hour == int(p['decision_hour'])
    ret = d.c.pct_change()
    kurt = ret.rolling(int(p['window']), min_periods=int(p['window'])).kurt()
    cost_ok = 2.5 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = decision & (kurt >= float(p['excess_kurtosis'])) & cost_ok
    never = d.c < -1
    return dict(le=never, se=se, lx=never, sx=kurt <= 0.0)


STRATEGIES['realized_kurtosis_risk_short_1h'] = dict(
    name='[RESEARCH] Realized-kurtosis risk short 1h', style='factor_reversion',
    fn=sig_realized_kurtosis_risk_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RKR1H),
    desc='Candidate 51: daily short when trailing seven-day hourly returns have excess kurtosis >=5.',
    mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=24))


VRL1H = dict(window=72, correlation=0.25, volume_jump=1.50,
             cost_multiple=5.0)


def sig_volume_return_leadlag_long_1h(d, p, ctx):
    """C52: use a validated own-symbol volume-change lead to forecast return."""
    qv = pd.Series(d.qv, index=d.index, dtype=float).replace(0.0, np.nan)
    volume_change = np.log(qv).diff()
    ret = d.c.pct_change()
    w = int(p['window'])
    predictive_corr = volume_change.shift().rolling(w, min_periods=w).corr(ret)
    shock = volume_change >= np.log(float(p['volume_jump']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (predictive_corr >= float(p['correlation'])) & shock & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=predictive_corr <= 0.0, sx=never)


STRATEGIES['volume_return_leadlag_long_1h'] = dict(
    name='[RESEARCH] Volume-return lead-lag long 1h', style='anticipatory',
    fn=sig_volume_return_leadlag_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(VRL1H),
    desc='Candidate 52: follow a 1.5x quote-volume jump only when lagged volume changes predicted next-hour returns over 72h.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=8))


HODD1H = dict(season_days=28, expected_return=0.0020,
              cost_multiple=5.0)


def sig_hour_of_day_drift_long_1h(d, p, ctx):
    """C53: trade learned next-hour UTC seasonality, never the current hour."""
    ret = d.c.pct_change()
    days = int(p['season_days'])
    # At t, ret[t + 1 - 24*d] is a historical realization of the hour
    # entered at t+1.  The nearest sample is therefore shifted 23 bars.
    same_next_hour = pd.concat({day: ret.shift(24 * day - 1)
                                for day in range(1, days + 1)}, axis=1)
    expected = same_next_hour.mean(axis=1, skipna=False)
    cost_ok = 1.5 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (expected >= float(p['expected_return'])) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=expected <= 0.0, sx=never)


STRATEGIES['hour_of_day_drift_long_1h'] = dict(
    name='[RESEARCH] Learned hour-of-day drift long 1h', style='calendar_risk_premium',
    fn=sig_hour_of_day_drift_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(HODD1H),
    desc='Candidate 53: hold the next hour when its prior 28 same-UTC-hour returns average at least +20 bp.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=1))


BBR1H = dict(beta_window=336, residual_window=168, residual_z=-2.5,
             cost_multiple=5.0)


def sig_btc_beta_residual_reversion_long_1h(d, p, ctx):
    """C54: fade an idiosyncratic hourly underperformance shock versus BTC."""
    target_return = d.c.pct_change()
    btc_return = pd.Series(ctx['btc_return'], index=d.index, dtype=float)
    bw, rw = int(p['beta_window']), int(p['residual_window'])
    # Beta is estimated only through t-1; the current completed return is then
    # compared with that frozen exposure estimate.  The z-score baseline also
    # ends at t-1, so the entry at t+1 cannot leak its own shock into the fit.
    beta = target_return.rolling(bw, min_periods=bw).cov(btc_return).shift() / (
        btc_return.rolling(bw, min_periods=bw).var().shift().replace(0.0, np.nan))
    residual = target_return - beta * btc_return
    mean = residual.rolling(rw, min_periods=rw).mean().shift()
    std = residual.rolling(rw, min_periods=rw).std().shift().replace(0.0, np.nan)
    z = (residual - mean) / std
    cost_ok = 1.5 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (z <= float(p['residual_z'])) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=z >= 0.0, sx=never)


STRATEGIES['btc_beta_residual_reversion_long_1h'] = dict(
    name='[RESEARCH] BTC-beta residual reversion long 1h', style='cross_asset_stat_arb',
    fn=sig_btc_beta_residual_reversion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(BBR1H),
    desc='Candidate 54: fade a <=-2.5 sigma hourly residual after a prior-only 14-day BTC-beta fit.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=4))


WIP1H = dict(window=12, pressure=0.20, cost_multiple=5.0)


def sig_wick_imbalance_pressure_long_1h(d, p, ctx):
    """C55: follow persistent failed downward auctions measured by wick asymmetry."""
    body_high = pd.concat([d.o, d.c], axis=1).max(axis=1)
    body_low = pd.concat([d.o, d.c], axis=1).min(axis=1)
    upper_wick = d.h - body_high
    lower_wick = body_low - d.l
    candle_range = (d.h - d.l).replace(0.0, np.nan)
    wick_imbalance = (lower_wick - upper_wick) / candle_range
    pressure = wick_imbalance.rolling(int(p['window']), min_periods=int(p['window'])).mean()
    cost_ok = 1.5 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (pressure >= float(p['pressure'])) & (d.c > d.o) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=pressure <= 0.0, sx=never)


STRATEGIES['wick_imbalance_pressure_long_1h'] = dict(
    name='[RESEARCH] Wick-imbalance pressure long 1h', style='auction_pressure',
    fn=sig_wick_imbalance_pressure_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(WIP1H),
    desc='Candidate 55: buy a bullish close after 12h average normalized lower-minus-upper wick pressure >=0.20.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=8))


RTT1H = dict(window=48, slope_t=3.0, cost_multiple=5.0)


def _rolling_log_slope_t(close, window):
    """OLS slope t-stat for each trailing fixed-length log-price window."""
    n = int(window)
    x = np.arange(n, dtype=float)
    xc = x - x.mean()
    ssx = float(np.dot(xc, xc))
    def score(raw):
        if len(raw) != n or not np.isfinite(raw).all():
            return np.nan
        y = np.log(raw)
        slope = float(np.dot(xc, y) / ssx)
        residual = y - (y.mean() + slope * xc)
        mse = float(np.dot(residual, residual) / (n - 2))
        if mse <= 0.0:
            return np.inf if slope > 0.0 else (-np.inf if slope < 0.0 else 0.0)
        return slope / np.sqrt(mse / ssx)
    return close.rolling(n, min_periods=n).apply(score, raw=True)


def sig_regression_trend_quality_long_1h(d, p, ctx):
    """C56: follow only statistically coherent positive log-price trends."""
    slope_t = _rolling_log_slope_t(d.c.astype(float), int(p['window']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (slope_t >= float(p['slope_t'])) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=slope_t <= 0.0, sx=never)


STRATEGIES['regression_trend_quality_long_1h'] = dict(
    name='[RESEARCH] Regression trend-quality long 1h', style='statistical_trend',
    fn=sig_regression_trend_quality_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RTT1H),
    desc='Candidate 56: follow a positive 48h log-price OLS slope only when its t-statistic is at least 3.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


DSR1H = dict(window=168, downside_ratio=2.0, cost_multiple=5.0)


def sig_downside_semivariance_rebound_long_1h(d, p, ctx):
    """C57: rebound after downside realized variance dominates upside variance."""
    ret = d.c.pct_change()
    w = int(p['window'])
    downside = ret.clip(upper=0.0).pow(2).rolling(w, min_periods=w).mean()
    upside = ret.clip(lower=0.0).pow(2).rolling(w, min_periods=w).mean().replace(0.0, np.nan)
    ratio = downside / upside
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (ratio >= float(p['downside_ratio'])) & (ret > 0.0) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=ratio <= 1.0, sx=never)


STRATEGIES['downside_semivariance_rebound_long_1h'] = dict(
    name='[RESEARCH] Downside-semivariance rebound long 1h', style='tail_risk_rebound',
    fn=sig_downside_semivariance_rebound_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(DSR1H),
    desc='Candidate 57: buy a positive hour when seven-day downside semivariance is at least 2x upside semivariance.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


DDRH1H = dict(high_window=168, min_age=48, drawdown=-0.10,
              cost_multiple=5.0)


def sig_drawdown_duration_recovery_long_1h(d, p, ctx):
    """C58: first EMA20 recovery after a deep, prolonged underwater spell."""
    w = int(p['high_window'])
    rolling_high = d.c.rolling(w, min_periods=w).max()
    underwater = d.c < rolling_high
    spell = (~underwater).cumsum()
    age = underwater.astype(int).groupby(spell).cumsum()
    drawdown = d.c / rolling_high - 1.0
    reclaim = (d.c > d.e20) & (d.c.shift() <= d.e20.shift())
    prior_state = (age.shift() >= int(p['min_age'])) & (drawdown.shift() <= float(p['drawdown']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = prior_state & reclaim & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(d.c < d.e20) | ~underwater, sx=never)


STRATEGIES['drawdown_duration_recovery_long_1h'] = dict(
    name='[RESEARCH] Drawdown-duration recovery long 1h', style='path_dependent_recovery',
    fn=sig_drawdown_duration_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(DDRH1H),
    desc='Candidate 58: EMA20 reclaim after >=48h underwater and a prior >=10% drawdown from the seven-day high.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


LVF1H = dict(window=168, select_k=5, exit_rank=8, cost_multiple=5.0)


def sig_cross_sectional_low_vol_long_1h(d, p, ctx):
    """C59: hold the lowest realized-volatility members of the contemporaneous research pack."""
    rank = ctx['vol_rank7']
    eligible = ctx['vol_rank7_n'] >= 8
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = eligible & (rank <= int(p['select_k'])) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(rank > int(p['exit_rank'])) | ~eligible, sx=never)


STRATEGIES['cross_sectional_low_vol_long_1h'] = dict(
    name='[RESEARCH] Cross-sectional low-volatility long 1h', style='defensive_factor',
    fn=sig_cross_sectional_low_vol_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(LVF1H),
    desc='Candidate 59: hold the five lowest trailing-seven-day realized-volatility members of the research pack.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


VCD1H = dict(window=168, min_corr=0.20, cost_multiple=5.0)


def sig_volatility_clustering_downside_short_1h(d, p, ctx):
    """C60: follow downside pressure only when squared returns show persistent volatility clustering."""
    ret = d.c.pct_change()
    squared = ret.pow(2)
    cluster = squared.rolling(int(p['window']), min_periods=int(p['window'])).corr(squared.shift())
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = (cluster >= float(p['min_corr'])) & (ret24 < 0.0) & (ret < 0.0) & cost_ok
    never = d.c < -1
    return dict(le=never, se=se, lx=never, sx=(cluster <= 0.0) | (ret24 >= 0.0))


STRATEGIES['volatility_clustering_downside_short_1h'] = dict(
    name='[RESEARCH] Volatility-clustering downside short 1h', style='conditional_variance_continuation',
    fn=sig_volatility_clustering_downside_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(VCD1H),
    desc='Candidate 60: short negative hours in a negative 24h path when 168h squared-return lag-1 correlation >=0.20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


ICR1H = dict(window=168, ratio=2.0, cost_multiple=5.0)


def sig_intrabar_churn_reversion_long_1h(d, p, ctx):
    """C61: fade a negative hour when range-based variance persistently dominates close variance."""
    w = int(p['window'])
    parkinson = np.log(d.h / d.l).pow(2) / (4.0 * np.log(2.0))
    close_var = np.log(d.c / d.c.shift()).pow(2)
    ratio = parkinson.rolling(w, min_periods=w).mean() / close_var.rolling(w, min_periods=w).mean().replace(0.0, np.nan)
    ret = d.c.pct_change()
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (ratio >= float(p['ratio'])) & (ret < 0.0) & (d.c < d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(ratio <= 1.0) | (d.c >= d.e20), sx=never)


STRATEGIES['intrabar_churn_reversion_long_1h'] = dict(
    name='[RESEARCH] Intrabar-churn reversion long 1h', style='range_variance_reversion',
    fn=sig_intrabar_churn_reversion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ICR1H),
    desc='Candidate 61: fade a negative hour below EMA20 when 168h Parkinson variance is >=2x close variance.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CSD1H = dict(vol_window=48, threshold=4.0, reference=0.10, cost_multiple=5.0)


def sig_cusum_downshift_short_1h(d, p, ctx):
    """C62: one-sided sequential CUSUM alarm for a persistent negative standardized-return shift."""
    ret = d.c.pct_change()
    sigma = ret.rolling(int(p['vol_window']), min_periods=int(p['vol_window'])).std().shift()
    z = ret / sigma.replace(0.0, np.nan)
    alarm = np.zeros(len(d), dtype=bool)
    g = 0.0
    for i, x in enumerate(z.to_numpy(float)):
        if not np.isfinite(x):
            g = 0.0
            continue
        g = min(0.0, g + x + float(p['reference']))
        if g <= -float(p['threshold']):
            alarm[i] = True
            g = 0.0
    alarm = pd.Series(alarm, index=d.index)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=never, se=alarm & cost_ok, lx=never, sx=d.c >= d.e20)


STRATEGIES['cusum_downshift_short_1h'] = dict(
    name='[RESEARCH] CUSUM downshift short 1h', style='sequential_change_detection',
    fn=sig_cusum_downshift_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CSD1H),
    desc='Candidate 62: short a one-sided CUSUM alarm after standardized negative returns cross -4.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


MSR1H = dict(window=168, horizon=24, max_h=0.40, cost_multiple=5.0)


def _rolling_variance_scaling_exponent(close, window, horizon):
    """Causal two-scale Hurst proxy: Var(q-step return) / Var(one-step return) = q^(2H)."""
    log_close = np.log(close.astype(float))
    r1 = log_close.diff()
    rq = log_close.diff(int(horizon))
    v1 = r1.rolling(int(window), min_periods=int(window)).var().replace(0.0, np.nan)
    vq = rq.rolling(int(window), min_periods=int(window)).var()
    return 0.5 * np.log(vq / v1) / np.log(float(horizon))


def sig_multiscale_roughness_reversion_long_1h(d, p, ctx):
    """C63: fade a negative hour only when multi-horizon variance scaling is anti-persistent."""
    exponent = _rolling_variance_scaling_exponent(d.c, int(p['window']), int(p['horizon']))
    ret = d.c.pct_change()
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (exponent <= float(p['max_h'])) & (ret < 0.0) & (d.c < d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(exponent >= 0.50) | (d.c >= d.e20), sx=never)


STRATEGIES['multiscale_roughness_reversion_long_1h'] = dict(
    name='[RESEARCH] Multiscale-roughness reversion long 1h', style='fractal_scaling_reversion',
    fn=sig_multiscale_roughness_reversion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(MSR1H),
    desc='Candidate 63: fade a negative hour below EMA20 when the 1h-to-24h variance-scaling exponent is <=0.40.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


LEV1H = dict(window=168, max_corr=-0.25, cost_multiple=5.0)


def sig_leverage_effect_downside_short_1h(d, p, ctx):
    """C64: downside continuation when returns covary negatively with changes in realized variance."""
    ret = d.c.pct_change()
    variance_change = ret.pow(2).diff()
    leverage_corr = ret.rolling(int(p['window']), min_periods=int(p['window'])).corr(variance_change)
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = ((leverage_corr <= float(p['max_corr'])) & (ret24 < 0.0) & (ret < 0.0) & cost_ok)
    never = d.c < -1
    return dict(le=never, se=se, lx=never, sx=(leverage_corr >= 0.0) | (ret24 >= 0.0))


STRATEGIES['leverage_effect_downside_short_1h'] = dict(
    name='[RESEARCH] Leverage-effect downside short 1h', style='return_variance_cross_moment',
    fn=sig_leverage_effect_downside_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(LEV1H),
    desc='Candidate 64: short a negative hour in a negative 24h path when return/variance-change correlation <=-0.25.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


OUH1H = dict(window=168, entry_z=-2.0, cost_multiple=5.0)


def _rolling_ou_state(close, window):
    """Rolling error-correction coefficient, implied half-life and price z-score from completed closes."""
    log_close = np.log(close.astype(float))
    lag = log_close.shift()
    delta = log_close.diff()
    w = int(window)
    beta = delta.rolling(w, min_periods=w).cov(lag) / lag.rolling(w, min_periods=w).var().replace(0.0, np.nan)
    half_life = pd.Series(np.where(beta < 0.0, -np.log(2.0) / beta, np.nan), index=close.index)
    mean = log_close.rolling(w, min_periods=w).mean()
    std = log_close.rolling(w, min_periods=w).std().replace(0.0, np.nan)
    return beta, half_life, (log_close - mean) / std


def sig_ou_half_life_reversion_long_1h(d, p, ctx):
    """C65: buy a recovering negative displacement only when estimated error correction is tradeably fast."""
    beta, half_life, z = _rolling_ou_state(d.c, int(p['window']))
    ret = d.c.pct_change()
    valid_speed = (half_life >= 2.0) & (half_life <= 24.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = valid_speed & (z <= float(p['entry_z'])) & (ret > 0.0) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(z >= 0.0) | (beta >= 0.0) | ~valid_speed, sx=never)


STRATEGIES['ou_half_life_reversion_long_1h'] = dict(
    name='[RESEARCH] OU half-life reversion long 1h', style='error_correction_reversion',
    fn=sig_ou_half_life_reversion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(OUH1H),
    desc='Candidate 65: buy a recovering <=-2z log-price displacement when estimated OU half-life is 2-24 hours.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


JVS1H = dict(window=168, min_jump_share=0.25, exit_jump_share=0.10, cost_multiple=5.0)


def _rolling_jump_share(close, window):
    """Causal RV-minus-bipower jump share and completed close-to-close log return."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    w = int(window)
    rv = ret.pow(2).rolling(w, min_periods=w).sum()
    cross = ret.abs() * ret.abs().shift()
    bv = (np.pi / 2.0) * cross.rolling(w, min_periods=w).sum()
    share = (rv - bv).clip(lower=0.0) / rv.replace(0.0, np.nan)
    return share, ret


def sig_jump_variation_downside_short_1h(d, p, ctx):
    """C66: follow downside only when discontinuous variation dominates the trailing path."""
    share, ret = _rolling_jump_share(d.c, int(p['window']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = ((share >= float(p['min_jump_share'])) & (ret24 < 0.0) &
          (ret < 0.0) & cost_ok)
    never = d.c < -1
    return dict(le=never, se=se, lx=never,
                sx=(share <= float(p['exit_jump_share'])) | (ret24 >= 0.0))


STRATEGIES['jump_variation_downside_short_1h'] = dict(
    name='[RESEARCH] Jump-variation downside short 1h', style='nonparametric_jump_decomposition',
    fn=sig_jump_variation_downside_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(JVS1H),
    desc='Candidate 66: short downside when trailing seven-day RV-minus-bipower jump share is at least 25%.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


LTD1H = dict(window=336, quantile=0.10, min_tail_dependence=0.40,
             exit_tail_dependence=0.20, min_btc_exceedances=20, cost_multiple=5.0)


def _rolling_lower_tail_dependence(target_return, btc_return, window, quantile):
    """Prior-only empirical P(target lower-tail | BTC lower-tail) with causal rolling quantiles."""
    w, q = int(window), float(quantile)
    target_q = target_return.rolling(w, min_periods=w).quantile(q).shift()
    btc_q = btc_return.rolling(w, min_periods=w).quantile(q).shift()
    target_hit = target_return <= target_q
    btc_hit = btc_return <= btc_q
    joint = (target_hit & btc_hit).astype(float).rolling(w, min_periods=w).sum().shift()
    btc_count = btc_hit.astype(float).rolling(w, min_periods=w).sum().shift()
    return joint / btc_count.replace(0.0, np.nan), btc_count, btc_q


def sig_lower_tail_dependence_short_1h(d, p, ctx):
    """C67: follow BTC deleveraging when the asset has persistent nonlinear lower-tail dependence."""
    target_return = d.c.pct_change()
    btc_return = pd.Series(ctx['btc_return'], index=d.index, dtype=float)
    dependence, btc_count, btc_q = _rolling_lower_tail_dependence(
        target_return, btc_return, int(p['window']), float(p['quantile']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    se = ((dependence >= float(p['min_tail_dependence'])) &
          (btc_count >= int(p['min_btc_exceedances'])) &
          (btc_return <= btc_q) & (target_return < 0.0) & cost_ok)
    never = d.c < -1
    return dict(le=never, se=se, lx=never,
                sx=(dependence <= float(p['exit_tail_dependence'])) | (btc_return >= 0.0))


STRATEGIES['lower_tail_dependence_short_1h'] = dict(
    name='[RESEARCH] Lower-tail dependence short 1h', style='nonlinear_cross_asset_tail_dependence',
    fn=sig_lower_tail_dependence_short_1h, sides='short', tf='1h', research_only=True,
    reentry_lock=True, params=dict(LTD1H),
    desc='Candidate 67: short BTC tail shocks when prior empirical lower-tail dependence is at least 40%.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12))


RNB1H = dict(cost_multiple=5.0)


def _prior_round_number_levels(close):
    """Nearest decimal-grid barriers around the prior completed close."""
    prior = close.astype(float).shift()
    valid = prior.where(prior > 0.0)
    step = pd.Series(10.0 ** (np.floor(np.log10(valid)) - 1.0), index=close.index)
    upper = np.ceil(valid / step) * step
    lower = np.floor(valid / step) * step
    return upper, lower, step


def sig_round_number_breakout_long_1h(d, p, ctx):
    """C68: follow an upside breach of an exogenous decimal price barrier."""
    upper, lower, _ = _prior_round_number_levels(d.c)
    prior = d.c.shift()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (prior < upper) & (d.c >= upper) & (d.c > prior) & (ret24 > 0.0) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(d.c < lower) | (ret24 <= 0.0), sx=never)


STRATEGIES['round_number_breakout_long_1h'] = dict(
    name='[RESEARCH] Round-number breakout long 1h', style='decimal_price_barrier',
    fn=sig_round_number_breakout_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RNB1H),
    desc='Candidate 68: buy a positive-24h upside breach of the next one-tenth-decade decimal price barrier.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


PRD1H = dict(window=168, radius_atr=0.5, max_density=0.05,
             exit_density=0.20, cost_multiple=5.0)


def _rolling_price_recurrence_density(close, atr, window, radius_atr):
    """Fraction of prior closes within a current-ATR neighbourhood of each completed close."""
    c, a = close.to_numpy(float), atr.to_numpy(float)
    w, radius_x = int(window), float(radius_atr)
    out = np.full(len(close), np.nan)
    for i in range(w, len(close)):
        if not (np.isfinite(c[i]) and np.isfinite(a[i]) and a[i] > 0.0):
            continue
        hist = c[i - w:i]
        valid = np.isfinite(hist)
        if valid.sum() == w:
            out[i] = np.mean(np.abs(hist - c[i]) <= radius_x * a[i])
    return pd.Series(out, index=close.index)


def sig_price_recurrence_discovery_long_1h(d, p, ctx):
    """C69: follow positive price discovery into a sparsely revisited local price zone."""
    density = _rolling_price_recurrence_density(d.c, d.atr, int(p['window']), float(p['radius_atr']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((density <= float(p['max_density'])) & (ret > 0.0) & (ret24 > 0.0) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(density >= float(p['exit_density'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['price_recurrence_discovery_long_1h'] = dict(
    name='[RESEARCH] Price-recurrence discovery long 1h', style='price_occupation_density',
    fn=sig_price_recurrence_discovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(PRD1H),
    desc='Candidate 69: buy positive price discovery when <=5% of prior 168 closes lie within 0.5 current ATR.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


LFP1H = dict(window=168, low_bins=7, min_share=0.15, exit_share=0.10, cost_multiple=5.0)


def _rolling_low_frequency_power_share(close, window, low_bins):
    """Share of demeaned log-return FFT power in the first non-DC frequency bins."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    w, bins = int(window), int(low_bins)
    def share(raw):
        if len(raw) != w or not np.isfinite(raw).all():
            return np.nan
        x = raw - raw.mean()
        power = np.abs(np.fft.rfft(x)) ** 2
        total = power[1:].sum()
        return float(power[1:1 + bins].sum() / total) if total > 0.0 else np.nan
    return ret.rolling(w, min_periods=w).apply(share, raw=True), ret


def sig_low_frequency_power_long_1h(d, p, ctx):
    """C70: follow positive drift when return variance concentrates at low frequencies."""
    share, ret = _rolling_low_frequency_power_share(d.c, int(p['window']), int(p['low_bins']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (share >= float(p['min_share'])) & (ret > 0.0) & (ret24 > 0.0) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(share <= float(p['exit_share'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['low_frequency_power_long_1h'] = dict(
    name='[RESEARCH] Low-frequency return-power long 1h', style='frequency_domain_persistence',
    fn=sig_low_frequency_power_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(LFP1H),
    desc='Candidate 70: buy positive drift when the first seven FFT bins contain at least 15% of 168h return power.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


VHC1H = dict(window=24, min_hhi=2.0, exit_hhi=1.25, cost_multiple=5.0)


def _rolling_volume_hhi(qv, window):
    """Normalized temporal HHI of quote-volume shares; one is uniform participation."""
    w = int(window)
    q = qv.astype(float).where(qv.astype(float) > 0.0)
    total = q.rolling(w, min_periods=w).sum()
    return w * q.pow(2).rolling(w, min_periods=w).sum() / total.pow(2).replace(0.0, np.nan)


def sig_volume_concentration_continuation_long_1h(d, p, ctx):
    """C71: follow positive drift when recent participation is concentrated in event hours."""
    hhi = _rolling_volume_hhi(d.qv, int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (hhi >= float(p['min_hhi'])) & (ret > 0.0) & (ret24 > 0.0) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(hhi <= float(p['exit_hhi'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['volume_concentration_continuation_long_1h'] = dict(
    name='[RESEARCH] Volume-concentration continuation long 1h', style='participation_concentration',
    fn=sig_volume_concentration_continuation_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(VHC1H),
    desc='Candidate 71: buy positive drift when normalized 24h quote-volume HHI is at least 2.0.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


HTR1H = dict(window=168, tail_k=12, max_entry_alpha=3.0, exit_alpha=5.0, cost_multiple=5.0)


def _rolling_downside_hill_alpha(close, window, tail_k):
    """Causal Hill exponent from the largest completed downside log returns."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    w, k = int(window), int(tail_k)

    def hill(raw):
        losses = -raw[raw < 0.0]
        if len(losses) < k + 1:
            return np.nan
        x = np.sort(losses)[::-1]
        threshold = x[k]
        if threshold <= 0.0:
            return np.nan
        denom = np.log(x[:k] / threshold).sum()
        return float(k / denom) if denom > 0.0 else np.nan

    return ret.rolling(w, min_periods=w).apply(hill, raw=True), ret


def sig_hill_tail_recovery_long_1h(d, p, ctx):
    """C72: buy a recovery bar after a statistically heavy downside tail."""
    alpha, ret = _rolling_downside_hill_alpha(d.c, int(p['window']), int(p['tail_k']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (alpha <= float(p['max_entry_alpha'])) & (ret > 0.0) & (d.c < d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(alpha >= float(p['exit_alpha'])) | (d.c >= d.e20), sx=never)


STRATEGIES['hill_tail_recovery_long_1h'] = dict(
    name='[RESEARCH] Hill downside-tail recovery long 1h', style='extreme_value_recovery',
    fn=sig_hill_tail_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(HTR1H),
    desc='Candidate 72: buy a positive recovery below EMA20 when the 168h downside Hill exponent is at most 3.0.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


TMR1H = dict(window=24, trim_each_tail=4, max_raw_sum=-0.03, min_trimmed_mean=0.0001,
             cost_multiple=5.0)


def _rolling_trimmed_return_location(close, window, trim_each_tail):
    """Completed log-return sum and symmetric trimmed central mean."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    w, trim = int(window), int(trim_each_tail)

    def center(raw):
        if len(raw) != w or not np.isfinite(raw).all() or 2 * trim >= w:
            return np.nan
        ordered = np.sort(raw)
        return float(ordered[trim:w-trim].mean())

    return ret.rolling(w, min_periods=w).sum(), ret.rolling(w, min_periods=w).apply(center, raw=True), ret


def sig_trimmed_return_recovery_long_1h(d, p, ctx):
    """C73: fade a tail-driven selloff whose robust central return remains positive."""
    raw_sum, trimmed_mean, ret = _rolling_trimmed_return_location(
        d.c, int(p['window']), int(p['trim_each_tail']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((raw_sum <= float(p['max_raw_sum'])) &
          (trimmed_mean >= float(p['min_trimmed_mean'])) &
          (ret > 0.0) & (d.c < d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(raw_sum >= 0.0) | (trimmed_mean <= 0.0), sx=never)


STRATEGIES['trimmed_return_recovery_long_1h'] = dict(
    name='[RESEARCH] Trimmed-return dislocation recovery long 1h', style='robust_location_recovery',
    fn=sig_trimmed_return_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(TMR1H),
    desc='Candidate 73: buy recovery when the 24h log return is <=-3% but its four-per-tail trimmed mean stays positive.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


MIR1H = dict(ema_span=9, sum_window=25, min_mass=27.0, exit_mass=25.0, cost_multiple=5.0)


def _mass_index(high, low, ema_span, sum_window):
    """Mass Index: summed ratio of single- to double-smoothed completed ranges."""
    span = (high.astype(float) - low.astype(float)).where(high.astype(float) > low.astype(float))
    e1 = span.ewm(span=int(ema_span), adjust=False, min_periods=int(ema_span)).mean()
    e2 = e1.ewm(span=int(ema_span), adjust=False, min_periods=int(ema_span)).mean()
    ratio = e1 / e2.replace(0.0, np.nan)
    return ratio.rolling(int(sum_window), min_periods=int(sum_window)).sum()


def sig_mass_index_recovery_long_1h(d, p, ctx):
    """C74: buy a recovery bar during a persistent high-low range bulge."""
    mass = _mass_index(d.h, d.l, int(p['ema_span']), int(p['sum_window']))
    ret = d.c.pct_change()
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (mass >= float(p['min_mass'])) & (ret > 0.0) & (d.c < d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(mass <= float(p['exit_mass'])) | (d.c >= d.e20), sx=never)


STRATEGIES['mass_index_recovery_long_1h'] = dict(
    name='[RESEARCH] Mass-index range-bulge recovery long 1h', style='range_cycle_recovery',
    fn=sig_mass_index_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(MIR1H),
    desc='Candidate 74: buy a positive recovery below EMA20 while the 9/9/25 Mass Index is at least 27.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


TPR1H = dict(window=48, max_entry_rate=0.50, exit_rate=2.0/3.0, cost_multiple=5.0)


def _rolling_turning_point_rate(close, window):
    """Fraction of interior completed closes that are strict local extrema."""
    w = int(window)

    def rate(raw):
        if len(raw) != w or not np.isfinite(raw).all() or w < 3:
            return np.nan
        middle = raw[1:-1]
        turns = ((middle > raw[:-2]) & (middle > raw[2:])) | ((middle < raw[:-2]) & (middle < raw[2:]))
        return float(turns.mean())

    return close.astype(float).rolling(w, min_periods=w).apply(rate, raw=True)


def sig_turning_point_persistence_long_1h(d, p, ctx):
    """C75: follow positive drift when the ordinal price path has unusually few reversals."""
    turn_rate = _rolling_turning_point_rate(d.c, int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((turn_rate <= float(p['max_entry_rate'])) & (ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(turn_rate >= float(p['exit_rate'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['turning_point_persistence_long_1h'] = dict(
    name='[RESEARCH] Turning-point persistence long 1h', style='ordinal_path_persistence',
    fn=sig_turning_point_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(TPR1H),
    desc='Candidate 75: follow positive drift when at most half of 46 interior points in 48 closes are local extrema.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


AROON1H = dict(window=25, min_oscillator=70.0, exit_oscillator=0.0, cost_multiple=5.0)


def _rolling_aroon_oscillator(high, low, window):
    """Extreme-recency oscillator using only completed rolling highs and lows."""
    w = int(window)

    def recency(raw, find_high):
        if len(raw) != w or not np.isfinite(raw).all() or w < 2:
            return np.nan
        idx = int(np.argmax(raw) if find_high else np.argmin(raw))
        return 100.0 * idx / (w - 1)

    up = high.astype(float).rolling(w, min_periods=w).apply(lambda x: recency(x, True), raw=True)
    down = low.astype(float).rolling(w, min_periods=w).apply(lambda x: recency(x, False), raw=True)
    return up - down


def sig_aroon_recency_long_1h(d, p, ctx):
    """C76: follow a recent-high/stale-low renewal state."""
    osc = _rolling_aroon_oscillator(d.h, d.l, int(p['window']))
    ret = d.c.pct_change()
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (osc >= float(p['min_oscillator'])) & (ret > 0.0) & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(osc <= float(p['exit_oscillator'])) | (d.c <= d.e20), sx=never)


STRATEGIES['aroon_recency_long_1h'] = dict(
    name='[RESEARCH] Aroon extreme-recency long 1h', style='extreme_recency_persistence',
    fn=sig_aroon_recency_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(AROON1H),
    desc='Candidate 76: follow positive drift when 25h Aroon-up minus Aroon-down is at least 70.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


VTX1H = dict(window=14, min_oscillator=0.20, exit_oscillator=0.0, cost_multiple=5.0)


def _rolling_vortex_oscillator(high, low, close, window):
    """Rolling VI+ minus VI- from completed cross-bar range movement."""
    w = int(window)
    vm_plus = (high.astype(float) - low.astype(float).shift()).abs()
    vm_minus = (low.astype(float) - high.astype(float).shift()).abs()
    prev_close = close.astype(float).shift()
    tr = pd.concat([(high-low).abs(), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    denom = tr.rolling(w, min_periods=w).sum().replace(0.0, np.nan)
    return (vm_plus.rolling(w, min_periods=w).sum() -
            vm_minus.rolling(w, min_periods=w).sum()) / denom


def sig_vortex_movement_long_1h(d, p, ctx):
    """C77: follow dominant positive cross-range travel."""
    osc = _rolling_vortex_oscillator(d.h, d.l, d.c, int(p['window']))
    ret = d.c.pct_change()
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (osc >= float(p['min_oscillator'])) & (ret > 0.0) & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(osc <= float(p['exit_oscillator'])) | (d.c <= d.e20), sx=never)


STRATEGIES['vortex_movement_long_1h'] = dict(
    name='[RESEARCH] Vortex cross-range movement long 1h', style='directional_range_movement',
    fn=sig_vortex_movement_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(VTX1H),
    desc='Candidate 77: follow positive drift when 14h VI+ minus VI- is at least 0.20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


OMG1H = dict(window=48, min_omega=1.50, exit_omega=1.0, cost_multiple=5.0)


def _rolling_omega_ratio(close, window):
    """Zero-threshold Omega ratio from completed positive and negative log returns."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    w = int(window)
    gains = ret.clip(lower=0.0).rolling(w, min_periods=w).sum()
    losses = (-ret.clip(upper=0.0)).rolling(w, min_periods=w).sum().replace(0.0, np.nan)
    return gains / losses, ret


def sig_omega_payoff_persistence_long_1h(d, p, ctx):
    """C78: follow drift when rolling realized gains dominate realized losses."""
    omega, ret = _rolling_omega_ratio(d.c, int(p['window']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = (omega >= float(p['min_omega'])) & (ret > 0.0) & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(omega <= float(p['exit_omega'])) | (d.c <= d.e20), sx=never)


STRATEGIES['omega_payoff_persistence_long_1h'] = dict(
    name='[RESEARCH] Omega payoff-persistence long 1h', style='payoff_distribution_persistence',
    fn=sig_omega_payoff_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(OMG1H),
    desc='Candidate 78: follow positive drift when 48h zero-threshold Omega ratio is at least 1.50.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CHOP1H = dict(window=14, max_entry=38.2, exit_level=61.8, cost_multiple=5.0)


def _rolling_choppiness_index(high, low, close, window):
    """Log-normalized traversed true range relative to the enclosing price range."""
    w = int(window)
    prev_close = close.astype(float).shift()
    tr = pd.concat([(high-low).abs(), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    path = tr.rolling(w, min_periods=w).sum()
    enclosure = (high.astype(float).rolling(w, min_periods=w).max() -
                 low.astype(float).rolling(w, min_periods=w).min()).replace(0.0, np.nan)
    return 100.0 * np.log10(path / enclosure) / np.log10(float(w))


def sig_choppiness_persistence_long_1h(d, p, ctx):
    """C79: follow positive drift when true-range travel is efficiently directional."""
    chop = _rolling_choppiness_index(d.h, d.l, d.c, int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((chop <= float(p['max_entry'])) & (ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(chop >= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['choppiness_persistence_long_1h'] = dict(
    name='[RESEARCH] Choppiness-index persistence long 1h', style='range_efficiency_persistence',
    fn=sig_choppiness_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CHOP1H),
    desc='Candidate 79: follow positive drift when the 14h Choppiness Index is at most 38.2.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


JEX1H = dict(event_atr=0.75, half_life=6.0, min_intensity=0.20,
             exit_intensity=0.10, cost_multiple=5.0)


def _positive_jump_excitation(close, atr, event_atr, half_life):
    """Exponentially decayed rate of completed-bar positive ATR-sized jumps."""
    c = close.astype(float)
    ret = np.log(c / c.shift())
    threshold = float(event_atr) * atr.astype(float) / c
    valid = ret.notna() & threshold.notna() & (threshold > 0.0)
    event = (ret >= threshold).astype(float).where(valid)
    intensity = event.ewm(halflife=float(half_life), adjust=False, min_periods=1).mean()
    return intensity, ret


def sig_positive_jump_excitation_long_1h(d, p, ctx):
    """C80: follow positive drift while recent positive jump arrivals cluster."""
    intensity, ret = _positive_jump_excitation(
        d.c, d.atr, float(p['event_atr']), float(p['half_life']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((intensity >= float(p['min_intensity'])) & (ret > 0.0) &
          (ret24 > 0.0) & (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(intensity <= float(p['exit_intensity'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['positive_jump_excitation_long_1h'] = dict(
    name='[RESEARCH] Positive-jump excitation long 1h', style='event_excitation_persistence',
    fn=sig_positive_jump_excitation_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(JEX1H),
    desc='Candidate 80: follow positive drift when ATR-sized upside jump arrivals cluster.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


RCON1H = dict(window=24, max_entry=0.08, exit_level=0.15, cost_multiple=5.0)


def _absolute_return_concentration(close, window):
    """Rolling Herfindahl concentration of completed-bar absolute log returns."""
    w = int(window)
    absolute = np.log(close.astype(float) / close.astype(float).shift()).abs()
    total = absolute.rolling(w, min_periods=w).sum()
    squared = absolute.pow(2).rolling(w, min_periods=w).sum()
    return squared / total.pow(2).replace(0.0, np.nan)


def sig_return_concentration_persistence_long_1h(d, p, ctx):
    """C81: follow positive drift distributed broadly through the recent path."""
    concentration = _absolute_return_concentration(d.c, int(p['window']))
    ret = np.log(d.c.astype(float) / d.c.astype(float).shift())
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((concentration <= float(p['max_entry'])) & (ret > 0.0) &
          (ret24 > 0.0) & (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(concentration >= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['return_concentration_persistence_long_1h'] = dict(
    name='[RESEARCH] Diffuse-return persistence long 1h', style='temporal_return_concentration',
    fn=sig_return_concentration_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RCON1H),
    desc='Candidate 81: follow positive drift when absolute returns are broadly distributed through 24h.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


IQRC1H = dict(window=24, baseline=96, max_ratio=0.60, impulse_atr=0.50, cost_multiple=5.0)


def _return_iqr_compression(close, window, baseline):
    """Current return IQR relative to the lagged median of prior rolling IQRs."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    w = int(window)
    iqr = (ret.rolling(w, min_periods=w).quantile(0.75) -
           ret.rolling(w, min_periods=w).quantile(0.25))
    prior_median = iqr.shift().rolling(int(baseline), min_periods=int(baseline)).median()
    return iqr / prior_median.replace(0.0, np.nan), ret


def sig_iqr_compression_breakout_long_1h(d, p, ctx):
    """C82: follow an upside impulse released from robust return compression."""
    ratio, ret = _return_iqr_compression(d.c, int(p['window']), int(p['baseline']))
    ret24 = d.c.pct_change(24)
    impulse = ret >= float(p['impulse_atr']) * d.atr / d.c
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((ratio <= float(p['max_ratio'])) & impulse & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(ratio >= 1.0) | (ret24 <= 0.0), sx=never)


STRATEGIES['iqr_compression_breakout_long_1h'] = dict(
    name='[RESEARCH] Return-IQR compression breakout long 1h', style='robust_compression_release',
    fn=sig_iqr_compression_breakout_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(IQRC1H),
    desc='Candidate 82: follow an upside impulse released from a compressed 24h return IQR.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


ULCR1H = dict(window=48, max_entry=0.03, exit_level=0.05, cost_multiple=5.0)


def _rolling_ulcer_index(close, window):
    """RMS percentage drawdown from the causal rolling high."""
    c = close.astype(float)
    w = int(window)
    peak = c.rolling(w, min_periods=w).max()
    drawdown = c / peak - 1.0
    return drawdown.pow(2).rolling(w, min_periods=w).mean().pow(0.5)


def sig_ulcer_resilience_long_1h(d, p, ctx):
    """C83: follow positive drift while rolling drawdown severity stays shallow."""
    ulcer = _rolling_ulcer_index(d.c, int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((ulcer <= float(p['max_entry'])) & (ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(ulcer >= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['ulcer_resilience_long_1h'] = dict(
    name='[RESEARCH] Ulcer-resilience long 1h', style='drawdown_severity_persistence',
    fn=sig_ulcer_resilience_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ULCR1H),
    desc='Candidate 83: follow positive drift while 48h RMS drawdown severity remains shallow.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


DCOS1H = dict(threshold_atr=1.0, cost_multiple=5.0)


def _directional_change_events(close, atr, threshold_atr):
    """Causal event-time reversals from maintained extrema using a dynamic ATR threshold."""
    c = close.astype(float)
    a = atr.astype(float)
    up = pd.Series(False, index=c.index)
    down = pd.Series(False, index=c.index)
    mode, peak, trough = 0, np.nan, np.nan
    for pos in range(len(c)):
        price, av = float(c.iloc[pos]), float(a.iloc[pos])
        if not np.isfinite(price) or not np.isfinite(av) or price <= 0.0 or av <= 0.0:
            continue
        if not np.isfinite(peak):
            peak = trough = price
            continue
        if mode >= 0:
            peak = max(peak, price)
        if mode <= 0:
            trough = min(trough, price)
        threshold = float(threshold_atr) * av / price
        if mode <= 0 and np.log(price / trough) >= threshold:
            up.iloc[pos] = True
            mode, peak = 1, price
        elif mode >= 0 and np.log(peak / price) >= threshold:
            down.iloc[pos] = True
            mode, trough = -1, price
    return up, down


def sig_directional_change_overshoot_long_1h(d, p, ctx):
    """C84: follow the overshoot after an ATR-confirmed upward directional change."""
    up, down = _directional_change_events(d.c, d.atr, float(p['threshold_atr']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = up & (ret24 > 0.0) & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=down | (ret24 <= 0.0), sx=never)


STRATEGIES['directional_change_overshoot_long_1h'] = dict(
    name='[RESEARCH] Directional-change overshoot long 1h', style='event_time_overshoot',
    fn=sig_directional_change_overshoot_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(DCOS1H),
    desc='Candidate 84: follow the overshoot after an upward one-ATR directional-change event.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


LZC1H = dict(window=32, max_entry=1.40, exit_level=1.65, cost_multiple=5.0)


def _lz78_binary_complexity(values):
    """Normalized LZ78 phrase count for one finite binary sequence."""
    bits = tuple(int(x > 0.5) for x in values)
    n = len(bits)
    if n < 2:
        return np.nan
    dictionary, phrases, pos = set(), 0, 0
    while pos < n:
        end = pos + 1
        while end <= n and bits[pos:end] in dictionary:
            end += 1
        if end <= n:
            dictionary.add(bits[pos:end])
            pos = end
        else:
            pos = n
        phrases += 1
    return phrases * np.log2(float(n)) / float(n)


def _rolling_sign_lz_complexity(close, window):
    ret = np.log(close.astype(float) / close.astype(float).shift())
    sign = (ret > 0.0).astype(float).where(ret.notna())
    complexity = sign.rolling(int(window), min_periods=int(window)).apply(
        _lz78_binary_complexity, raw=True)
    return complexity, ret


def sig_lz_sign_complexity_long_1h(d, p, ctx):
    """C85: follow positive drift when ordered return signs remain compressible."""
    complexity, ret = _rolling_sign_lz_complexity(d.c, int(p['window']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((complexity <= float(p['max_entry'])) & (ret > 0.0) &
          (ret24 > 0.0) & (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(complexity >= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['lz_sign_complexity_long_1h'] = dict(
    name='[RESEARCH] LZ sign-complexity long 1h', style='ordered_sequence_complexity',
    fn=sig_lz_sign_complexity_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(LZC1H),
    desc='Candidate 85: follow positive drift when 32h return-sign ordering has low LZ78 complexity.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


VSTB1H = dict(window=48, max_entry=0.50, exit_level=0.80, cost_multiple=5.0)


def _volatility_of_volatility_cv(close, window):
    """Coefficient of variation of the causal six-return realized-volatility series."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    realized = ret.rolling(6, min_periods=6).std(ddof=0)
    w = int(window)
    mean = realized.rolling(w, min_periods=w).mean()
    variation = realized.rolling(w, min_periods=w).std(ddof=0)
    return variation / mean.replace(0.0, np.nan), ret


def sig_volatility_stability_long_1h(d, p, ctx):
    """C86: follow positive drift while short-horizon realized volatility is stable."""
    vol_cv, ret = _volatility_of_volatility_cv(d.c, int(p['window']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((vol_cv <= float(p['max_entry'])) & (ret > 0.0) &
          (ret24 > 0.0) & (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(vol_cv >= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['volatility_stability_long_1h'] = dict(
    name='[RESEARCH] Volatility-stability long 1h', style='volatility_of_volatility',
    fn=sig_volatility_stability_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(VSTB1H),
    desc='Candidate 86: follow positive drift when six-hour realized volatility has low 48h variation.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


BCR1H = dict(window=48, max_entry=0.10, exit_level=0.25, cost_multiple=5.0)


def _ema_boundary_crossing_rate(close, ema, window):
    """Fraction of completed transitions that flip side relative to a causal EMA."""
    valid = close.notna() & ema.notna()
    above = close.astype(float) > ema.astype(float)
    prior_valid = valid.shift(fill_value=False)
    crossing = (above != above.shift()) & valid & prior_valid
    return crossing.astype(float).where(valid & prior_valid).rolling(
        int(window), min_periods=int(window)).mean()


def sig_boundary_crossing_persistence_long_1h(d, p, ctx):
    """C87: follow positive drift when EMA boundary flips remain rare."""
    crossing_rate = _ema_boundary_crossing_rate(d.c, d.e20, int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((crossing_rate <= float(p['max_entry'])) & (ret > 0.0) &
          (ret24 > 0.0) & (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(crossing_rate >= float(p['exit_level'])) | (d.c <= d.e20), sx=never)


STRATEGIES['boundary_crossing_persistence_long_1h'] = dict(
    name='[RESEARCH] EMA boundary-crossing persistence long 1h', style='boundary_crossing_intensity',
    fn=sig_boundary_crossing_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(BCR1H),
    desc='Candidate 87: follow positive drift when 48h EMA20 boundary-crossing intensity is low.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


KMON1H = dict(window=24, min_entry=0.70, exit_level=0.30, cost_multiple=5.0)


def _kendall_time_tau(values):
    """Kendall tau-a between a finite value sequence and its strict time order."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    pairs = n * (n - 1) // 2
    if pairs <= 0 or not np.isfinite(x).all():
        return np.nan
    score = 0.0
    for pos in range(n - 1):
        score += np.sign(x[pos + 1:] - x[pos]).sum()
    return score / float(pairs)


def _rolling_kendall_monotonicity(close, window):
    log_price = np.log(close.astype(float))
    return log_price.rolling(int(window), min_periods=int(window)).apply(
        _kendall_time_tau, raw=True)


def sig_kendall_monotonicity_long_1h(d, p, ctx):
    """C88: follow positive drift when the recent price path is ordinally monotone."""
    tau = _rolling_kendall_monotonicity(d.c, int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((tau >= float(p['min_entry'])) & (ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never, lx=(tau <= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['kendall_monotonicity_long_1h'] = dict(
    name='[RESEARCH] Kendall-monotonicity long 1h', style='ordinal_trend_monotonicity',
    fn=sig_kendall_monotonicity_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(KMON1H),
    desc='Candidate 88: follow positive drift when 24h log-price Kendall tau-a is at least 0.70.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


TRA1H = dict(window=48, min_entry=0.10, exit_level=0.0, cost_multiple=5.0)


def _time_reversal_asymmetry(close, window):
    """Standardized third-order return-ordering moment using completed bars only."""
    ret = np.log(close.astype(float) / close.astype(float).shift())
    lag = ret.shift()
    moment = (ret.pow(2) * lag - ret * lag.pow(2)).rolling(
        int(window), min_periods=int(window)).mean()
    scale = ret.rolling(int(window), min_periods=int(window)).std(ddof=0).pow(3)
    return moment / scale.replace(0.0, np.nan), ret


def sig_time_reversal_asymmetry_long_1h(d, p, ctx):
    """C89: follow positive drift with positive nonlinear temporal asymmetry."""
    asym, log_ret = _time_reversal_asymmetry(d.c, int(p['window']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((asym >= float(p['min_entry'])) & (log_ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(asym <= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['time_reversal_asymmetry_long_1h'] = dict(
    name='[RESEARCH] Time-reversal asymmetry long 1h', style='nonlinear_time_irreversibility',
    fn=sig_time_reversal_asymmetry_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(TRA1H),
    desc='Candidate 89: follow positive drift with positive standardized third-order return asymmetry.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


SAMPEN1H = dict(window=48, template_length=2, tolerance=0.20,
                max_entry=0.80, exit_level=1.20, cost_multiple=5.0)


def _sample_entropy_value(values, template_length=2, tolerance=0.20):
    """Magnitude-aware Sample Entropy with Chebyshev template matching."""
    x = np.asarray(values, dtype=float)
    m = int(template_length)
    if len(x) <= m + 1 or not np.isfinite(x).all():
        return np.nan
    scale = np.std(x, ddof=0)
    if not np.isfinite(scale) or scale <= 0.0:
        return np.nan
    radius = float(tolerance) * scale

    def matches(length):
        templates = np.lib.stride_tricks.sliding_window_view(x, length)
        count = 0
        for pos in range(len(templates) - 1):
            count += np.all(
                np.abs(templates[pos + 1:] - templates[pos]) <= radius,
                axis=1).sum()
        return count

    base, extended = matches(m), matches(m + 1)
    if base <= 0 or extended <= 0:
        return np.inf
    return -np.log(float(extended) / float(base))


def _rolling_sample_entropy(close, window, template_length, tolerance):
    ret = np.log(close.astype(float) / close.astype(float).shift())
    entropy = ret.rolling(int(window), min_periods=int(window)).apply(
        lambda x: _sample_entropy_value(x, template_length, tolerance), raw=True)
    return entropy, ret


def sig_sample_entropy_long_1h(d, p, ctx):
    """C90: follow positive drift when return magnitudes repeat predictably."""
    entropy, log_ret = _rolling_sample_entropy(
        d.c, int(p['window']), int(p['template_length']), float(p['tolerance']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((entropy <= float(p['max_entry'])) & (log_ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(entropy >= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['sample_entropy_long_1h'] = dict(
    name='[RESEARCH] Sample-entropy long 1h', style='magnitude_sequence_complexity',
    fn=sig_sample_entropy_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(SAMPEN1H),
    desc='Candidate 90: follow positive drift when 48h magnitude-aware Sample Entropy is at most 0.80.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CS_SPREAD1H = dict(window=24, max_entry=0.003, exit_level=0.006, cost_multiple=5.0)


def _corwin_schultz_spread(high, low, window):
    """Rolling median two-bar Corwin-Schultz OHLC implied-spread estimate."""
    high, low = high.astype(float), low.astype(float)
    hl2 = np.log(high / low).pow(2)
    beta = hl2 + hl2.shift()
    two_high = pd.concat([high, high.shift()], axis=1).max(axis=1)
    two_low = pd.concat([low, low.shift()], axis=1).min(axis=1)
    gamma = np.log(two_high / two_low).pow(2)
    denom = 3.0 - 2.0 * np.sqrt(2.0)
    alpha = ((np.sqrt(2.0 * beta) - np.sqrt(beta)) / denom -
             np.sqrt(gamma / denom)).clip(lower=0.0, upper=50.0)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return spread.rolling(int(window), min_periods=int(window)).median()


def sig_corwin_schultz_spread_long_1h(d, p, ctx):
    """C91: follow positive drift only in a tight OHLC-implied spread regime."""
    spread = _corwin_schultz_spread(d.h, d.l, int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((spread <= float(p['max_entry'])) & (ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(spread >= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['corwin_schultz_spread_long_1h'] = dict(
    name='[RESEARCH] Corwin-Schultz tight-spread long 1h', style='ohlc_implied_liquidity',
    fn=sig_corwin_schultz_spread_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CS_SPREAD1H),
    desc='Candidate 91: follow positive drift when the 24h median OHLC-implied spread is at most 30bp.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


MADZ1H = dict(window=48, max_entry=-2.5, exit_level=-0.5, cost_multiple=5.0)


def _rolling_mad_zscore(close, window):
    """Causal robust z-score of log price using rolling median and scaled MAD."""
    log_price = np.log(close.astype(float))
    median = log_price.rolling(int(window), min_periods=int(window)).median()
    mad = log_price.rolling(int(window), min_periods=int(window)).apply(
        lambda x: np.median(np.abs(x - np.median(x))), raw=True)
    scale = 1.4826 * mad
    return (log_price - median) / scale.replace(0.0, np.nan)


def sig_robust_location_reversion_long_1h(d, p, ctx):
    """C92: buy a positive reversal from an extreme median/MAD price displacement."""
    zscore = _rolling_mad_zscore(d.c, int(p['window']))
    ret = d.c.pct_change()
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((zscore <= float(p['max_entry'])) & (ret > 0.0) &
          (d.c < d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(zscore >= float(p['exit_level'])) | (d.c >= d.e20), sx=never)


STRATEGIES['robust_location_reversion_long_1h'] = dict(
    name='[RESEARCH] Robust-location reversion long 1h', style='median_mad_dislocation',
    fn=sig_robust_location_reversion_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(MADZ1H),
    desc='Candidate 92: reverse from a 48h log-price median/MAD z-score at or below -2.5.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


AR2F1H = dict(window=168, min_entry=0.25, exit_level=0.0, cost_multiple=5.0)


def _ar2_forecast_z(values):
    """OLS AR(2) next-return forecast standardized by in-window return scale."""
    x = np.asarray(values, dtype=float)
    if len(x) < 6 or not np.isfinite(x).all():
        return np.nan
    y = x[2:]
    design = np.column_stack([np.ones(len(y)), x[1:-1], x[:-2]])
    try:
        coef = np.linalg.lstsq(design, y, rcond=None)[0]
    except np.linalg.LinAlgError:
        return np.nan
    scale = np.std(y, ddof=0)
    if not np.isfinite(scale) or scale <= 0.0:
        return np.nan
    return float(np.dot(np.array([1.0, x[-1], x[-2]]), coef) / scale)


def _rolling_ar2_forecast(close, window):
    ret = np.log(close.astype(float) / close.astype(float).shift())
    forecast = ret.rolling(int(window), min_periods=int(window)).apply(
        _ar2_forecast_z, raw=True)
    return forecast, ret


def sig_ar2_forecast_long_1h(d, p, ctx):
    """C93: follow positive drift when a causal AR(2) forecasts continuation."""
    forecast, log_ret = _rolling_ar2_forecast(d.c, int(p['window']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((forecast >= float(p['min_entry'])) & (log_ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(forecast <= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['ar2_forecast_long_1h'] = dict(
    name='[RESEARCH] AR(2) forecast long 1h', style='two_lag_autoregressive_forecast',
    fn=sig_ar2_forecast_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(AR2F1H),
    desc='Candidate 93: follow positive drift when a 168h causal AR(2) forecasts at least 0.25 sigma.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


RECHI1H = dict(record_lookback=24, window=48, min_entry=0.10,
               exit_level=0.02, cost_multiple=5.0)


def _record_high_intensity(close, record_lookback, window):
    """Fraction of recent completed bars that set a strict trailing close record."""
    prior_high = close.astype(float).shift().rolling(
        int(record_lookback), min_periods=int(record_lookback)).max()
    records = (close.astype(float) > prior_high).astype(float).where(prior_high.notna())
    return records.rolling(int(window), min_periods=int(window)).mean()


def sig_record_high_intensity_long_1h(d, p, ctx):
    """C94: follow positive drift when new closing highs arrive persistently."""
    intensity = _record_high_intensity(
        d.c, int(p['record_lookback']), int(p['window']))
    ret = d.c.pct_change()
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((intensity >= float(p['min_entry'])) & (ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(intensity <= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['record_high_intensity_long_1h'] = dict(
    name='[RESEARCH] Closing-record intensity long 1h', style='record_statistics_persistence',
    fn=sig_record_high_intensity_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RECHI1H),
    desc='Candidate 94: follow positive drift when at least 10% of 48h bars set a strict 24h closing record.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


RSHIFT1H = dict(recent=24, baseline=72, min_entry=0.60,
                exit_level=0.50, cost_multiple=5.0)


def _rank_shift_dominance(values, recent=24, baseline=72):
    """Mann-Whitney probability that a recent return exceeds a baseline return."""
    x = np.asarray(values, dtype=float)
    nr, nb = int(recent), int(baseline)
    if len(x) != nr + nb or not np.isfinite(x).all():
        return np.nan
    old, new = x[:nb], x[nb:]
    diff = new[:, None] - old[None, :]
    return float((np.sum(diff > 0.0) + 0.5 * np.sum(diff == 0.0)) / diff.size)


def _rolling_rank_shift(close, recent, baseline):
    ret = np.log(close.astype(float) / close.astype(float).shift())
    total = int(recent) + int(baseline)
    dominance = ret.rolling(total, min_periods=total).apply(
        lambda x: _rank_shift_dominance(x, recent, baseline), raw=True)
    return dominance, ret


def sig_return_rank_shift_long_1h(d, p, ctx):
    """C95: follow a nonparametric upward shift in the return distribution."""
    dominance, log_ret = _rolling_rank_shift(
        d.c, int(p['recent']), int(p['baseline']))
    ret24 = d.c.pct_change(24)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = ((dominance >= float(p['min_entry'])) & (log_ret > 0.0) & (ret24 > 0.0) &
          (d.c > d.e20) & cost_ok)
    never = d.c < -1
    return dict(le=le, se=never,
                lx=(dominance <= float(p['exit_level'])) | (ret24 <= 0.0), sx=never)


STRATEGIES['return_rank_shift_long_1h'] = dict(
    name='[RESEARCH] Return rank-shift long 1h', style='mann_whitney_distribution_shift',
    fn=sig_return_rank_shift_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RSHIFT1H),
    desc='Candidate 95: follow positive drift when recent 24h returns dominate the prior 72h sample at 60%.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


PSAR1H = dict(af_step=0.02, af_max=0.20, cost_multiple=5.0)


def _parabolic_sar(high, low, close, af_step=0.02, af_max=0.20):
    """Causal Wilder Parabolic SAR and trend state on completed bars."""
    h, l, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    n = len(c)
    sar = np.full(n, np.nan, dtype=float)
    bull_state = np.zeros(n, dtype=bool)
    if n < 2:
        return pd.Series(sar, index=close.index), pd.Series(bull_state, index=close.index)
    step, cap = float(af_step), float(af_max)
    bull = bool(c[1] >= c[0])
    ep = max(h[0], h[1]) if bull else min(l[0], l[1])
    af = step
    sar[0] = l[0] if bull else h[0]
    sar[1] = sar[0]
    bull_state[:2] = bull
    for i in range(2, n):
        candidate = sar[i - 1] + af * (ep - sar[i - 1])
        if bull:
            candidate = min(candidate, l[i - 1], l[i - 2])
            if l[i] < candidate:
                bull, sar[i], ep, af = False, ep, l[i], step
            else:
                sar[i] = candidate
                if h[i] > ep:
                    ep, af = h[i], min(cap, af + step)
        else:
            candidate = max(candidate, h[i - 1], h[i - 2])
            if h[i] > candidate:
                bull, sar[i], ep, af = True, ep, h[i], step
            else:
                sar[i] = candidate
                if l[i] < ep:
                    ep, af = l[i], min(cap, af + step)
        bull_state[i] = bull
    return pd.Series(sar, index=close.index), pd.Series(bull_state, index=close.index)


def sig_parabolic_sar_flip_long_1h(d, p, ctx):
    """C96: enter on a Wilder Parabolic SAR bearish-to-bullish state flip."""
    _, bull = _parabolic_sar(d.h, d.l, d.c, p['af_step'], p['af_max'])
    flip_up = bull & ~bull.shift(fill_value=False)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = flip_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=~bull, sx=never)


STRATEGIES['parabolic_sar_flip_long_1h'] = dict(
    name='[RESEARCH] Parabolic SAR bullish flip long 1h', style='accelerating_extreme_point_trend',
    fn=sig_parabolic_sar_flip_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(PSAR1H),
    desc='Candidate 96: follow a causal Wilder Parabolic SAR bearish-to-bullish flip above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


ICHI1H = dict(conversion=9, base=26, span=52, cost_multiple=5.0)


def _rolling_midrange(high, low, window):
    n = int(window)
    return (high.rolling(n, min_periods=n).max() + low.rolling(n, min_periods=n).min()) / 2.0


def sig_ichimoku_cloud_cross_long_1h(d, p, ctx):
    """C97: causal bullish conversion/base cross above a positive range-equilibrium cloud."""
    conversion = _rolling_midrange(d.h, d.l, p['conversion'])
    base = _rolling_midrange(d.h, d.l, p['base'])
    span_a = (conversion + base) / 2.0
    span_b = _rolling_midrange(d.h, d.l, p['span'])
    cloud_upper = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_lower = pd.concat([span_a, span_b], axis=1).min(axis=1)
    cross_up = (conversion > base) & (conversion.shift() <= base.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (span_a > span_b) & (d.c > cloud_upper) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(conversion < base) | (d.c < cloud_lower), sx=never)


STRATEGIES['ichimoku_cloud_cross_long_1h'] = dict(
    name='[RESEARCH] Causal Ichimoku cloud cross long 1h', style='dual_midrange_equilibrium',
    fn=sig_ichimoku_cloud_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ICHI1H),
    desc='Candidate 97: bullish 9/26 range-midpoint cross above a positive causal 26/52 cloud.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


RVI1H = dict(window=10, cost_multiple=5.0)


def _weighted_four(series):
    return (series + 2.0 * series.shift(1) + 2.0 * series.shift(2) + series.shift(3)) / 6.0


def _relative_vigor(open_, high, low, close, window=10):
    """Classic causal Relative Vigor Index and four-bar signal line."""
    numerator = _weighted_four(close.astype(float) - open_.astype(float))
    denominator = _weighted_four((high.astype(float) - low.astype(float)).replace(0.0, np.nan))
    raw = numerator / denominator
    rvi = raw.rolling(int(window), min_periods=int(window)).mean()
    return rvi, _weighted_four(rvi)


def sig_relative_vigor_cross_long_1h(d, p, ctx):
    """C98: enter when normalized candle-body pressure crosses bullish."""
    rvi, signal = _relative_vigor(d.o, d.h, d.l, d.c, p['window'])
    cross_up = (rvi > signal) & (rvi.shift() <= signal.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (rvi > 0.0) & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(rvi < signal) | (rvi <= 0.0), sx=never)


STRATEGIES['relative_vigor_cross_long_1h'] = dict(
    name='[RESEARCH] Relative Vigor bullish cross long 1h', style='normalized_body_pressure',
    fn=sig_relative_vigor_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RVI1H),
    desc='Candidate 98: classic 10h Relative Vigor Index bullish signal cross above zero and EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


UO1H = dict(fast=7, medium=14, slow=28, level=50.0, cost_multiple=5.0)


def _ultimate_oscillator(high, low, close, fast=7, medium=14, slow=28):
    """Causal Larry Williams Ultimate Oscillator on completed bars."""
    prev_close = close.astype(float).shift()
    true_low = pd.concat([low.astype(float), prev_close], axis=1).min(axis=1)
    true_high = pd.concat([high.astype(float), prev_close], axis=1).max(axis=1)
    buying_pressure = close.astype(float) - true_low
    true_range = (true_high - true_low).replace(0.0, np.nan)
    def ratio(n):
        return (buying_pressure.rolling(int(n), min_periods=int(n)).sum() /
                true_range.rolling(int(n), min_periods=int(n)).sum())
    return 100.0 * (4.0 * ratio(fast) + 2.0 * ratio(medium) + ratio(slow)) / 7.0


def sig_ultimate_oscillator_cross_long_1h(d, p, ctx):
    """C99: enter when gap-aware multi-horizon buying pressure crosses bullish."""
    uo = _ultimate_oscillator(d.h, d.l, d.c, p['fast'], p['medium'], p['slow'])
    level = float(p['level'])
    cross_up = (uo > level) & (uo.shift() <= level)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=uo < level, sx=never)


STRATEGIES['ultimate_oscillator_cross_long_1h'] = dict(
    name='[RESEARCH] Ultimate Oscillator bullish cross long 1h', style='multihorizon_buying_pressure',
    fn=sig_ultimate_oscillator_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(UO1H),
    desc='Candidate 99: classic gap-aware 7/14/28 Ultimate Oscillator cross above 50 and EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


DEM1H = dict(window=14, entry=0.30, exit=0.70, cost_multiple=5.0)


def _demarker(high, low, window=14):
    """Causal DeMarker oscillator from incremental completed-bar extrema."""
    de_max = (high.astype(float) - high.astype(float).shift()).clip(lower=0.0)
    de_min = (low.astype(float).shift() - low.astype(float)).clip(lower=0.0)
    n = int(window)
    up = de_max.rolling(n, min_periods=n).sum()
    down = de_min.rolling(n, min_periods=n).sum()
    return up / (up + down).replace(0.0, np.nan)


def sig_demarker_recovery_long_1h(d, p, ctx):
    """C100: enter when incremental-extrema pressure recovers from oversold."""
    dem = _demarker(d.h, d.l, p['window'])
    entry, exit_ = float(p['entry']), float(p['exit'])
    cross_up = (dem > entry) & (dem.shift() <= entry)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(dem >= exit_) | (dem < entry), sx=never)


STRATEGIES['demarker_recovery_long_1h'] = dict(
    name='[RESEARCH] DeMarker oversold recovery long 1h', style='incremental_extrema_pressure',
    fn=sig_demarker_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(DEM1H),
    desc='Candidate 100: classic DeMarker(14) recovery above 0.30 with 0.70 exit and EMA20 filter.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


KAL1H = dict(process_var=0.000025, observation_var=0.0004,
             shock=-2.0, recovery=-1.0, cost_multiple=5.0)


def _kalman_local_level_score(close, process_var=0.000025, observation_var=0.0004):
    """Causal scalar local-level Kalman innovations on log price."""
    z = np.log(np.asarray(close, dtype=float))
    score = np.full(len(z), np.nan, dtype=float)
    if not len(z):
        return pd.Series(score, index=close.index)
    q, r = float(process_var), float(observation_var)
    state, variance = z[0], r
    for i in range(1, len(z)):
        prior_var = variance + q
        innovation_var = prior_var + r
        innovation = z[i] - state
        score[i] = innovation / np.sqrt(innovation_var)
        gain = prior_var / innovation_var
        state += gain * innovation
        variance = (1.0 - gain) * prior_var
    return pd.Series(score, index=close.index)


def sig_kalman_innovation_recovery_long_1h(d, p, ctx):
    """C101: recover from a standardized downside innovation to a causal latent level."""
    score = _kalman_local_level_score(d.c, p['process_var'], p['observation_var'])
    ret = d.c.pct_change()
    recovering = ((score.shift() <= float(p['shock'])) &
                  (score > float(p['recovery'])) & (ret > 0.0))
    cost_ok = 1.5 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = recovering & (d.c < d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=score >= 0.0, sx=never)


STRATEGIES['kalman_innovation_recovery_long_1h'] = dict(
    name='[RESEARCH] Kalman innovation recovery long 1h', style='state_space_dislocation',
    fn=sig_kalman_innovation_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(KAL1H),
    desc='Candidate 101: recover from a <=-2 sigma local-level innovation to >-1 while below EMA20.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=8))


ERAY1H = dict(ema_span=13, cost_multiple=5.0)


def _elder_ray(high, low, close, span=13):
    """Causal Elder Ray bull and bear power around EMA."""
    ema = close.astype(float).ewm(span=int(span), adjust=False, min_periods=int(span)).mean()
    return ema, high.astype(float) - ema, low.astype(float) - ema


def sig_elder_ray_pullback_long_1h(d, p, ctx):
    """C102: buy improving but still-negative bear power inside a rising bull trend."""
    ema, bull_power, bear_power = _elder_ray(d.h, d.l, d.c, p['ema_span'])
    bear_improving = (bear_power > bear_power.shift()) & (bear_power.shift() > bear_power.shift(2))
    trend = (ema > ema.shift()) & (bull_power > 0.0) & (bear_power < 0.0) & (d.c > ema)
    cost_ok = 1.5 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = bear_improving & trend & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=(bear_power >= 0.0) | (ema <= ema.shift()), sx=never)


STRATEGIES['elder_ray_pullback_long_1h'] = dict(
    name='[RESEARCH] Elder Ray improving-bear-power long 1h', style='extrema_power_pullback',
    fn=sig_elder_ray_pullback_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ERAY1H),
    desc='Candidate 102: rising EMA13, positive bull power, and two increases in still-negative bear power.',
    mgmt=dict(stop_atr=1.5, tp_r=1.5, max_bars=12))


NVI1H = dict(ema_span=255, cost_multiple=5.0)


def _negative_volume_index(close, quote_volume):
    """Causal NVI: compound returns only when completed quote volume declines."""
    log_ret = np.log(close.astype(float) / close.astype(float).shift())
    selected = log_ret.where(quote_volume.astype(float) < quote_volume.astype(float).shift(), 0.0).fillna(0.0)
    return 1000.0 * np.exp(selected.cumsum())


def sig_negative_volume_index_long_1h(d, p, ctx):
    """C103: follow selective low-volume price accumulation crossing its slow mean."""
    nvi = _negative_volume_index(d.c, d.qv)
    signal = nvi.ewm(span=int(p['ema_span']), adjust=False, min_periods=int(p['ema_span'])).mean()
    cross_up = (nvi > signal) & (nvi.shift() <= signal.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=nvi < signal, sx=never)


STRATEGIES['negative_volume_index_long_1h'] = dict(
    name='[RESEARCH] Negative Volume Index crossover long 1h', style='selective_low_volume_accumulation',
    fn=sig_negative_volume_index_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(NVI1H),
    desc='Candidate 103: low-volume-only price accumulation crosses above its 255h EMA with price above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


TRIX1H = dict(span=15, cost_multiple=5.0)


def _trix(close, span=15):
    """Causal triple-exponential one-bar rate of change."""
    n = int(span)
    e1 = close.astype(float).ewm(span=n, adjust=False, min_periods=n).mean()
    e2 = e1.ewm(span=n, adjust=False, min_periods=n).mean()
    e3 = e2.ewm(span=n, adjust=False, min_periods=n).mean()
    return 100.0 * e3.pct_change()


def sig_trix_zero_cross_long_1h(d, p, ctx):
    """C104: follow a positive transition in triple-smoothed price momentum."""
    trix = _trix(d.c, p['span'])
    cross_up = (trix > 0.0) & (trix.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=trix <= 0.0, sx=never)


STRATEGIES['trix_zero_cross_long_1h'] = dict(
    name='[RESEARCH] TRIX zero-cross long 1h', style='triple_smoothed_momentum',
    fn=sig_trix_zero_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(TRIX1H),
    desc='Candidate 104: conventional TRIX(15) crosses above zero with price above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


HA1H = dict(cost_multiple=5.0)


def _heikin_ashi(o, h, l, c):
    """Return causal Heikin-Ashi open/close series from completed ordinary candles."""
    hc = (o.astype(float) + h.astype(float) + l.astype(float) + c.astype(float)) / 4.0
    ho = pd.Series(np.nan, index=c.index, dtype=float)
    if len(ho):
        ho.iloc[0] = (float(o.iloc[0]) + float(c.iloc[0])) / 2.0
        for i in range(1, len(ho)):
            ho.iloc[i] = (float(ho.iloc[i - 1]) + float(hc.iloc[i - 1])) / 2.0
    return ho, hc


def sig_heikin_ashi_flip_long_1h(d, p, ctx):
    """C105: enter on a bearish-to-bullish recursive transformed-candle transition."""
    ha_open, ha_close = _heikin_ashi(d.o, d.h, d.l, d.c)
    bullish = ha_close > ha_open
    cross_up = bullish & ~bullish.shift(1, fill_value=False)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=~bullish, sx=never)


STRATEGIES['heikin_ashi_flip_long_1h'] = dict(
    name='[RESEARCH] Heikin-Ashi state flip long 1h', style='transformed_candle_state',
    fn=sig_heikin_ashi_flip_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(HA1H),
    desc='Candidate 105: recursive Heikin-Ashi state flips bearish to bullish while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


FISHER1H = dict(lookback=10, cost_multiple=5.0)


def _fisher_transform(high, low, lookback=10):
    """Causal conventional Ehlers Fisher transform of rolling median-price location."""
    price = (high.astype(float) + low.astype(float)) / 2.0
    lo, hi = price.rolling(lookback).min(), price.rolling(lookback).max()
    loc = 2.0 * ((price - lo) / (hi - lo).replace(0.0, np.nan) - 0.5)
    value = pd.Series(np.nan, index=price.index, dtype=float)
    fisher = pd.Series(np.nan, index=price.index, dtype=float)
    prior_value = prior_fisher = 0.0
    for i, raw in enumerate(loc.to_numpy(float)):
        if not np.isfinite(raw):
            continue
        prior_value = float(np.clip(0.33 * raw + 0.67 * prior_value, -0.999, 0.999))
        prior_fisher = 0.5 * np.log((1.0 + prior_value) / (1.0 - prior_value)) + 0.5 * prior_fisher
        value.iloc[i], fisher.iloc[i] = prior_value, prior_fisher
    return fisher


def sig_fisher_zero_cross_long_1h(d, p, ctx):
    """C106: follow a positive transition in recursively Gaussianized price location."""
    fisher = _fisher_transform(d.h, d.l, int(p['lookback']))
    cross_up = (fisher > 0.0) & (fisher.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=fisher <= 0.0, sx=never)


STRATEGIES['fisher_zero_cross_long_1h'] = dict(
    name='[RESEARCH] Fisher transform zero-cross long 1h', style='gaussianized_price_location',
    fn=sig_fisher_zero_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(FISHER1H),
    desc='Candidate 106: conventional Ehlers Fisher(10) crosses above zero with price above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


COPPOCK1H = dict(roc_slow=14, roc_fast=11, wma=10, cost_multiple=5.0)


def _coppock_curve(close, roc_slow=14, roc_fast=11, wma=10):
    """Causal conventional Coppock curve: WMA of the 14- and 11-bar ROC sum."""
    raw = 100.0 * (close.astype(float).pct_change(roc_slow) + close.astype(float).pct_change(roc_fast))
    weights = np.arange(1.0, float(wma) + 1.0)
    return raw.rolling(wma).apply(lambda x: float(np.dot(x, weights) / weights.sum()), raw=True)


def sig_coppock_zero_cross_long_1h(d, p, ctx):
    """C107: follow a positive recovery in smoothed dual-horizon rate of change."""
    curve = _coppock_curve(d.c, int(p['roc_slow']), int(p['roc_fast']), int(p['wma']))
    cross_up = (curve > 0.0) & (curve.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=curve <= 0.0, sx=never)


STRATEGIES['coppock_zero_cross_long_1h'] = dict(
    name='[RESEARCH] Coppock zero-cross long 1h', style='dual_horizon_roc_recovery',
    fn=sig_coppock_zero_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(COPPOCK1H),
    desc='Candidate 107: conventional Coppock 14/11 ROC sum smoothed by WMA10 crosses above zero above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


STC1H = dict(fast=23, slow=50, cycle=10, alpha=0.5, cost_multiple=5.0)


def _recursive_smooth(series, alpha):
    out = pd.Series(np.nan, index=series.index, dtype=float)
    prior = np.nan
    for i, raw in enumerate(series.to_numpy(float)):
        if not np.isfinite(raw):
            continue
        prior = raw if not np.isfinite(prior) else alpha * raw + (1.0 - alpha) * prior
        out.iloc[i] = prior
    return out


def _schaff_trend_cycle(close, fast=23, slow=50, cycle=10, alpha=0.5):
    """Causal Schaff Trend Cycle: MACD passed through two smoothed stochastic transforms."""
    c = close.astype(float)
    macd = c.ewm(span=fast, adjust=False).mean() - c.ewm(span=slow, adjust=False).mean()
    lo1, hi1 = macd.rolling(cycle).min(), macd.rolling(cycle).max()
    k1 = 100.0 * (macd - lo1) / (hi1 - lo1).replace(0.0, np.nan)
    d1 = _recursive_smooth(k1, alpha)
    lo2, hi2 = d1.rolling(cycle).min(), d1.rolling(cycle).max()
    k2 = 100.0 * (d1 - lo2) / (hi2 - lo2).replace(0.0, np.nan)
    return _recursive_smooth(k2, alpha)


def sig_schaff_cycle_long_1h(d, p, ctx):
    """C108: enter an early dual-stochastic MACD cycle transition."""
    stc = _schaff_trend_cycle(d.c, int(p['fast']), int(p['slow']), int(p['cycle']), float(p['alpha']))
    cross_up = (stc > 25.0) & (stc.shift() <= 25.0)
    cross_down = (stc < 75.0) & (stc.shift() >= 75.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=cross_down, sx=never)


STRATEGIES['schaff_cycle_long_1h'] = dict(
    name='[RESEARCH] Schaff Trend Cycle long 1h', style='dual_stochastic_macd_cycle',
    fn=sig_schaff_cycle_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(STC1H),
    desc='Candidate 108: conventional 23/50/10 Schaff cycle crosses 25 above EMA20; exit on downward 75 cross.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


DPO1H = dict(lookback=20, displacement=11, cost_multiple=5.0)


def _detrended_price(close, lookback=20, displacement=11):
    """Causal DPO: current close less a trailing SMA observed `displacement` bars earlier."""
    return close.astype(float) - close.astype(float).rolling(lookback).mean().shift(displacement)


def sig_dpo_zero_cross_long_1h(d, p, ctx):
    """C109: follow recovery above a lag-displaced local price baseline."""
    dpo = _detrended_price(d.c, int(p['lookback']), int(p['displacement']))
    cross_up = (dpo > 0.0) & (dpo.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=dpo <= 0.0, sx=never)


STRATEGIES['dpo_zero_cross_long_1h'] = dict(
    name='[RESEARCH] Detrended-price zero-cross long 1h', style='lag_displaced_price_residual',
    fn=sig_dpo_zero_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(DPO1H),
    desc='Candidate 109: DPO(20) against the conventional 11-bar-displaced SMA crosses above zero above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


FRAMA1H = dict(window=16, alpha_floor=0.01, cost_multiple=5.0)


def _frama(high, low, close, window=16, alpha_floor=0.01):
    """Causal Fractal Adaptive Moving Average using two half-window path ranges."""
    if window < 4 or window % 2: raise ValueError('FRAMA window must be even and >= 4')
    h, l, c = high.to_numpy(float), low.to_numpy(float), close.to_numpy(float)
    out = np.full(len(c), np.nan); half = window // 2; prior = np.nan
    for i in range(window - 1, len(c)):
        start, mid = i - window + 1, i - half + 1
        n1 = (np.max(h[start:mid]) - np.min(l[start:mid])) / half
        n2 = (np.max(h[mid:i + 1]) - np.min(l[mid:i + 1])) / half
        n3 = (np.max(h[start:i + 1]) - np.min(l[start:i + 1])) / window
        if n1 + n2 > 0.0 and n3 > 0.0:
            dimension = (np.log(n1 + n2) - np.log(n3)) / np.log(2.0)
            alpha = float(np.clip(np.exp(-4.6 * (dimension - 1.0)), alpha_floor, 1.0))
        else:
            alpha = 1.0
        prior = c[i] if not np.isfinite(prior) else alpha * c[i] + (1.0 - alpha) * prior
        out[i] = prior
    return pd.Series(out, index=close.index)


def sig_frama_cross_long_1h(d, p, ctx):
    """C110: enter a close transition above a path-dimension adaptive baseline."""
    frama = _frama(d.h, d.l, d.c, int(p['window']), float(p['alpha_floor']))
    cross_up = (d.c > frama) & (d.c.shift() <= frama.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=d.c <= frama, sx=never)


STRATEGIES['frama_cross_long_1h'] = dict(
    name='[RESEARCH] FRAMA crossover long 1h', style='fractal_adaptive_trend',
    fn=sig_frama_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(FRAMA1H),
    desc='Candidate 110: close crosses above a conventional FRAMA(16) path-dimension adaptive baseline above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


VHF1H = dict(lookback=28, enter=0.40, exit=0.20, cost_multiple=5.0)


def _vertical_horizontal_filter(close, lookback=28):
    """Directional close range divided by cumulative close-to-close path length."""
    c = close.astype(float)
    direction = c.rolling(lookback).max() - c.rolling(lookback).min()
    path = c.diff().abs().rolling(lookback).sum()
    return direction / path.replace(0.0, np.nan)


def sig_vhf_trend_onset_long_1h(d, p, ctx):
    """C111: enter when directional efficiency transitions into a trending state."""
    vhf = _vertical_horizontal_filter(d.c, int(p['lookback']))
    cross_up = (vhf > float(p['enter'])) & (vhf.shift() <= float(p['enter']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never,
                lx=(vhf < float(p['exit'])) | (d.c <= d.e20), sx=never)


STRATEGIES['vhf_trend_onset_long_1h'] = dict(
    name='[RESEARCH] VHF trend-onset long 1h', style='directional_path_efficiency',
    fn=sig_vhf_trend_onset_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(VHF1H),
    desc='Candidate 111: VHF(28) crosses above 0.40 above EMA20; exits below 0.20 or EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


COG1H = dict(lookback=10, cost_multiple=5.0)


def _center_of_gravity(high, low, lookback=10):
    """Causal Ehlers Center-of-Gravity oscillator on trailing median prices."""
    price = (high.astype(float) + low.astype(float)) / 2.0
    numerator = sum(float(i + 1) * price.shift(i) for i in range(lookback))
    denominator = sum(price.shift(i) for i in range(lookback))
    return -numerator / denominator.replace(0.0, np.nan) + (float(lookback) + 1.0) / 2.0


def sig_cog_zero_cross_long_1h(d, p, ctx):
    """C112: enter when the trailing price balance point shifts positive."""
    cog = _center_of_gravity(d.h, d.l, int(p['lookback']))
    cross_up = (cog > 0.0) & (cog.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=cog <= 0.0, sx=never)


STRATEGIES['cog_zero_cross_long_1h'] = dict(
    name='[RESEARCH] Center-of-Gravity zero-cross long 1h', style='price_balance_point',
    fn=sig_cog_zero_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(COG1H),
    desc='Candidate 112: conventional Ehlers median-price COG(10) crosses above zero above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


RVOL1H = dict(std_window=10, smooth=14, cost_multiple=5.0)


def _relative_volatility_index(close, std_window=10, smooth=14):
    """Directional volatility ratio using rolling close dispersion and Wilder smoothing."""
    c = close.astype(float); sigma = c.rolling(std_window).std(ddof=0); delta = c.diff()
    up = sigma.where(delta > 0.0, 0.0); down = sigma.where(delta < 0.0, 0.0)
    up_avg = up.ewm(alpha=1.0 / smooth, adjust=False, min_periods=smooth).mean()
    down_avg = down.ewm(alpha=1.0 / smooth, adjust=False, min_periods=smooth).mean()
    return 100.0 * up_avg / (up_avg + down_avg).replace(0.0, np.nan)


def sig_relative_volatility_long_1h(d, p, ctx):
    """C113: enter when volatility becomes dominated by advancing closes."""
    rvi = _relative_volatility_index(d.c, int(p['std_window']), int(p['smooth']))
    cross_up = (rvi > 50.0) & (rvi.shift() <= 50.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=rvi <= 50.0, sx=never)


STRATEGIES['relative_volatility_long_1h'] = dict(
    name='[RESEARCH] Relative Volatility Index long 1h', style='directional_volatility_balance',
    fn=sig_relative_volatility_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RVOL1H),
    desc='Candidate 113: directional close-volatility RVI(10,14) crosses above 50 above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CFO1H = dict(lookback=14, cost_multiple=5.0)


def _chande_forecast_oscillator(close, lookback=14):
    """Current-price residual from a causal rolling OLS endpoint, normalized by price."""
    x = np.arange(float(lookback)); xm = x.mean(); denom = float(np.sum((x - xm) ** 2))
    def endpoint(y):
        ym = float(np.mean(y)); slope = float(np.sum((x - xm) * (y - ym)) / denom)
        return ym + slope * (float(lookback - 1) - xm)
    forecast = close.astype(float).rolling(lookback).apply(endpoint, raw=True)
    return 100.0 * (close.astype(float) - forecast) / close.astype(float).replace(0.0, np.nan)


def sig_cfo_zero_cross_long_1h(d, p, ctx):
    """C114: enter when price shifts above its rolling regression endpoint."""
    cfo = _chande_forecast_oscillator(d.c, int(p['lookback']))
    cross_up = (cfo > 0.0) & (cfo.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=cfo <= 0.0, sx=never)


STRATEGIES['cfo_zero_cross_long_1h'] = dict(
    name='[RESEARCH] Chande Forecast Oscillator long 1h', style='regression_endpoint_residual',
    fn=sig_cfo_zero_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CFO1H),
    desc='Candidate 114: CFO(14) normalized rolling-regression endpoint residual crosses above zero above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


RWI1H = dict(max_period=14, threshold=1.0, cost_multiple=5.0)


def _random_walk_index(high, low, close, max_period=14):
    """Causal high/low Random Walk Index maximized across horizons 2..max_period."""
    prev = close.astype(float).shift()
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    highs, lows = [], []
    for n in range(2, max_period + 1):
        scale = tr.rolling(n).mean() * np.sqrt(float(n))
        highs.append((high.astype(float) - low.astype(float).shift(n - 1)) / scale.replace(0.0, np.nan))
        lows.append((high.astype(float).shift(n - 1) - low.astype(float)) / scale.replace(0.0, np.nan))
    return pd.concat(highs, axis=1).max(axis=1), pd.concat(lows, axis=1).max(axis=1)


def sig_random_walk_index_long_1h(d, p, ctx):
    """C115: enter when upward displacement becomes inconsistent with a random walk."""
    rhi, rlo = _random_walk_index(d.h, d.l, d.c, int(p['max_period']))
    level = float(p['threshold']); cross_up = (rhi > level) & (rhi.shift() <= level)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (rhi > rlo) & (d.c > d.e20) & cost_ok, se=never,
                lx=(rhi <= level) | (rhi <= rlo), sx=never)


STRATEGIES['random_walk_index_long_1h'] = dict(
    name='[RESEARCH] Random Walk Index long 1h', style='atr_scaled_directional_displacement',
    fn=sig_random_walk_index_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(RWI1H),
    desc='Candidate 115: max 2-14 bar RWI-high crosses 1, dominates RWI-low, and close is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


LRSI1H = dict(gamma=0.5, cost_multiple=5.0)


def _laguerre_rsi(close, gamma=0.5):
    """Causal four-stage Ehlers Laguerre RSI."""
    c = close.to_numpy(float); out = np.full(len(c), np.nan)
    l0 = l1 = l2 = l3 = np.nan
    for i, price in enumerate(c):
        if not np.isfinite(l0):
            l0 = l1 = l2 = l3 = price; out[i] = 0.5; continue
        p0, p1, p2, p3 = l0, l1, l2, l3
        l0 = (1.0 - gamma) * price + gamma * p0
        l1 = -gamma * l0 + p0 + gamma * p1
        l2 = -gamma * l1 + p1 + gamma * p2
        l3 = -gamma * l2 + p2 + gamma * p3
        cu = cd = 0.0
        for a, b in ((l0, l1), (l1, l2), (l2, l3)):
            if a >= b: cu += a - b
            else: cd += b - a
        out[i] = cu / (cu + cd) if cu + cd > 0.0 else 0.5
    return pd.Series(out, index=close.index)


def sig_laguerre_rsi_long_1h(d, p, ctx):
    """C116: enter when recursive Laguerre directional energy turns positive."""
    lrsi = _laguerre_rsi(d.c, float(p['gamma']))
    cross_up = (lrsi > 0.5) & (lrsi.shift() <= 0.5)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=lrsi <= 0.5, sx=never)


STRATEGIES['laguerre_rsi_long_1h'] = dict(
    name='[RESEARCH] Laguerre RSI long 1h', style='recursive_laguerre_energy',
    fn=sig_laguerre_rsi_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(LRSI1H),
    desc='Candidate 116: four-stage Laguerre RSI(gamma=.5) crosses above .5 above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


KAMA1H = dict(er_window=10, fast=2, slow=30, cost_multiple=5.0)


def _kaufman_adaptive_average(close, er_window=10, fast=2, slow=30):
    """Causal Kaufman Adaptive Moving Average with squared efficiency-mapped smoothing."""
    c = close.astype(float); change = (c - c.shift(er_window)).abs()
    path = c.diff().abs().rolling(er_window).sum(); er = change / path.replace(0.0, np.nan)
    fast_sc, slow_sc = 2.0 / (fast + 1.0), 2.0 / (slow + 1.0)
    sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
    out = pd.Series(np.nan, index=c.index, dtype=float); prior = np.nan
    for i, (price, alpha) in enumerate(zip(c.to_numpy(float), sc.to_numpy(float))):
        if not np.isfinite(alpha): continue
        prior = price if not np.isfinite(prior) else prior + alpha * (price - prior)
        out.iloc[i] = prior
    return out


def sig_kama_cross_long_1h(d, p, ctx):
    """C117: enter a transition above an efficiency-adaptive price baseline."""
    kama = _kaufman_adaptive_average(d.c, int(p['er_window']), int(p['fast']), int(p['slow']))
    cross_up = (d.c > kama) & (d.c.shift() <= kama.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=d.c <= kama, sx=never)


STRATEGIES['kama_cross_long_1h'] = dict(
    name='[RESEARCH] Kaufman adaptive crossover long 1h', style='efficiency_adaptive_trend',
    fn=sig_kama_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(KAMA1H),
    desc='Candidate 117: close crosses above conventional KAMA(10,2,30) while above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


TII1H = dict(mean_window=60, sum_window=30, cost_multiple=5.0)


def _trend_intensity_index(close, mean_window=60, sum_window=30):
    """Balance of accumulated positive and negative deviations from a trailing mean."""
    c = close.astype(float); deviation = c - c.rolling(mean_window).mean()
    positive = deviation.clip(lower=0.0).rolling(sum_window).sum()
    negative = (-deviation.clip(upper=0.0)).rolling(sum_window).sum()
    return 100.0 * positive / (positive + negative).replace(0.0, np.nan)


def sig_trend_intensity_long_1h(d, p, ctx):
    """C118: enter when accumulated deviations become positively dominated."""
    tii = _trend_intensity_index(d.c, int(p['mean_window']), int(p['sum_window']))
    cross_up = (tii > 50.0) & (tii.shift() <= 50.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=tii <= 50.0, sx=never)


STRATEGIES['trend_intensity_long_1h'] = dict(
    name='[RESEARCH] Trend Intensity Index long 1h', style='deviation_balance_trend',
    fn=sig_trend_intensity_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(TII1H),
    desc='Candidate 118: conventional TII(60,30) crosses above 50 while close is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CTI1H = dict(lookback=20, enter=0.5, exit=0.0, cost_multiple=5.0)


def _correlation_trend_indicator(close, lookback=20):
    """Rolling Pearson correlation of completed closes with ascending time."""
    x = np.arange(float(lookback)); xm = x.mean(); xnorm = np.sqrt(np.sum((x - xm) ** 2))
    def corr(y):
        dy = y - np.mean(y); denom = xnorm * np.sqrt(np.sum(dy ** 2))
        return float(np.sum((x - xm) * dy) / denom) if denom > 0.0 else np.nan
    return close.astype(float).rolling(lookback).apply(corr, raw=True)


def sig_cti_trend_long_1h(d, p, ctx):
    """C119: enter when price becomes strongly linearly aligned with time."""
    cti = _correlation_trend_indicator(d.c, int(p['lookback']))
    cross_up = (cti > float(p['enter'])) & (cti.shift() <= float(p['enter']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=cti <= float(p['exit']), sx=never)


STRATEGIES['cti_trend_long_1h'] = dict(
    name='[RESEARCH] Correlation Trend Indicator long 1h', style='price_time_alignment',
    fn=sig_cti_trend_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CTI1H),
    desc='Candidate 119: rolling price-time CTI(20) crosses above +0.5 above EMA20; exits at zero.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


HURST1H = dict(window=64, enter=0.60, exit=0.50, drift_bars=24, cost_multiple=5.0)


def _hurst_rescaled_range(close, window=64):
    """Causal rolling Hurst slope from average rescaled ranges at 8, 16, and 32 returns."""
    sizes = (8, 16, 32); logs = np.log(np.asarray(sizes, float)); lm = logs.mean()
    def estimate(prices):
        returns = np.diff(prices); points = []
        for size in sizes:
            ratios = []
            for start in range(0, len(returns) - size + 1, size):
                block = returns[start:start + size]; dev = block - np.mean(block); s = np.std(block)
                if s > 0.0:
                    path = np.cumsum(dev); ratios.append((np.max(path) - np.min(path)) / s)
            if not ratios or np.mean(ratios) <= 0.0: return np.nan
            points.append(np.log(np.mean(ratios)))
        y = np.asarray(points); return float(np.sum((logs - lm) * (y - y.mean())) / np.sum((logs - lm) ** 2))
    return close.astype(float).rolling(window).apply(estimate, raw=True)


def sig_hurst_persistence_long_1h(d, p, ctx):
    """C120: enter when multi-scale rescaled range transitions into persistence."""
    hurst = _hurst_rescaled_range(d.c, int(p['window'])); drift = d.c.pct_change(int(p['drift_bars']))
    cross_up = (hurst > float(p['enter'])) & (hurst.shift() <= float(p['enter']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (drift > 0.0) & (d.c > d.e20) & cost_ok, se=never,
                lx=(hurst < float(p['exit'])) | (drift <= 0.0), sx=never)


STRATEGIES['hurst_persistence_long_1h'] = dict(
    name='[RESEARCH] Hurst R/S persistence long 1h', style='multiscale_persistence',
    fn=sig_hurst_persistence_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(HURST1H),
    desc='Candidate 120: rolling 64-bar Hurst R/S crosses .60 with positive 24h drift above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


SMI1H = dict(range_window=14, smooth1=3, smooth2=3, cost_multiple=5.0)


def _stochastic_momentum_index(high, low, close, range_window=14, smooth1=3, smooth2=3):
    """Double-smoothed close displacement from rolling midpoint divided by half-range."""
    hi, lo = high.rolling(range_window).max(), low.rolling(range_window).min()
    displacement = close.astype(float) - (hi + lo) / 2.0; half_range = (hi - lo) / 2.0
    num = displacement.ewm(span=smooth1, adjust=False).mean().ewm(span=smooth2, adjust=False).mean()
    den = half_range.ewm(span=smooth1, adjust=False).mean().ewm(span=smooth2, adjust=False).mean()
    return 100.0 * num / den.replace(0.0, np.nan)


def sig_smi_zero_cross_long_1h(d, p, ctx):
    """C121: enter when double-smoothed range-midpoint momentum turns positive."""
    smi = _stochastic_momentum_index(d.h, d.l, d.c, int(p['range_window']), int(p['smooth1']), int(p['smooth2']))
    cross_up = (smi > 0.0) & (smi.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=smi <= 0.0, sx=never)


STRATEGIES['smi_zero_cross_long_1h'] = dict(
    name='[RESEARCH] Stochastic Momentum Index long 1h', style='smoothed_range_midpoint_momentum',
    fn=sig_smi_zero_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(SMI1H),
    desc='Candidate 121: conventional SMI(14,3,3) crosses above zero while close is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


PROJ1H = dict(lookback=14, threshold=50.0, cost_multiple=5.0)


def _projection_oscillator(high, low, close, lookback=14):
    """Close location within causal projected high/low linear-regression envelopes."""
    x = np.arange(float(lookback)); xm = x.mean(); denom = float(np.sum((x - xm) ** 2)); last = lookback - 1.0
    def upper(y):
        slope = float(np.sum((x - xm) * (y - np.mean(y))) / denom)
        return float(np.max(y - slope * x) + slope * last)
    def lower(y):
        slope = float(np.sum((x - xm) * (y - np.mean(y))) / denom)
        return float(np.min(y - slope * x) + slope * last)
    hi = high.astype(float).rolling(lookback).apply(upper, raw=True)
    lo = low.astype(float).rolling(lookback).apply(lower, raw=True)
    return 100.0 * (close.astype(float) - lo) / (hi - lo).replace(0.0, np.nan)


def sig_projection_oscillator_long_1h(d, p, ctx):
    """C122: enter when close moves into the upper half of a detrended price channel."""
    po = _projection_oscillator(d.h, d.l, d.c, int(p['lookback'])); level = float(p['threshold'])
    cross_up = (po > level) & (po.shift() <= level)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=po <= level, sx=never)


STRATEGIES['projection_oscillator_long_1h'] = dict(
    name='[RESEARCH] Projection Oscillator long 1h', style='projected_channel_location',
    fn=sig_projection_oscillator_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(PROJ1H),
    desc='Candidate 122: close crosses above the midpoint of 14-bar projected high/low regression envelopes above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


MCG1H = dict(period=14, constant=0.6, cost_multiple=5.0)


def _mcginley_dynamic(close, period=14, constant=0.6):
    """Causal McGinley Dynamic with nonlinear price/prior feedback."""
    c = close.to_numpy(float); out = np.full(len(c), np.nan); prior = np.nan
    for i, price in enumerate(c):
        if not np.isfinite(prior) or prior <= 0.0:
            prior = price
        else:
            ratio = price / prior; denom = constant * period * ratio ** 4
            prior = prior + (price - prior) / denom if denom > 0.0 else price
        out[i] = prior
    return pd.Series(out, index=close.index)


def sig_mcginley_cross_long_1h(d, p, ctx):
    """C123: enter a close transition above a nonlinear lag-adjusted baseline."""
    dynamic = _mcginley_dynamic(d.c, int(p['period']), float(p['constant']))
    cross_up = (d.c > dynamic) & (d.c.shift() <= dynamic.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    never = d.c < -1
    return dict(le=cross_up & (d.c > d.e20) & cost_ok, se=never, lx=d.c <= dynamic, sx=never)


STRATEGIES['mcginley_cross_long_1h'] = dict(
    name='[RESEARCH] McGinley Dynamic crossover long 1h', style='nonlinear_ratio_adaptive_trend',
    fn=sig_mcginley_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(MCG1H),
    desc='Candidate 123: close crosses above conventional McGinley Dynamic(14,0.6) while above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


EOM1H = dict(window=14, cost_multiple=5.0)


def _ease_of_movement(high, low, quote_volume, window=14):
    """Causal conventional Ease of Movement, with scale-free quote-volume normalization."""
    midpoint = (high.astype(float) + low.astype(float)) / 2.0
    distance = midpoint.diff()
    traded_range = high.astype(float) - low.astype(float)
    raw = distance * traded_range / quote_volume.astype(float).replace(0.0, np.nan)
    return raw.rolling(int(window), min_periods=int(window)).mean()


def sig_ease_of_movement_long_1h(d, p, ctx):
    """C124: follow a positive transition in range displacement per unit of quote liquidity."""
    eom = _ease_of_movement(d.h, d.l, d.qv, p['window'])
    cross_up = (eom > 0.0) & (eom.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=eom <= 0.0, sx=never)


STRATEGIES['ease_of_movement_long_1h'] = dict(
    name='[RESEARCH] Ease of Movement zero-cross long 1h', style='range_liquidity_efficiency',
    fn=sig_ease_of_movement_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(EOM1H),
    desc='Candidate 124: conventional 14h Ease of Movement crosses above zero while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CCI1H = dict(window=20, oversold=-100.0, target=100.0, cost_multiple=5.0)


def _commodity_channel_index(high, low, close, window=20):
    """Causal conventional CCI of typical price around its rolling mean deviation."""
    n = int(window)
    typical = (high.astype(float) + low.astype(float) + close.astype(float)) / 3.0
    mean = typical.rolling(n, min_periods=n).mean()
    deviation = typical.rolling(n, min_periods=n).apply(
        lambda x: float(np.mean(np.abs(x - np.mean(x)))), raw=True)
    return (typical - mean) / (0.015 * deviation.replace(0.0, np.nan))


def sig_cci_recovery_long_1h(d, p, ctx):
    """C125: buy a conventional CCI recovery from below -100."""
    cci = _commodity_channel_index(d.h, d.l, d.c, p['window'])
    cross_up = (cci > float(p['oversold'])) & (cci.shift() <= float(p['oversold']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c < d.e20) & (d.c > d.c.shift()) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=cci >= float(p['target']), sx=never)


STRATEGIES['cci_recovery_long_1h'] = dict(
    name='[RESEARCH] CCI oversold recovery long 1h', style='typical_price_deviation_recovery',
    fn=sig_cci_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CCI1H),
    desc='Candidate 125: conventional CCI(20) crosses above -100 from oversold below EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


TSI1H = dict(long_span=25, short_span=13, signal_span=7, cost_multiple=5.0)


def _true_strength_index(close, long_span=25, short_span=13, signal_span=7):
    """Causal conventional TSI and signal line from double-smoothed momentum."""
    momentum = close.astype(float).diff()
    absolute = momentum.abs()
    signed_smooth = momentum.ewm(span=int(long_span), adjust=False,
                                 min_periods=int(long_span)).mean()
    signed_smooth = signed_smooth.ewm(span=int(short_span), adjust=False,
                                     min_periods=int(short_span)).mean()
    absolute_smooth = absolute.ewm(span=int(long_span), adjust=False,
                                   min_periods=int(long_span)).mean()
    absolute_smooth = absolute_smooth.ewm(span=int(short_span), adjust=False,
                                         min_periods=int(short_span)).mean()
    tsi = 100.0 * signed_smooth / absolute_smooth.replace(0.0, np.nan)
    signal = tsi.ewm(span=int(signal_span), adjust=False,
                     min_periods=int(signal_span)).mean()
    return tsi, signal


def sig_tsi_cross_long_1h(d, p, ctx):
    """C126: follow positive normalized double-smoothed momentum acceleration."""
    tsi, signal = _true_strength_index(d.c, p['long_span'], p['short_span'], p['signal_span'])
    cross_up = (tsi > signal) & (tsi.shift() <= signal.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (tsi > 0.0) & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=tsi <= signal, sx=never)


STRATEGIES['tsi_cross_long_1h'] = dict(
    name='[RESEARCH] True Strength Index crossover long 1h', style='normalized_smoothed_momentum',
    fn=sig_tsi_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(TSI1H),
    desc='Candidate 126: conventional TSI(25,13) crosses above its 7h signal while positive and above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


FORCE1H = dict(span=13, cost_multiple=5.0)


def _force_index(close, quote_volume, span=13):
    """Causal conventional Elder Force Index using quote liquidity."""
    raw = close.astype(float).diff() * quote_volume.astype(float)
    return raw.ewm(span=int(span), adjust=False, min_periods=int(span)).mean()


def sig_force_index_long_1h(d, p, ctx):
    """C127: follow a positive transition in smoothed price-volume force."""
    force = _force_index(d.c, d.qv, p['span'])
    cross_up = (force > 0.0) & (force.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=force <= 0.0, sx=never)


STRATEGIES['force_index_long_1h'] = dict(
    name='[RESEARCH] Force Index zero-cross long 1h', style='price_liquidity_force',
    fn=sig_force_index_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(FORCE1H),
    desc='Candidate 127: conventional 13h Elder Force Index crosses above zero while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CMF1H = dict(window=20, cost_multiple=5.0)


def _chaikin_money_flow(high, low, close, quote_volume, window=20):
    """Causal conventional Chaikin Money Flow using quote liquidity."""
    spread = high.astype(float) - low.astype(float)
    multiplier = (2.0 * close.astype(float) - high.astype(float) - low.astype(float)) / spread.replace(0.0, np.nan)
    flow = multiplier * quote_volume.astype(float)
    n = int(window)
    return flow.rolling(n, min_periods=n).sum() / quote_volume.astype(float).rolling(n, min_periods=n).sum().replace(0.0, np.nan)


def sig_chaikin_money_flow_long_1h(d, p, ctx):
    """C128: follow a positive transition in range-location-weighted liquidity flow."""
    cmf = _chaikin_money_flow(d.h, d.l, d.c, d.qv, p['window'])
    cross_up = (cmf > 0.0) & (cmf.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=cmf <= 0.0, sx=never)


STRATEGIES['chaikin_money_flow_long_1h'] = dict(
    name='[RESEARCH] Chaikin Money Flow zero-cross long 1h', style='range_location_liquidity_flow',
    fn=sig_chaikin_money_flow_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CMF1H),
    desc='Candidate 128: conventional 20h Chaikin Money Flow crosses above zero while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


HMA1H = dict(window=20, cost_multiple=5.0)


def _weighted_moving_average(series, window):
    n = int(window)
    weights = np.arange(1.0, n + 1.0)
    scale = float(weights.sum())
    return series.astype(float).rolling(n, min_periods=n).apply(
        lambda x: float(np.dot(x, weights) / scale), raw=True)


def _hull_moving_average(close, window=20):
    """Causal conventional Hull Moving Average."""
    n = int(window)
    raw = 2.0 * _weighted_moving_average(close, max(1, n // 2)) - _weighted_moving_average(close, n)
    return _weighted_moving_average(raw, max(1, int(np.sqrt(n))))


def sig_hull_ma_cross_long_1h(d, p, ctx):
    """C129: follow a close transition above a rising low-lag finite weighted baseline."""
    hma = _hull_moving_average(d.c, p['window'])
    cross_up = (d.c > hma) & (d.c.shift() <= hma.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (hma > hma.shift()) & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=d.c <= hma, sx=never)


STRATEGIES['hull_ma_cross_long_1h'] = dict(
    name='[RESEARCH] Hull Moving Average crossover long 1h', style='finite_weighted_low_lag_trend',
    fn=sig_hull_ma_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(HMA1H),
    desc='Candidate 129: close crosses above a rising conventional HMA(20) while above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CMO1H = dict(window=14, cost_multiple=5.0)


def _chande_momentum_oscillator(close, window=14):
    """Causal conventional Chande Momentum Oscillator."""
    delta = close.astype(float).diff()
    n = int(window)
    gains = delta.clip(lower=0.0).rolling(n, min_periods=n).sum()
    losses = (-delta.clip(upper=0.0)).rolling(n, min_periods=n).sum()
    return 100.0 * (gains - losses) / (gains + losses).replace(0.0, np.nan)


def sig_cmo_cross_long_1h(d, p, ctx):
    """C130: follow a positive transition in fixed-window directional momentum balance."""
    cmo = _chande_momentum_oscillator(d.c, p['window'])
    cross_up = (cmo > 0.0) & (cmo.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=cmo <= 0.0, sx=never)


STRATEGIES['cmo_cross_long_1h'] = dict(
    name='[RESEARCH] Chande Momentum Oscillator crossover long 1h', style='fixed_window_momentum_balance',
    fn=sig_cmo_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CMO1H),
    desc='Candidate 130: conventional CMO(14) crosses above zero while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


KVO1H = dict(fast=34, slow=55, signal=13, cost_multiple=5.0)


def _klinger_volume_oscillator(high, low, close, quote_volume, fast=34, slow=55):
    """Causal conventional Klinger Volume Oscillator and its volume force."""
    h, l, c = (x.astype(float) for x in (high, low, close))
    dm = h - l
    hlc = h + l + c
    trend = pd.Series(np.where(hlc > hlc.shift(), 1.0, -1.0), index=c.index)
    trend.iloc[0] = np.nan
    cm = pd.Series(np.nan, index=c.index, dtype=float)
    for i in range(1, len(c)):
        if not np.isfinite(dm.iloc[i]) or not np.isfinite(dm.iloc[i - 1]):
            continue
        if i > 1 and np.isfinite(cm.iloc[i - 1]) and trend.iloc[i] == trend.iloc[i - 1]:
            cm.iloc[i] = cm.iloc[i - 1] + dm.iloc[i]
        else:
            cm.iloc[i] = dm.iloc[i - 1] + dm.iloc[i]
    vf = quote_volume.astype(float) * (2.0 * dm / cm.replace(0.0, np.nan) - 1.0).abs() * trend * 100.0
    kvo = vf.ewm(span=int(fast), adjust=False, min_periods=int(fast)).mean() - \
          vf.ewm(span=int(slow), adjust=False, min_periods=int(slow)).mean()
    return kvo, vf


def sig_klinger_volume_long_1h(d, p, ctx):
    """C131: follow a positive crossover in Klinger price-trend volume force."""
    kvo, _ = _klinger_volume_oscillator(d.h, d.l, d.c, d.qv, p['fast'], p['slow'])
    signal = kvo.ewm(span=int(p['signal']), adjust=False, min_periods=int(p['signal'])).mean()
    cross_up = (kvo > signal) & (kvo.shift() <= signal.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=kvo <= signal, sx=never)


STRATEGIES['klinger_volume_long_1h'] = dict(
    name='[RESEARCH] Klinger Volume Oscillator crossover long 1h', style='price_trend_volume_force',
    fn=sig_klinger_volume_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(KVO1H),
    desc='Candidate 131: conventional KVO(34,55) crosses above its signal(13) while positive and price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


AO1H = dict(fast=5, slow=34, cost_multiple=5.0)


def _awesome_oscillator(high, low, fast=5, slow=34):
    """Causal conventional Awesome Oscillator on median price."""
    median = (high.astype(float) + low.astype(float)) / 2.0
    return median.rolling(int(fast), min_periods=int(fast)).mean() - \
           median.rolling(int(slow), min_periods=int(slow)).mean()


def sig_awesome_oscillator_long_1h(d, p, ctx):
    """C132: follow a positive transition in short-vs-long median-price momentum."""
    ao = _awesome_oscillator(d.h, d.l, p['fast'], p['slow'])
    cross_up = (ao > 0.0) & (ao.shift() <= 0.0)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=ao <= 0.0, sx=never)


STRATEGIES['awesome_oscillator_long_1h'] = dict(
    name='[RESEARCH] Awesome Oscillator zero-cross long 1h', style='median_price_dual_sma_momentum',
    fn=sig_awesome_oscillator_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(AO1H),
    desc='Candidate 132: conventional AO SMA5-minus-SMA34 of median price crosses above zero while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


CRSI1H = dict(price_rsi=3, streak_rsi=2, rank_window=100, entry=10.0, exit=70.0, cost_multiple=5.0)


def _connors_rsi(close, price_rsi=3, streak_rsi=2, rank_window=100):
    """Causal conventional Connors RSI: price RSI, streak RSI and return PercentRank."""
    c = close.astype(float)
    delta = np.sign(c.diff().fillna(0.0).to_numpy(float))
    streak = np.zeros(len(c), dtype=float)
    for i in range(1, len(c)):
        streak[i] = streak[i - 1] + delta[i] if delta[i] != 0.0 and delta[i] == delta[i - 1] else delta[i]
    streak = pd.Series(streak, index=c.index)
    ret = c.pct_change()
    n = int(rank_window)
    pct_rank = ret.rolling(n, min_periods=n).apply(
        lambda x: 100.0 * float(np.sum(x < x[-1])) / float(len(x)), raw=True)
    return (rsi(c, int(price_rsi)) + rsi(streak, int(streak_rsi)) + pct_rank) / 3.0


def sig_connors_rsi_recovery_long_1h(d, p, ctx):
    """C133: buy recovery from a conventional composite short-term oversold state."""
    crsi = _connors_rsi(d.c, p['price_rsi'], p['streak_rsi'], p['rank_window'])
    recovery = (crsi > float(p['entry'])) & (crsi.shift() <= float(p['entry']))
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = recovery & (d.c > d.e200) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=crsi >= float(p['exit']), sx=never)


STRATEGIES['connors_rsi_recovery_long_1h'] = dict(
    name='[RESEARCH] Connors RSI oversold-recovery long 1h', style='composite_short_term_mean_reversion',
    fn=sig_connors_rsi_recovery_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(CRSI1H),
    desc='Candidate 133: conventional CRSI(3,2,100) recovers above 10 while price is above EMA200; exit at 70.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


KST1H = dict(cost_multiple=5.0)


def _know_sure_thing(close):
    """Causal conventional KST with standard ROC and smoothing horizons."""
    c = close.astype(float)
    components = []
    for roc_n, smooth_n, weight in ((10, 10, 1.0), (15, 10, 2.0), (20, 10, 3.0), (30, 15, 4.0)):
        roc = 100.0 * (c / c.shift(roc_n) - 1.0)
        components.append(weight * roc.rolling(smooth_n, min_periods=smooth_n).mean())
    kst = sum(components)
    signal = kst.rolling(9, min_periods=9).mean()
    return kst, signal


def sig_kst_cross_long_1h(d, p, ctx):
    """C134: follow a positive crossover in standard multi-horizon smoothed momentum."""
    kst, signal = _know_sure_thing(d.c)
    cross_up = (kst > signal) & (kst.shift() <= signal.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=kst <= signal, sx=never)


STRATEGIES['kst_cross_long_1h'] = dict(
    name='[RESEARCH] Know Sure Thing crossover long 1h', style='weighted_multihorizon_smoothed_roc',
    fn=sig_kst_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(KST1H),
    desc='Candidate 134: standard KST crosses above its 9-bar signal while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


ALLIGATOR1H = dict(cost_multiple=5.0)


def _williams_alligator(high, low):
    """Causal standard Williams Alligator lines as visible on the decision bar."""
    median = (high.astype(float) + low.astype(float)) / 2.0
    jaw = median.ewm(alpha=1.0 / 13.0, adjust=False, min_periods=13).mean().shift(8)
    teeth = median.ewm(alpha=1.0 / 8.0, adjust=False, min_periods=8).mean().shift(5)
    lips = median.ewm(alpha=1.0 / 5.0, adjust=False, min_periods=5).mean().shift(3)
    return jaw, teeth, lips


def sig_alligator_alignment_long_1h(d, p, ctx):
    """C135: follow the first causal bullish alignment of standard displaced Alligator lines."""
    jaw, teeth, lips = _williams_alligator(d.h, d.l)
    aligned = (lips > teeth) & (teeth > jaw) & (d.c > lips)
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = aligned & ~aligned.shift(fill_value=False) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=lips <= teeth, sx=never)


STRATEGIES['alligator_alignment_long_1h'] = dict(
    name='[RESEARCH] Williams Alligator alignment long 1h', style='displaced_smoothed_ma_alignment',
    fn=sig_alligator_alignment_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(ALLIGATOR1H),
    desc='Candidate 135: standard causal jaw/teeth/lips lines first align bullish with close above lips.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))


PVT1H = dict(signal_span=20, cost_multiple=5.0)


def _price_volume_trend(close, base_volume):
    """Causal conventional Price Volume Trend cumulative line."""
    increment = base_volume.astype(float) * close.astype(float).pct_change()
    return increment.fillna(0.0).cumsum()


def sig_pvt_cross_long_1h(d, p, ctx):
    """C136: follow a bullish crossover in cumulative relative-return-weighted volume."""
    pvt = _price_volume_trend(d.c, d.v)
    signal = pvt.ewm(span=int(p['signal_span']), adjust=False, min_periods=int(p['signal_span'])).mean()
    cross_up = (pvt > signal) & (pvt.shift() <= signal.shift())
    cost_ok = 2.0 * d.atr / d.c >= float(p['cost_multiple']) * 2 * (0.0005 + 0.0002)
    le = cross_up & (d.c > d.e20) & cost_ok
    never = d.c < -1
    return dict(le=le, se=never, lx=pvt <= signal, sx=never)


STRATEGIES['pvt_cross_long_1h'] = dict(
    name='[RESEARCH] Price Volume Trend crossover long 1h', style='cumulative_relative_return_volume',
    fn=sig_pvt_cross_long_1h, sides='long', tf='1h', research_only=True,
    reentry_lock=True, params=dict(PVT1H),
    desc='Candidate 136: conventional PVT crosses above EMA20 of PVT while price is above EMA20.',
    mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24))

RAVI1H = dict(fast=7, slow=65, threshold=3.0, cost_multiple=5.0)
def _ravi(close, fast=7, slow=65):
    c=close.astype(float); f=c.rolling(int(fast),min_periods=int(fast)).mean(); s=c.rolling(int(slow),min_periods=int(slow)).mean(); return 100.0*(f-s).abs()/s.replace(0.0,np.nan),f,s
def sig_ravi_trend_long_1h(d,p,ctx):
    ravi,f,s=_ravi(d.c,p['fast'],p['slow']); active=(ravi>float(p['threshold']))&(f>s); cost_ok=2.0*d.atr/d.c>=float(p['cost_multiple'])*2*(.0005+.0002); le=active&~active.shift(fill_value=False)&(d.c>d.e20)&cost_ok; never=d.c<-1; return dict(le=le,se=never,lx=(ravi<=float(p['threshold']))|(f<=s),sx=never)
STRATEGIES['ravi_trend_long_1h']=dict(name='[RESEARCH] RAVI trend activation long 1h',style='normalized_dual_sma_separation',fn=sig_ravi_trend_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(RAVI1H),desc='Candidate 137: conventional RAVI(7,65) first exceeds 3% with fast SMA above slow.',mgmt=dict(stop_atr=2.0,tp_r=2.0,max_bars=24))

GANN_HILO1H=dict(window=3,cost_multiple=5.0)
def _gann_hilo_state(high,low,close,window=3):
    hi=high.astype(float).rolling(int(window),min_periods=int(window)).mean().shift();lo=low.astype(float).rolling(int(window),min_periods=int(window)).mean().shift();state=np.zeros(len(close),dtype=float)
    for i in range(1,len(close)):
        state[i]=1.0 if close.iloc[i]>hi.iloc[i] else (-1.0 if close.iloc[i]<lo.iloc[i] else state[i-1])
    return pd.Series(state,index=close.index),hi,lo
def sig_gann_hilo_long_1h(d,p,ctx):
    state,hi,lo=_gann_hilo_state(d.h,d.l,d.c,p['window']);bull=(state>0)&(state.shift()<=0);cost_ok=2*d.atr/d.c>=float(p['cost_multiple'])*2*(.0005+.0002);never=d.c<-1;return dict(le=bull&(d.c>d.e20)&cost_ok,se=never,lx=state<0,sx=never)
STRATEGIES['gann_hilo_long_1h']=dict(name='[RESEARCH] Gann HiLo Activator long 1h',style='high_low_average_state_switch',fn=sig_gann_hilo_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(GANN_HILO1H),desc='Candidate 138: causal standard three-bar Gann HiLo state switches bullish.',mgmt=dict(stop_atr=2.0,tp_r=2.0,max_bars=24))
PGO1H=dict(window=14,threshold=0.0,cost_multiple=5.0)
def _pretty_good_oscillator(close,atr,window=14):return (close.astype(float)-close.astype(float).rolling(int(window),min_periods=int(window)).mean())/atr.astype(float).replace(0,np.nan)
def sig_pgo_long_1h(d,p,ctx):
 x=_pretty_good_oscillator(d.c,d.atr,p['window']);up=(x>p['threshold'])&(x.shift()<=p['threshold']);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=x<=0,sx=never)
STRATEGIES['pgo_long_1h']=dict(name='[RESEARCH] Pretty Good Oscillator long 1h',style='atr_normalized_mean_deviation',fn=sig_pgo_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(PGO1H),desc='C139 PGO14 crosses zero above EMA20.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
PSY1H=dict(window=12,threshold=50.0,cost_multiple=5.0)
def _psychological_line(close,window=12):
 return close.astype(float).diff().gt(0).astype(float).rolling(int(window),min_periods=int(window)).mean()*100.0
def sig_psychological_line_long_1h(d,p,ctx):
 x=_psychological_line(d.c,p['window']);up=(x>p['threshold'])&(x.shift()<=p['threshold']);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=x<=p['threshold'],sx=never)
STRATEGIES['psychological_line_long_1h']=dict(name='[RESEARCH] Psychological Line long 1h',style='up_close_frequency',fn=sig_psychological_line_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(PSY1H),desc='C140 PSY12 crosses above 50 while close is above EMA20.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
RMI1H=dict(momentum=5,smooth=20,threshold=50.0,cost_multiple=5.0)
def _relative_momentum_index(close,momentum=5,smooth=20):
 delta=close.astype(float).diff(int(momentum));up=delta.clip(lower=0).ewm(alpha=1.0/int(smooth),adjust=False,min_periods=int(smooth)).mean();down=(-delta.clip(upper=0)).ewm(alpha=1.0/int(smooth),adjust=False,min_periods=int(smooth)).mean();den=up+down;return 100.0*up/den.replace(0,np.nan)
def sig_relative_momentum_index_long_1h(d,p,ctx):
 x=_relative_momentum_index(d.c,p['momentum'],p['smooth']);up=(x>p['threshold'])&(x.shift()<=p['threshold']);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=x<=p['threshold'],sx=never)
STRATEGIES['relative_momentum_index_long_1h']=dict(name='[RESEARCH] Relative Momentum Index long 1h',style='lagged_momentum_ratio',fn=sig_relative_momentum_index_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(RMI1H),desc='C141 conventional RMI(5,20) crosses above 50 while close is above EMA20.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
STOCHRSI1H=dict(rsi_window=14,stoch_window=14,entry=20.0,exit=80.0,cost_multiple=5.0)
def _stochastic_rsi(close,rsi_window=14,stoch_window=14):
 x=rsi(close.astype(float),int(rsi_window));lo=x.rolling(int(stoch_window),min_periods=int(stoch_window)).min();hi=x.rolling(int(stoch_window),min_periods=int(stoch_window)).max();return 100.0*(x-lo)/(hi-lo).replace(0,np.nan)
def sig_stochastic_rsi_recovery_long_1h(d,p,ctx):
 x=_stochastic_rsi(d.c,p['rsi_window'],p['stoch_window']);up=(x>p['entry'])&(x.shift()<=p['entry']);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=x>=p['exit'],sx=never)
STRATEGIES['stochastic_rsi_recovery_long_1h']=dict(name='[RESEARCH] Stochastic RSI recovery long 1h',style='normalized_rsi_recovery',fn=sig_stochastic_rsi_recovery_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(STOCHRSI1H),desc='C142 conventional StochRSI(14,14) recovers above 20 over EMA200 and exits at 80.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
CHAIKINOSC1H=dict(fast=3,slow=10,threshold=0.0,cost_multiple=5.0)
def _chaikin_oscillator(high,low,close,volume,fast=3,slow=10):
 span=(high.astype(float)-low.astype(float)).replace(0,np.nan);mfm=((close.astype(float)-low.astype(float))-(high.astype(float)-close.astype(float)))/span;adl=(mfm.fillna(0)*volume.astype(float)).cumsum();return adl.ewm(span=int(fast),adjust=False,min_periods=int(fast)).mean()-adl.ewm(span=int(slow),adjust=False,min_periods=int(slow)).mean()
def sig_chaikin_oscillator_long_1h(d,p,ctx):
 x=_chaikin_oscillator(d.h,d.l,d.c,d.v,p['fast'],p['slow']);up=(x>p['threshold'])&(x.shift()<=p['threshold']);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=x<=p['threshold'],sx=never)
STRATEGIES['chaikin_oscillator_long_1h']=dict(name='[RESEARCH] Chaikin Oscillator long 1h',style='adl_momentum',fn=sig_chaikin_oscillator_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(CHAIKINOSC1H),desc='C143 conventional Chaikin Oscillator EMA3-minus-EMA10 of ADL crosses above zero over EMA20.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
VZO1H=dict(window=14,threshold=0.0,cost_multiple=5.0)
def _volume_zone_oscillator(close,volume,window=14):
 direction=np.sign(close.astype(float).diff()).fillna(0.0);signed=(volume.astype(float)*direction).ewm(span=int(window),adjust=False,min_periods=int(window)).mean();total=volume.astype(float).ewm(span=int(window),adjust=False,min_periods=int(window)).mean();return 100.0*signed/total.replace(0,np.nan)
def sig_volume_zone_oscillator_long_1h(d,p,ctx):
 x=_volume_zone_oscillator(d.c,d.v,p['window']);up=(x>p['threshold'])&(x.shift()<=p['threshold']);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=x<=p['threshold'],sx=never)
STRATEGIES['volume_zone_oscillator_long_1h']=dict(name='[RESEARCH] Volume Zone Oscillator long 1h',style='ema_signed_volume_ratio',fn=sig_volume_zone_oscillator_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(VZO1H),desc='C144 conventional VZO14 crosses above zero while close is above EMA20.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
PVI1H=dict(ema_span=255,cost_multiple=5.0)
def _positive_volume_index(close,quote_volume):
 log_ret=np.log(close.astype(float)/close.astype(float).shift());selected=log_ret.where(quote_volume.astype(float)>quote_volume.astype(float).shift(),0.0).fillna(0.0);return 1000.0*np.exp(selected.cumsum())
def sig_positive_volume_index_long_1h(d,p,ctx):
 pvi=_positive_volume_index(d.c,d.qv);signal=pvi.ewm(span=int(p['ema_span']),adjust=False,min_periods=int(p['ema_span'])).mean();up=(pvi>signal)&(pvi.shift()<=signal.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=pvi<signal,sx=never)
STRATEGIES['positive_volume_index_long_1h']=dict(name='[RESEARCH] Positive Volume Index long 1h',style='selective_high_volume_accumulation',fn=sig_positive_volume_index_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(PVI1H),desc='C145 conventional PVI crosses above its 255h EMA with price above EMA20.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
PVO1H=dict(fast=12,slow=26,signal=9,cost_multiple=5.0)
def _percentage_volume_oscillator(volume,fast=12,slow=26,signal=9):
 v=volume.astype(float);ef=v.ewm(span=int(fast),adjust=False,min_periods=int(fast)).mean();es=v.ewm(span=int(slow),adjust=False,min_periods=int(slow)).mean();pvo=100.0*(ef-es)/es.replace(0,np.nan);sig=pvo.ewm(span=int(signal),adjust=False,min_periods=int(signal)).mean();return pvo,sig
def sig_percentage_volume_oscillator_long_1h(d,p,ctx):
 x,s=_percentage_volume_oscillator(d.v,p['fast'],p['slow'],p['signal']);up=(x>s)&(x.shift()<=s.shift())&(x>0);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=x<=s,sx=never)
STRATEGIES['percentage_volume_oscillator_long_1h']=dict(name='[RESEARCH] Percentage Volume Oscillator long 1h',style='volume_ema_spread',fn=sig_percentage_volume_oscillator_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(PVO1H),desc='C146 conventional PVO(12,26,9) bullish signal crossover above zero with price above EMA20.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
EIMP1H=dict(ema_span=13,macd_fast=12,macd_slow=26,macd_signal=9,cost_multiple=5.0)
def _elder_impulse(close,ema_span=13,macd_fast=12,macd_slow=26,macd_signal=9):
 c=close.astype(float);trend=c.ewm(span=int(ema_span),adjust=False,min_periods=int(ema_span)).mean();macd=c.ewm(span=int(macd_fast),adjust=False,min_periods=int(macd_fast)).mean()-c.ewm(span=int(macd_slow),adjust=False,min_periods=int(macd_slow)).mean();signal=macd.ewm(span=int(macd_signal),adjust=False,min_periods=int(macd_signal)).mean();return trend,macd-signal
def sig_elder_impulse_long_1h(d,p,ctx):
 trend,hist=_elder_impulse(d.c,p['ema_span'],p['macd_fast'],p['macd_slow'],p['macd_signal']);green=(trend>trend.shift())&(hist>hist.shift());up=green&~green.shift(fill_value=False);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=~green,sx=never)
STRATEGIES['elder_impulse_long_1h']=dict(name='[RESEARCH] Elder Impulse System long 1h',style='trend_momentum_slope',fn=sig_elder_impulse_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(EIMP1H),desc='C147 conventional Elder Impulse turns green when EMA13 and MACD histogram both rise above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
CKS1H=dict(atr_window=10,stop_window=9,multiplier=1.0,cost_multiple=5.0)
def _chande_kroll_stops(high,low,close,atr_window=10,stop_window=9,multiplier=1.0):
 p=int(atr_window);q=int(stop_window);prev=close.astype(float).shift();tr=pd.concat([(high-low).abs(),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1);atr=tr.ewm(alpha=1.0/p,adjust=False,min_periods=p).mean();first_long=high.astype(float).rolling(p,min_periods=p).max()-float(multiplier)*atr;first_short=low.astype(float).rolling(p,min_periods=p).min()+float(multiplier)*atr;return first_long.rolling(q,min_periods=q).max(),first_short.rolling(q,min_periods=q).min()
def sig_chande_kroll_breakout_long_1h(d,p,ctx):
 lower,upper=_chande_kroll_stops(d.h,d.l,d.c,p['atr_window'],p['stop_window'],p['multiplier']);up=(d.c>upper)&(d.c.shift()<=upper.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e20)&cost,se=never,lx=d.c<lower,sx=never)
STRATEGIES['chande_kroll_breakout_long_1h']=dict(name='[RESEARCH] Chande Kroll Stop breakout long 1h',style='atr_stop_channel_breakout',fn=sig_chande_kroll_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(CKS1H),desc='C148 conventional CKS(10,1,9) close crosses above upper stop over EMA20 and exits below lower stop.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
VIDYA1H=dict(length=14,momentum=9,cost_multiple=5.0)
def _vidya(close,length=14,momentum=9):
 c=close.astype(float);d=c.diff();up=d.clip(lower=0).rolling(int(momentum),min_periods=int(momentum)).sum();down=(-d.clip(upper=0)).rolling(int(momentum),min_periods=int(momentum)).sum();ratio=(up-down).abs()/(up+down).replace(0,np.nan);alpha=2.0/(int(length)+1.0);out=np.empty(len(c),dtype=float);out[0]=c.iloc[0]
 for i in range(1,len(c)):
  k=alpha*ratio.iloc[i] if np.isfinite(ratio.iloc[i]) else 0.0;out[i]=k*c.iloc[i]+(1.0-k)*out[i-1]
 return pd.Series(out,index=c.index)
def sig_vidya_crossover_long_1h(d,p,ctx):
 x=_vidya(d.c,p['length'],p['momentum']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['vidya_crossover_long_1h']=dict(name='[RESEARCH] VIDYA crossover long 1h',style='cmo_adaptive_average',fn=sig_vidya_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(VIDYA1H),desc='C149 conventional VIDYA(14,CMO9) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
ALMA1H=dict(window=9,offset=0.85,sigma=6.0,cost_multiple=5.0)
def _alma(close,window=9,offset=0.85,sigma=6.0):
 n=int(window);i=np.arange(n,dtype=float);m=float(offset)*(n-1);s=n/float(sigma);w=np.exp(-((i-m)**2)/(2.0*s*s));w/=w.sum();return close.astype(float).rolling(n,min_periods=n).apply(lambda x:float(np.dot(x,w)),raw=True)
def sig_alma_crossover_long_1h(d,p,ctx):
 x=_alma(d.c,p['window'],p['offset'],p['sigma']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['alma_crossover_long_1h']=dict(name='[RESEARCH] ALMA crossover long 1h',style='gaussian_weighted_average',fn=sig_alma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(ALMA1H),desc='C150 conventional ALMA(9,.85,6) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
DEMA1H=dict(length=20,cost_multiple=5.0)
def _dema(close,length=20):
 e1=close.astype(float).ewm(span=int(length),adjust=False,min_periods=int(length)).mean();e2=e1.ewm(span=int(length),adjust=False,min_periods=int(length)).mean();return 2.0*e1-e2
def sig_dema_crossover_long_1h(d,p,ctx):
 x=_dema(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['dema_crossover_long_1h']=dict(name='[RESEARCH] DEMA crossover long 1h',style='double_exponential_average',fn=sig_dema_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(DEMA1H),desc='C151 conventional DEMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
TEMA1H=dict(length=20,cost_multiple=5.0)
def _tema(close,length=20):
 n=int(length);e1=close.astype(float).ewm(span=n,adjust=False,min_periods=n).mean();e2=e1.ewm(span=n,adjust=False,min_periods=n).mean();e3=e2.ewm(span=n,adjust=False,min_periods=n).mean();return 3.0*e1-3.0*e2+e3
def sig_tema_crossover_long_1h(d,p,ctx):
 x=_tema(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['tema_crossover_long_1h']=dict(name='[RESEARCH] TEMA crossover long 1h',style='triple_exponential_average',fn=sig_tema_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(TEMA1H),desc='C152 conventional TEMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
ZLEMA1H=dict(length=20,cost_multiple=5.0)
def _zlema(close,length=20):
 n=int(length);lag=(n-1)//2;x=close.astype(float)+(close.astype(float)-close.astype(float).shift(lag));return x.ewm(span=n,adjust=False,min_periods=n).mean()
def sig_zlema_crossover_long_1h(d,p,ctx):
 x=_zlema(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['zlema_crossover_long_1h']=dict(name='[RESEARCH] ZLEMA crossover long 1h',style='zero_lag_exponential_average',fn=sig_zlema_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(ZLEMA1H),desc='C153 conventional ZLEMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
WMA1H=dict(window=20,cost_multiple=5.0)
def sig_wma_crossover_long_1h(d,p,ctx):
 x=_weighted_moving_average(d.c,p['window']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['wma_crossover_long_1h']=dict(name='[RESEARCH] WMA crossover long 1h',style='linear_weighted_average',fn=sig_wma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(WMA1H),desc='C154 conventional linear WMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
SMMA1H=dict(length=20,cost_multiple=5.0)
def _smma(close,length=20):
 n=int(length);x=close.astype(float).to_numpy();out=np.full(len(x),np.nan)
 if len(x)>=n:
  out[n-1]=float(np.mean(x[:n]))
  for i in range(n,len(x)):out[i]=(out[i-1]*(n-1)+x[i])/n
 return pd.Series(out,index=close.index)
def sig_smma_crossover_long_1h(d,p,ctx):
 x=_smma(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['smma_crossover_long_1h']=dict(name='[RESEARCH] SMMA crossover long 1h',style='wilder_smoothed_average',fn=sig_smma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(SMMA1H),desc='C155 conventional Wilder SMMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
SMA1H=dict(length=20,cost_multiple=5.0)
def sig_sma_crossover_long_1h(d,p,ctx):
 x=d.c.astype(float).rolling(int(p['length']),min_periods=int(p['length'])).mean();up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['sma_crossover_long_1h']=dict(name='[RESEARCH] SMA crossover long 1h',style='simple_moving_average',fn=sig_sma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(SMA1H),desc='C156 conventional SMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
TMA1H=dict(length=20,cost_multiple=5.0)
def _tma(close,length=20):
 n=int(length);first=(n+1)//2;second=n//2+1;return close.astype(float).rolling(first,min_periods=first).mean().rolling(second,min_periods=second).mean()
def sig_tma_crossover_long_1h(d,p,ctx):
 x=_tma(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['tma_crossover_long_1h']=dict(name='[RESEARCH] TMA crossover long 1h',style='triangular_moving_average',fn=sig_tma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(TMA1H),desc='C157 conventional TMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
LSMA1H=dict(length=20,cost_multiple=5.0)
def _lsma(close,length=20):
 n=int(length);i=np.arange(n,dtype=float);center=i-i.mean();den=float(np.dot(center,center));return close.astype(float).rolling(n,min_periods=n).apply(lambda x:float(x.mean()+(n-1-i.mean())*np.dot(center,x)/den),raw=True)
def sig_lsma_crossover_long_1h(d,p,ctx):
 x=_lsma(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['lsma_crossover_long_1h']=dict(name='[RESEARCH] LSMA crossover long 1h',style='least_squares_moving_average',fn=sig_lsma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(LSMA1H),desc='C158 conventional LSMA(20) endpoint crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
VWMA1H=dict(length=20,cost_multiple=5.0)
def _vwma(close,volume,length=20):
 n=int(length);den=volume.astype(float).rolling(n,min_periods=n).sum();return (close.astype(float)*volume.astype(float)).rolling(n,min_periods=n).sum()/den.replace(0.0,np.nan)
def sig_vwma_crossover_long_1h(d,p,ctx):
 x=_vwma(d.c,d.v,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['vwma_crossover_long_1h']=dict(name='[RESEARCH] VWMA crossover long 1h',style='volume_weighted_moving_average',fn=sig_vwma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(VWMA1H),desc='C159 conventional VWMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
GMA1H=dict(length=20,cost_multiple=5.0)
def _gma(close,length=20):
 n=int(length);x=close.astype(float).where(close.astype(float)>0.0);return np.exp(np.log(x).rolling(n,min_periods=n).mean())
def sig_gma_crossover_long_1h(d,p,ctx):
 x=_gma(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['gma_crossover_long_1h']=dict(name='[RESEARCH] GMA crossover long 1h',style='geometric_moving_average',fn=sig_gma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(GMA1H),desc='C160 conventional GMA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
MAENV1H=dict(length=20,envelope=0.025,cost_multiple=5.0)
def _ma_envelope(close,length=20,envelope=0.025):
 mid=close.astype(float).rolling(int(length),min_periods=int(length)).mean();return mid,mid*(1.0+float(envelope)),mid*(1.0-float(envelope))
def sig_ma_envelope_breakout_long_1h(d,p,ctx):
 mid,upper,_=_ma_envelope(d.c,p['length'],p['envelope']);up=(d.c>upper)&(d.c.shift()<=upper.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['ma_envelope_breakout_long_1h']=dict(name='[RESEARCH] MA envelope breakout long 1h',style='fixed_percent_price_envelope',fn=sig_ma_envelope_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(MAENV1H),desc='C161 conventional SMA20 +2.5% envelope breakout above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
SEB1H=dict(length=20,multiplier=2.0,cost_multiple=5.0)
def _standard_error_bands(close,length=20,multiplier=2.0):
 n=int(length);i=np.arange(n,dtype=float);center=i-i.mean();den=float(np.dot(center,center));mid=_lsma(close,n)
 def err(x):
  slope=float(np.dot(center,x)/den);fit=float(x.mean())+slope*center;return float(np.sqrt(np.dot(x-fit,x-fit)/(n-2)))
 se=close.astype(float).rolling(n,min_periods=n).apply(err,raw=True);k=float(multiplier);return mid,mid+k*se,mid-k*se
def sig_standard_error_band_breakout_long_1h(d,p,ctx):
 mid,upper,_=_standard_error_bands(d.c,p['length'],p['multiplier']);up=(d.c>upper)&(d.c.shift()<=upper.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['standard_error_band_breakout_long_1h']=dict(name='[RESEARCH] Standard Error Band breakout long 1h',style='regression_standard_error_channel',fn=sig_standard_error_band_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(SEB1H),desc='C162 LSMA20 +2 residual standard errors breakout above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
RAFF1H=dict(length=20,cost_multiple=5.0)
def _raff_channel(close,high,low,length=20):
 n=int(length);i=np.arange(n,dtype=float);center=i-i.mean();den=float(np.dot(center,center));c=close.astype(float).to_numpy();h=high.astype(float).to_numpy();l=low.astype(float).to_numpy();mid=np.full(len(c),np.nan);width=np.full(len(c),np.nan)
 for j in range(n-1,len(c)):
  cw=c[j-n+1:j+1];slope=float(np.dot(center,cw)/den);fit=float(cw.mean())+slope*center;mid[j]=fit[-1];width[j]=max(float(np.max(h[j-n+1:j+1]-fit)),float(np.max(fit-l[j-n+1:j+1])))
 m=pd.Series(mid,index=close.index);w=pd.Series(width,index=close.index);return m,m+w,m-w
def sig_raff_channel_breakout_long_1h(d,p,ctx):
 mid,upper,_=_raff_channel(d.c,d.h,d.l,p['length']);prior=upper.shift();up=(d.c>prior)&(d.c.shift()<=upper.shift(2));cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=mid.shift(),sx=never)
STRATEGIES['raff_channel_breakout_long_1h']=dict(name='[RESEARCH] Raff Regression Channel breakout long 1h',style='raff_regression_channel',fn=sig_raff_channel_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(RAFF1H),desc='C163 breakout of prior 20-bar Raff regression channel above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
STARC1H=dict(ma_length=6,atr_length=15,multiplier=2.0,cost_multiple=5.0)
def _starc_bands(high,low,close,ma_length=6,atr_length=15,multiplier=2.0):
 prev=close.astype(float).shift();tr=pd.concat([(high-low).abs(),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1);atr=tr.ewm(alpha=1.0/int(atr_length),adjust=False,min_periods=int(atr_length)).mean();mid=close.astype(float).rolling(int(ma_length),min_periods=int(ma_length)).mean();k=float(multiplier);return mid,mid+k*atr,mid-k*atr
def sig_starc_band_breakout_long_1h(d,p,ctx):
 mid,upper,_=_starc_bands(d.h,d.l,d.c,p['ma_length'],p['atr_length'],p['multiplier']);up=(d.c>upper)&(d.c.shift()<=upper.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['starc_band_breakout_long_1h']=dict(name='[RESEARCH] STARC Band breakout long 1h',style='stoller_average_range_channel',fn=sig_starc_band_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(STARC1H),desc='C164 conventional STARC(6,15,2) upper-band breakout above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
ACCEL1H=dict(length=20,factor=4.0,cost_multiple=5.0)
def _acceleration_bands(high,low,close,length=20,factor=4.0):
 n=int(length);h=high.astype(float);l=low.astype(float);c=close.astype(float);width=float(factor)*(h-l)/(h+l).replace(0.0,np.nan);upper=(h*(1.0+width)).rolling(n,min_periods=n).mean();lower=(l*(1.0-width)).rolling(n,min_periods=n).mean();mid=c.rolling(n,min_periods=n).mean();return mid,upper,lower
def sig_acceleration_band_breakout_long_1h(d,p,ctx):
 mid,upper,_=_acceleration_bands(d.h,d.l,d.c,p['length'],p['factor']);up=(d.c>upper)&(d.c.shift()<=upper.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['acceleration_band_breakout_long_1h']=dict(name='[RESEARCH] Acceleration Band breakout long 1h',style='headley_acceleration_bands',fn=sig_acceleration_band_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(ACCEL1H),desc='C165 conventional Headley Acceleration Bands(20,4) upper-band breakout above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
SINWMA1H=dict(length=20,cost_multiple=5.0)
def _sine_wma(close,length=20):
 n=int(length);w=np.sin(np.pi*np.arange(1,n+1,dtype=float)/(n+1));return close.astype(float).rolling(n,min_periods=n).apply(lambda x:float(np.dot(x,w)/w.sum()),raw=True)
def sig_sine_wma_crossover_long_1h(d,p,ctx):
 x=_sine_wma(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['sine_wma_crossover_long_1h']=dict(name='[RESEARCH] Sine WMA crossover long 1h',style='sine_weighted_moving_average',fn=sig_sine_wma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(SINWMA1H),desc='C166 conventional sine-weighted MA(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
MEDMA1H=dict(length=20,cost_multiple=5.0)
def _median_ma(close,length=20):
 return close.astype(float).rolling(int(length),min_periods=int(length)).median()
def sig_median_ma_crossover_long_1h(d,p,ctx):
 x=_median_ma(d.c,p['length']);up=(d.c>x)&(d.c.shift()<=x.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=x,sx=never)
STRATEGIES['median_ma_crossover_long_1h']=dict(name='[RESEARCH] Median MA crossover long 1h',style='rolling_median_moving_average',fn=sig_median_ma_crossover_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(MEDMA1H),desc='C167 conventional rolling median(20) price crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
FCB1H=dict(left=2,right=2,cost_multiple=5.0)
def _fractal_chaos_bands(high,low,left=2,right=2):
 l=int(left);r=int(right);w=l+r+1;ph=high.astype(float).shift(r);pl=low.astype(float).shift(r);upper=ph.where(ph.eq(high.astype(float).rolling(w,min_periods=w).max())).ffill();lower=pl.where(pl.eq(low.astype(float).rolling(w,min_periods=w).min())).ffill();return upper,lower
def sig_fractal_chaos_breakout_long_1h(d,p,ctx):
 upper,lower=_fractal_chaos_bands(d.h,d.l,p['left'],p['right']);up=(d.c>upper)&(d.c.shift()<=upper.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=lower,sx=never)
STRATEGIES['fractal_chaos_breakout_long_1h']=dict(name='[RESEARCH] Fractal Chaos Bands breakout long 1h',style='confirmed_fractal_pivot_channel',fn=sig_fractal_chaos_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(FCB1H),desc='C168 conventional confirmed 2/2 Fractal Chaos upper-band breakout above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
COG1H=dict(length=10,cost_multiple=5.0)
def _center_of_gravity(close,length=10):
 n=int(length);w=np.arange(n,0,-1,dtype=float);return close.astype(float).rolling(n,min_periods=n).apply(lambda x:float(-np.dot(x,w)/x.sum()) if x.sum()!=0 else np.nan,raw=True)
def sig_cog_oscillator_long_1h(d,p,ctx):
 x=_center_of_gravity(d.c,p['length']);trigger=x.shift();up=(x>trigger)&(x.shift()<=trigger.shift());down=(x<=trigger)&(x.shift()>trigger.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=down,sx=never)
STRATEGIES['cog_oscillator_long_1h']=dict(name='[RESEARCH] Center of Gravity oscillator long 1h',style='ehlers_center_of_gravity',fn=sig_cog_oscillator_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(COG1H),desc='C169 conventional Ehlers COG(10) crossing its one-bar trigger above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
RAVI1H=dict(fast=7,slow=65,threshold=3.0,cost_multiple=5.0)
def _ravi(close,fast=7,slow=65):
 f=close.astype(float).rolling(int(fast),min_periods=int(fast)).mean();s=close.astype(float).rolling(int(slow),min_periods=int(slow)).mean();return (100.0*(f-s).abs()/s.replace(0.0,np.nan)),f,s
def sig_ravi_trend_long_1h(d,p,ctx):
 x,f,s=_ravi(d.c,p['fast'],p['slow']);level=float(p['threshold']);up=(x>level)&(x.shift()<=level);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(f>s)&(d.c>d.e200)&cost,se=never,lx=(x<=level)|(f<=s),sx=never)
STRATEGIES['ravi_trend_long_1h']=dict(name='[RESEARCH] RAVI trend long 1h',style='range_action_verification_index',fn=sig_ravi_trend_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(RAVI1H),desc='C170 conventional RAVI(7,65) crossing 3% with bullish SMA ordering above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
PFE1H=dict(length=10,smoothing=5,cost_multiple=5.0)
def _polarized_fractal_efficiency(close,length=10,smoothing=5):
 n=int(length);d=close.astype(float).diff();delta=close.astype(float)-close.astype(float).shift(n);straight=np.sqrt(delta*delta+float(n*n));path=np.sqrt(d*d+1.0).rolling(n,min_periods=n).sum();raw=100.0*np.sign(delta)*straight/path.replace(0.0,np.nan);return raw.ewm(span=int(smoothing),adjust=False,min_periods=int(smoothing)).mean()
def sig_pfe_trend_long_1h(d,p,ctx):
 x=_polarized_fractal_efficiency(d.c,p['length'],p['smoothing']);up=(x>0)&(x.shift()<=0);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=x<=0,sx=never)
STRATEGIES['pfe_trend_long_1h']=dict(name='[RESEARCH] Polarized Fractal Efficiency long 1h',style='polarized_fractal_efficiency',fn=sig_pfe_trend_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(PFE1H),desc='C171 conventional smoothed PFE(10,5) positive crossover above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
GMMA1H=dict(short_periods=(3,5,8,10,12,15),long_periods=(30,35,40,45,50,60),cost_multiple=5.0)
def _gmma_ribbons(close,short_periods=(3,5,8,10,12,15),long_periods=(30,35,40,45,50,60)):
 c=close.astype(float);short=pd.concat([c.ewm(span=int(n),adjust=False,min_periods=int(n)).mean() for n in short_periods],axis=1);long=pd.concat([c.ewm(span=int(n),adjust=False,min_periods=int(n)).mean() for n in long_periods],axis=1);return short.min(axis=1,skipna=False),long.max(axis=1,skipna=False)
def sig_gmma_ribbon_long_1h(d,p,ctx):
 short_min,long_max=_gmma_ribbons(d.c,p['short_periods'],p['long_periods']);aligned=short_min>long_max;up=aligned&~aligned.shift(fill_value=False);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=~aligned,sx=never)
STRATEGIES['gmma_ribbon_long_1h']=dict(name='[RESEARCH] GMMA ribbon long 1h',style='guppy_multiple_moving_average',fn=sig_gmma_ribbon_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(GMMA1H),desc='C172 canonical Guppy short EMA ribbon fully above long EMA ribbon and EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
SSL1H=dict(length=10,cost_multiple=5.0)
def _ssl_channel(high,low,close,length=10):
 n=int(length);mh=high.astype(float).rolling(n,min_periods=n).mean();ml=low.astype(float).rolling(n,min_periods=n).mean();state=np.zeros(len(close),dtype=np.int8)
 for i in range(len(close)):
  prev=state[i-1] if i else 0;state[i]=1 if close.iloc[i]>mh.iloc[i] else (-1 if close.iloc[i]<ml.iloc[i] else prev)
 s=pd.Series(state,index=close.index);up=mh.where(s>=0,ml);down=ml.where(s>=0,mh);return s,up,down
def sig_ssl_channel_long_1h(d,p,ctx):
 state,_,_=_ssl_channel(d.h,d.l,d.c,p['length']);up=(state>0)&(state.shift(fill_value=0)<=0);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=state<0,sx=never)
STRATEGIES['ssl_channel_long_1h']=dict(name='[RESEARCH] SSL Channel long 1h',style='ssl_high_low_hysteresis',fn=sig_ssl_channel_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(SSL1H),desc='C173 conventional SSL Channel(10) bullish state flip above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
CHAND1H=dict(length=22,multiplier=3.0,cost_multiple=5.0)
def _chandelier_exit(high,low,close,length=22,multiplier=3.0):
 n=int(length);prev=close.astype(float).shift();tr=pd.concat([(high-low).abs(),(high-prev).abs(),(low-prev).abs()],axis=1).max(axis=1);atr=tr.ewm(alpha=1.0/n,adjust=False,min_periods=n).mean();k=float(multiplier);long=high.astype(float).rolling(n,min_periods=n).max()-k*atr;short=low.astype(float).rolling(n,min_periods=n).min()+k*atr;return long,short
def sig_chandelier_breakout_long_1h(d,p,ctx):
 long,short=_chandelier_exit(d.h,d.l,d.c,p['length'],p['multiplier']);up=(d.c>short)&(d.c.shift()<=short.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<long,sx=never)
STRATEGIES['chandelier_breakout_long_1h']=dict(name='[RESEARCH] Chandelier Exit breakout long 1h',style='chandelier_atr_stop_flip',fn=sig_chandelier_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(CHAND1H),desc='C174 canonical Chandelier Exit(22,3) short-stop upside crossing above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
INERTIA1H=dict(std_window=10,rvi_smooth=14,regression=20,cost_multiple=5.0)
def _inertia_indicator(close,std_window=10,rvi_smooth=14,regression=20):
 return _lsma(_relative_volatility_index(close,int(std_window),int(rvi_smooth)),int(regression))
def sig_inertia_long_1h(d,p,ctx):
 x=_inertia_indicator(d.c,p['std_window'],p['rvi_smooth'],p['regression']);up=(x>50.0)&(x.shift()<=50.0);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=x<=50.0,sx=never)
STRATEGIES['inertia_long_1h']=dict(name='[RESEARCH] Dorsey Inertia long 1h',style='rvi_linear_regression_inertia',fn=sig_inertia_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(INERTIA1H),desc='C175 conventional RVI(10,14) linear-regression(20) Inertia crossing 50 above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
NR71H=dict(setup_length=7,exit_length=10,cost_multiple=5.0)
def _nr7_setup(high,low,length=7):
 r=high.astype(float)-low.astype(float);return r.eq(r.rolling(int(length),min_periods=int(length)).min())
def sig_nr7_breakout_long_1h(d,p,ctx):
 setup=_nr7_setup(d.h,d.l,p['setup_length']);up=setup.shift(fill_value=False)&(d.c>d.h.shift());mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['nr7_breakout_long_1h']=dict(name='[RESEARCH] NR7 breakout long 1h',style='narrow_range_7_breakout',fn=sig_nr7_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(NR71H),desc='C176 conventional next-bar breakout above a Narrow Range 7 setup and EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
PIVOT1H=dict(cost_multiple=5.0)
def _classic_floor_pivots(time,high,low,close):
 day=pd.to_datetime(time,utc=True).dt.floor('D');frame=pd.DataFrame({'day':day.to_numpy(),'h':high.to_numpy(float),'l':low.to_numpy(float),'c':close.to_numpy(float)});daily=frame.groupby('day',sort=True).agg(h=('h','max'),l=('l','min'),c=('c','last')).shift();pivot=(daily.h+daily.l+daily.c)/3.0;r1=2.0*pivot-daily.l;s1=2.0*pivot-daily.h;keys=pd.Series(day.to_numpy(),index=close.index);return keys.map(pivot),keys.map(r1),keys.map(s1)
def sig_floor_pivot_breakout_long_1h(d,p,ctx):
 pivot,r1,_=_classic_floor_pivots(d.t,d.h,d.l,d.c);up=(d.c>r1)&(d.c.shift()<=r1.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=pivot,sx=never)
STRATEGIES['floor_pivot_breakout_long_1h']=dict(name='[RESEARCH] Classic floor-pivot breakout long 1h',style='prior_day_classic_floor_pivots',fn=sig_floor_pivot_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(PIVOT1H),desc='C177 prior-UTC-day classic R1 breakout above EMA200 with pivot exit.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
DARVAS1H=dict(lookback=20,confirmation=3,cost_multiple=5.0)
def _darvas_boxes(high,low,lookback=20,confirmation=3):
 h=high.to_numpy(float);l=low.to_numpy(float);n=len(h);k=int(confirmation);roll=high.astype(float).rolling(int(lookback),min_periods=int(lookback)).max().to_numpy();top=np.full(n,np.nan);bottom=np.full(n,np.nan);ct=cb=np.nan
 for i in range(n):
  j=i-k
  if j>=0 and np.isfinite(roll[j]) and h[j]>=roll[j] and np.all(h[j+1:i+1]<h[j]):ct=h[j];cb=float(np.min(l[j:i+1]))
  top[i]=ct;bottom[i]=cb
 return pd.Series(top,index=high.index),pd.Series(bottom,index=high.index)
def sig_darvas_breakout_long_1h(d,p,ctx):
 top,bottom=_darvas_boxes(d.h,d.l,p['lookback'],p['confirmation']);up=(d.c>top)&(d.c.shift()<=top.shift());cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=d.c<=bottom,sx=never)
STRATEGIES['darvas_breakout_long_1h']=dict(name='[RESEARCH] Darvas Box breakout long 1h',style='confirmed_darvas_box',fn=sig_darvas_breakout_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(DARVAS1H),desc='C178 confirmed three-bar Darvas box after a new 20-bar high, above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
TLB1H=dict(reversal_lines=3,cost_multiple=5.0)
def _three_line_break_direction(close,reversal_lines=3):
 c=close.to_numpy(float);state=np.zeros(len(c),dtype=np.int8);opens=[];closes=[];dirs=[];base=c[0] if len(c) else np.nan;n=int(reversal_lines)
 for i in range(1,len(c)):
  x=c[i]
  if not dirs:
   if x!=base:opens.append(base);closes.append(x);dirs.append(1 if x>base else -1)
  else:
   direction=dirs[-1];last=closes[-1]
   if (direction>0 and x>last) or (direction<0 and x<last):opens.append(last);closes.append(x);dirs.append(direction)
   elif direction>0:
    floor=min(min(o,z) for o,z in zip(opens[-n:],closes[-n:]))
    if x<floor:opens.append(last);closes.append(x);dirs.append(-1)
   else:
    ceiling=max(max(o,z) for o,z in zip(opens[-n:],closes[-n:]))
    if x>ceiling:opens.append(last);closes.append(x);dirs.append(1)
  state[i]=dirs[-1] if dirs else 0
 return pd.Series(state,index=close.index)
def sig_three_line_break_long_1h(d,p,ctx):
 state=_three_line_break_direction(d.c,p['reversal_lines']);up=(state>0)&(state.shift(fill_value=0)<=0);cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=up&(d.c>d.e200)&cost,se=never,lx=state<0,sx=never)
STRATEGIES['three_line_break_long_1h']=dict(name='[RESEARCH] Three-Line Break long 1h',style='three_line_break_event_chart',fn=sig_three_line_break_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(TLB1H),desc='C179 conventional close-only Three-Line Break bullish reversal above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
TWS1H=dict(exit_length=10,cost_multiple=5.0)
def _three_white_soldiers(open_,close):
 o=open_.astype(float);c=close.astype(float);bull=c>o;return bull&bull.shift()&bull.shift(2)&(c>c.shift())&(c.shift()>c.shift(2))&(o>=o.shift())&(o<=c.shift())&(o.shift()>=o.shift(2))&(o.shift()<=c.shift(2))
def sig_three_white_soldiers_long_1h(d,p,ctx):
 pattern=_three_white_soldiers(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['three_white_soldiers_long_1h']=dict(name='[RESEARCH] Three White Soldiers long 1h',style='three_white_soldiers_candlestick',fn=sig_three_white_soldiers_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(TWS1H),desc='C180 conventional three advancing bullish real bodies above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
HAMMER1H=dict(exit_length=10,cost_multiple=5.0)
def _bullish_hammer(open_,high,low,close):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=c-o;lower=o-l;upper=h-c;return (body>0)&(lower>=2.0*body)&(upper<=body)
def sig_bullish_hammer_long_1h(d,p,ctx):
 pattern=_bullish_hammer(d.o,d.h,d.l,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['bullish_hammer_long_1h']=dict(name='[RESEARCH] Bullish Hammer long 1h',style='bullish_hammer_candlestick',fn=sig_bullish_hammer_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(HAMMER1H),desc='C181 classical bullish hammer geometry above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
ENGULF1H=dict(exit_length=10,cost_multiple=5.0)
def _bullish_engulfing(open_,close):
 o=open_.astype(float);c=close.astype(float);return (c>o)&(c.shift()<o.shift())&(o<=c.shift())&(c>=o.shift())
def sig_bullish_engulfing_long_1h(d,p,ctx):
 pattern=_bullish_engulfing(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['bullish_engulfing_long_1h']=dict(name='[RESEARCH] Bullish Engulfing long 1h',style='bullish_real_body_engulfing',fn=sig_bullish_engulfing_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(ENGULF1H),desc='C182 conventional bullish real-body engulfing pattern above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
BENGULF1H=dict(exit_length=10,cost_multiple=5.0)
def _bearish_engulfing(open_,close):
 o=open_.astype(float);c=close.astype(float);return (c<o)&(c.shift()>o.shift())&(o>=c.shift())&(c<=o.shift())
def sig_bearish_engulfing_short_1h(d,p,ctx):
 pattern=_bearish_engulfing(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['bearish_engulfing_short_1h']=dict(name='[RESEARCH] Bearish Engulfing short 1h',style='bearish_real_body_engulfing',fn=sig_bearish_engulfing_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(BENGULF1H),desc='C183 conventional bearish real-body engulfing pattern below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
PIERCING1H=dict(exit_length=10,cost_multiple=5.0)
def _piercing_line(open_,close):
 o=open_.astype(float);c=close.astype(float);mid=(o.shift()+c.shift())/2.;return (c>o)&(c.shift()<o.shift())&(o<=c.shift())&(c>mid)&(c<o.shift())
def sig_piercing_line_long_1h(d,p,ctx):
 pattern=_piercing_line(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['piercing_line_long_1h']=dict(name='[RESEARCH] Piercing Line long 1h',style='piercing_line_candlestick',fn=sig_piercing_line_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(PIERCING1H),desc='C184 conventional Piercing Line partial body recovery above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
DARKCLOUD1H=dict(exit_length=10,cost_multiple=5.0)
def _dark_cloud_cover(open_,close):
 o=open_.astype(float);c=close.astype(float);mid=(o.shift()+c.shift())/2.;return (c<o)&(c.shift()>o.shift())&(o>=c.shift())&(c<mid)&(c>o.shift())
def sig_dark_cloud_cover_short_1h(d,p,ctx):
 pattern=_dark_cloud_cover(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['dark_cloud_cover_short_1h']=dict(name='[RESEARCH] Dark Cloud Cover short 1h',style='dark_cloud_cover_candlestick',fn=sig_dark_cloud_cover_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(DARKCLOUD1H),desc='C185 conventional Dark Cloud Cover partial body reversal below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
BHARAMI1H=dict(exit_length=10,cost_multiple=5.0)
def _bullish_harami(open_,close):
 o=open_.astype(float);c=close.astype(float);return (c>o)&(c.shift()<o.shift())&(o>=c.shift())&(c<=o.shift())
def sig_bullish_harami_long_1h(d,p,ctx):
 pattern=_bullish_harami(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['bullish_harami_long_1h']=dict(name='[RESEARCH] Bullish Harami long 1h',style='bullish_harami_candlestick',fn=sig_bullish_harami_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(BHARAMI1H),desc='C186 conventional bullish real-body Harami above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
BEARHARAMI1H=dict(exit_length=10,cost_multiple=5.0)
def _bearish_harami(open_,close):
 o=open_.astype(float);c=close.astype(float);return (c<o)&(c.shift()>o.shift())&(o<=c.shift())&(c>=o.shift())
def sig_bearish_harami_short_1h(d,p,ctx):
 pattern=_bearish_harami(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['bearish_harami_short_1h']=dict(name='[RESEARCH] Bearish Harami short 1h',style='bearish_harami_candlestick',fn=sig_bearish_harami_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(BEARHARAMI1H),desc='C187 conventional bearish real-body Harami below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
TWEEZERBOT1H=dict(low_tolerance_atr=0.10,exit_length=10,cost_multiple=5.0)
def _tweezer_bottom(open_,low,close,atr,tolerance):
 o=open_.astype(float);l=low.astype(float);c=close.astype(float);a=atr.astype(float);return (c>o)&(c.shift()<o.shift())&((l-l.shift()).abs()<=float(tolerance)*a)
def sig_tweezer_bottom_long_1h(d,p,ctx):
 pattern=_tweezer_bottom(d.o,d.l,d.c,d.atr,p['low_tolerance_atr']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['tweezer_bottom_long_1h']=dict(name='[RESEARCH] Tweezer Bottom long 1h',style='tweezer_bottom_candlestick',fn=sig_tweezer_bottom_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(TWEEZERBOT1H),desc='C188 conventional adjacent equal-low Tweezer Bottom above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
TWEEZERTOP1H=dict(high_tolerance_atr=0.10,exit_length=10,cost_multiple=5.0)
def _tweezer_top(open_,high,close,atr,tolerance):
 o=open_.astype(float);h=high.astype(float);c=close.astype(float);a=atr.astype(float);return (c<o)&(c.shift()>o.shift())&((h-h.shift()).abs()<=float(tolerance)*a)
def sig_tweezer_top_short_1h(d,p,ctx):
 pattern=_tweezer_top(d.o,d.h,d.c,d.atr,p['high_tolerance_atr']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['tweezer_top_short_1h']=dict(name='[RESEARCH] Tweezer Top short 1h',style='tweezer_top_candlestick',fn=sig_tweezer_top_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(TWEEZERTOP1H),desc='C189 conventional adjacent equal-high Tweezer Top below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
SHOOTSTAR1H=dict(exit_length=10,cost_multiple=5.0)
def _bearish_shooting_star(open_,high,low,close):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=o-c;upper=h-o;lower=c-l;return (body>0)&(upper>=2.0*body)&(lower<=body)
def sig_shooting_star_short_1h(d,p,ctx):
 pattern=_bearish_shooting_star(d.o,d.h,d.l,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['shooting_star_short_1h']=dict(name='[RESEARCH] Shooting Star short 1h',style='bearish_shooting_star_candlestick',fn=sig_shooting_star_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(SHOOTSTAR1H),desc='C190 classical bearish Shooting Star geometry below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
INVHAMMER1H=dict(exit_length=10,cost_multiple=5.0)
def _bullish_inverted_hammer(open_,high,low,close):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=c-o;upper=h-c;lower=o-l;return (body>0)&(upper>=2.0*body)&(lower<=body)
def sig_inverted_hammer_long_1h(d,p,ctx):
 pattern=_bullish_inverted_hammer(d.o,d.h,d.l,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['inverted_hammer_long_1h']=dict(name='[RESEARCH] Inverted Hammer long 1h',style='bullish_inverted_hammer_candlestick',fn=sig_inverted_hammer_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(INVHAMMER1H),desc='C191 classical bullish Inverted Hammer geometry above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))
HANGMAN1H=dict(exit_length=10,cost_multiple=5.0)
def _bearish_hanging_man(open_,high,low,close):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=o-c;lower=c-l;upper=h-o;return (body>0)&(lower>=2.0*body)&(upper<=body)
def sig_hanging_man_short_1h(d,p,ctx):
 pattern=_bearish_hanging_man(d.o,d.h,d.l,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['hanging_man_short_1h']=dict(name='[RESEARCH] Hanging Man short 1h',style='bearish_hanging_man_candlestick',fn=sig_hanging_man_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(HANGMAN1H),desc='C192 classical bearish Hanging Man geometry below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

MARUBOZU1H=dict(shadow_ratio=.10,exit_length=10,cost_multiple=5.)
def _bullish_marubozu(open_,high,low,close,shadow_ratio=.10):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=c-o
 return (body>0)&((o-l)<=shadow_ratio*body)&((h-c)<=shadow_ratio*body)
def sig_bullish_marubozu_long_1h(d,p,ctx):
 pattern=_bullish_marubozu(d.o,d.h,d.l,d.c,p['shadow_ratio']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['bullish_marubozu_long_1h']=dict(name='[RESEARCH] Bullish Marubozu long 1h',style='bullish_marubozu_candlestick',fn=sig_bullish_marubozu_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(MARUBOZU1H),desc='C193 classical bullish Marubozu geometry above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

BEARMARU1H=dict(shadow_ratio=.10,exit_length=10,cost_multiple=5.)
def _bearish_marubozu(open_,high,low,close,shadow_ratio=.10):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=o-c
 return (body>0)&((h-o)<=shadow_ratio*body)&((c-l)<=shadow_ratio*body)
def sig_bearish_marubozu_short_1h(d,p,ctx):
 pattern=_bearish_marubozu(d.o,d.h,d.l,d.c,p['shadow_ratio']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['bearish_marubozu_short_1h']=dict(name='[RESEARCH] Bearish Marubozu short 1h',style='bearish_marubozu_candlestick',fn=sig_bearish_marubozu_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(BEARMARU1H),desc='C194 classical bearish Marubozu geometry below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

RISING3M1H=dict(body_fraction=.60,exit_length=10,cost_multiple=5.)
def _rising_three_methods(open_,high,low,close,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);o0=o.shift(4);h0=h.shift(4);l0=l.shift(4);c0=c.shift(4)
 first=(c0>o0)&((c0-o0)>=body_fraction*(h0-l0));inner=True
 for k in (3,2,1):inner=inner&(c.shift(k)<o.shift(k))&(h.shift(k)<=h0)&(l.shift(k)>=l0)
 last=(c>o)&((c-o)>=body_fraction*(h-l))&(c>c0)
 return first&inner&last
def sig_rising_three_methods_long_1h(d,p,ctx):
 pattern=_rising_three_methods(d.o,d.h,d.l,d.c,p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['rising_three_methods_long_1h']=dict(name='[RESEARCH] Rising Three Methods long 1h',style='rising_three_methods_candlestick',fn=sig_rising_three_methods_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(RISING3M1H),desc='C195 conventional five-candle contained-pullback continuation above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

FALLING3M1H=dict(body_fraction=.60,exit_length=10,cost_multiple=5.)
def _falling_three_methods(open_,high,low,close,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);o0=o.shift(4);h0=h.shift(4);l0=l.shift(4);c0=c.shift(4)
 first=(o0>c0)&((o0-c0)>=body_fraction*(h0-l0));inner=True
 for k in (3,2,1):inner=inner&(c.shift(k)>o.shift(k))&(h.shift(k)<=h0)&(l.shift(k)>=l0)
 last=(o>c)&((o-c)>=body_fraction*(h-l))&(c<c0)
 return first&inner&last
def sig_falling_three_methods_short_1h(d,p,ctx):
 pattern=_falling_three_methods(d.o,d.h,d.l,d.c,p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['falling_three_methods_short_1h']=dict(name='[RESEARCH] Falling Three Methods short 1h',style='falling_three_methods_candlestick',fn=sig_falling_three_methods_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(FALLING3M1H),desc='C196 conventional five-candle contained-rally continuation below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

BULLBELT1H=dict(shadow_ratio=.10,body_fraction=.60,exit_length=10,cost_multiple=5.)
def _bullish_belt_hold(open_,high,low,close,shadow_ratio=.10,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=c-o;rng=h-l
 return (body>0)&((o-l)<=shadow_ratio*body)&(body>=body_fraction*rng)
def sig_bullish_belt_hold_long_1h(d,p,ctx):
 pattern=_bullish_belt_hold(d.o,d.h,d.l,d.c,p['shadow_ratio'],p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['bullish_belt_hold_long_1h']=dict(name='[RESEARCH] Bullish Belt Hold long 1h',style='bullish_belt_hold_candlestick',fn=sig_bullish_belt_hold_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(BULLBELT1H),desc='C197 conventional bullish opening-side shaven candle above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

BEARBELT1H=dict(shadow_ratio=.10,body_fraction=.60,exit_length=10,cost_multiple=5.)
def _bearish_belt_hold(open_,high,low,close,shadow_ratio=.10,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);body=o-c;rng=h-l
 return (body>0)&((h-o)<=shadow_ratio*body)&(body>=body_fraction*rng)
def sig_bearish_belt_hold_short_1h(d,p,ctx):
 pattern=_bearish_belt_hold(d.o,d.h,d.l,d.c,p['shadow_ratio'],p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['bearish_belt_hold_short_1h']=dict(name='[RESEARCH] Bearish Belt Hold short 1h',style='bearish_belt_hold_candlestick',fn=sig_bearish_belt_hold_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(BEARBELT1H),desc='C198 conventional bearish opening-side shaven candle below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

BULLKICK1H=dict(body_fraction=.60,exit_length=10,cost_multiple=5.)
def _bullish_kicker(open_,high,low,close,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);po=o.shift(1);ph=h.shift(1);pl=l.shift(1);pc=c.shift(1)
 prior=(po>pc)&((po-pc)>=body_fraction*(ph-pl));current=(c>o)&((c-o)>=body_fraction*(h-l))
 return prior&current&(o>=po)
def sig_bullish_kicker_long_1h(d,p,ctx):
 pattern=_bullish_kicker(d.o,d.h,d.l,d.c,p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['bullish_kicker_long_1h']=dict(name='[RESEARCH] Bullish Kicker long 1h',style='bullish_kicker_candlestick',fn=sig_bullish_kicker_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(BULLKICK1H),desc='C199 conventional bullish gap-through-body Kicker above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

BEARKICK1H=dict(body_fraction=.60,exit_length=10,cost_multiple=5.)
def _bearish_kicker(open_,high,low,close,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);po=o.shift(1);ph=h.shift(1);pl=l.shift(1);pc=c.shift(1)
 prior=(pc>po)&((pc-po)>=body_fraction*(ph-pl));current=(o>c)&((o-c)>=body_fraction*(h-l))
 return prior&current&(o<=po)
def sig_bearish_kicker_short_1h(d,p,ctx):
 pattern=_bearish_kicker(d.o,d.h,d.l,d.c,p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['bearish_kicker_short_1h']=dict(name='[RESEARCH] Bearish Kicker short 1h',style='bearish_kicker_candlestick',fn=sig_bearish_kicker_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(BEARKICK1H),desc='C200 conventional bearish gap-through-body Kicker below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

BULLSEP1H=dict(open_tolerance_atr=.10,exit_length=10,cost_multiple=5.)
def _bullish_separating_lines(open_,close,atr,tolerance=.10):
 o=open_.astype(float);c=close.astype(float);a=atr.astype(float);po=o.shift(1);pc=c.shift(1)
 return (pc<po)&(c>o)&((o-po).abs()<=tolerance*a)&(c>po)
def sig_bullish_separating_lines_long_1h(d,p,ctx):
 pattern=_bullish_separating_lines(d.o,d.c,d.atr,p['open_tolerance_atr']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['bullish_separating_lines_long_1h']=dict(name='[RESEARCH] Bullish Separating Lines long 1h',style='bullish_separating_lines_candlestick',fn=sig_bullish_separating_lines_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(BULLSEP1H),desc='C201 conventional matched-open bullish continuation above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

BEARSEP1H=dict(open_tolerance_atr=.10,exit_length=10,cost_multiple=5.)
def _bearish_separating_lines(open_,close,atr,tolerance=.10):
 o=open_.astype(float);c=close.astype(float);a=atr.astype(float);po=o.shift(1);pc=c.shift(1)
 return (pc>po)&(c<o)&((o-po).abs()<=tolerance*a)&(c<po)
def sig_bearish_separating_lines_short_1h(d,p,ctx):
 pattern=_bearish_separating_lines(d.o,d.c,d.atr,p['open_tolerance_atr']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['bearish_separating_lines_short_1h']=dict(name='[RESEARCH] Bearish Separating Lines short 1h',style='bearish_separating_lines_candlestick',fn=sig_bearish_separating_lines_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(BEARSEP1H),desc='C202 conventional matched-open bearish continuation below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

MATCHLOW1H=dict(close_tolerance_atr=.10,body_fraction=.60,exit_length=10,cost_multiple=5.)
def _matching_low(open_,high,low,close,atr,tolerance=.10,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);a=atr.astype(float);po=o.shift(1);ph=h.shift(1);pl=l.shift(1);pc=c.shift(1)
 prior=(po>pc)&((po-pc)>=body_fraction*(ph-pl));return prior&(o>c)&((c-pc).abs()<=tolerance*a)
def sig_matching_low_long_1h(d,p,ctx):
 pattern=_matching_low(d.o,d.h,d.l,d.c,d.atr,p['close_tolerance_atr'],p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['matching_low_long_1h']=dict(name='[RESEARCH] Matching Low long 1h',style='matching_low_candlestick',fn=sig_matching_low_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(MATCHLOW1H),desc='C203 conventional adjacent matched bearish closes above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

MATCHHIGH1H=dict(close_tolerance_atr=.10,body_fraction=.60,exit_length=10,cost_multiple=5.)
def _matching_high(open_,high,low,close,atr,tolerance=.10,body_fraction=.60):
 o=open_.astype(float);h=high.astype(float);l=low.astype(float);c=close.astype(float);a=atr.astype(float);po=o.shift(1);ph=h.shift(1);pl=l.shift(1);pc=c.shift(1)
 prior=(pc>po)&((pc-po)>=body_fraction*(ph-pl));return prior&(c>o)&((c-pc).abs()<=tolerance*a)
def sig_matching_high_short_1h(d,p,ctx):
 pattern=_matching_high(d.o,d.h,d.l,d.c,d.atr,p['close_tolerance_atr'],p['body_fraction']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['matching_high_short_1h']=dict(name='[RESEARCH] Matching High short 1h',style='matching_high_candlestick',fn=sig_matching_high_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(MATCHHIGH1H),desc='C204 conventional adjacent matched bullish closes below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

HOMING1H=dict(exit_length=10,cost_multiple=5.)
def _homing_pigeon(open_,close):
 o=open_.astype(float);c=close.astype(float);po=o.shift(1);pc=c.shift(1)
 return (po>pc)&(o>c)&(o<po)&(c>pc)
def sig_homing_pigeon_long_1h(d,p,ctx):
 pattern=_homing_pigeon(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['homing_pigeon_long_1h']=dict(name='[RESEARCH] Homing Pigeon long 1h',style='homing_pigeon_candlestick',fn=sig_homing_pigeon_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(HOMING1H),desc='C205 conventional nested bearish-body reversal above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

DESCHAWK1H=dict(exit_length=10,cost_multiple=5.)
def _descending_hawk(open_,close):
 o=open_.astype(float);c=close.astype(float);po=o.shift(1);pc=c.shift(1)
 return (pc>po)&(c>o)&(o>po)&(c<pc)
def sig_descending_hawk_short_1h(d,p,ctx):
 pattern=_descending_hawk(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['descending_hawk_short_1h']=dict(name='[RESEARCH] Descending Hawk short 1h',style='descending_hawk_candlestick',fn=sig_descending_hawk_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(DESCHAWK1H),desc='C206 conventional nested bullish-body reversal below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

STICKSAND1H=dict(close_tolerance_atr=.10,exit_length=10,cost_multiple=5.)
def _stick_sandwich(open_,close,atr,tolerance=.10):
 o=open_.astype(float);c=close.astype(float);a=atr.astype(float);o2=o.shift(2);c2=c.shift(2);o1=o.shift(1);c1=c.shift(1)
 return (c2<o2)&(c1>o1)&(o1>c2)&(o>c)&(o>c1)&((c-c2).abs()<=tolerance*a)
def sig_stick_sandwich_long_1h(d,p,ctx):
 pattern=_stick_sandwich(d.o,d.c,d.atr,p['close_tolerance_atr']);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['stick_sandwich_long_1h']=dict(name='[RESEARCH] Stick Sandwich long 1h',style='stick_sandwich_candlestick',fn=sig_stick_sandwich_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(STICKSAND1H),desc='C207 conventional three-candle matched-close support reversal above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

THREEINUP1H=dict(exit_length=10,cost_multiple=5.)
def _three_inside_up(open_,close):
 o=open_.astype(float);c=close.astype(float);o2=o.shift(2);c2=c.shift(2);o1=o.shift(1);c1=c.shift(1)
 return (o2>c2)&(c1>o1)&(o1>c2)&(c1<o2)&(c>o)&(c>o2)
def sig_three_inside_up_long_1h(d,p,ctx):
 pattern=_three_inside_up(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=pattern&(d.c>d.e200)&cost,se=never,lx=d.c<=mid,sx=never)
STRATEGIES['three_inside_up_long_1h']=dict(name='[RESEARCH] Three Inside Up long 1h',style='three_inside_up_candlestick',fn=sig_three_inside_up_long_1h,sides='long',tf='1h',research_only=True,reentry_lock=True,params=dict(THREEINUP1H),desc='C208 conventional Harami-core bullish confirmation above EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))

THREEINDOWN1H=dict(exit_length=10,cost_multiple=5.)
def _three_inside_down(open_,close):
 o=open_.astype(float);c=close.astype(float);o2=o.shift(2);c2=c.shift(2);o1=o.shift(1);c1=c.shift(1)
 return (c2>o2)&(o1>c1)&(o1<c2)&(c1>o2)&(o>c)&(c<o2)
def sig_three_inside_down_short_1h(d,p,ctx):
 pattern=_three_inside_down(d.o,d.c);mid=d.c.astype(float).rolling(int(p['exit_length']),min_periods=int(p['exit_length'])).mean();cost=2*d.atr/d.c>=p['cost_multiple']*2*(.0005+.0002);never=d.c<-1;return dict(le=never,se=pattern&(d.c<d.e200)&cost,lx=never,sx=d.c>=mid)
STRATEGIES['three_inside_down_short_1h']=dict(name='[RESEARCH] Three Inside Down short 1h',style='three_inside_down_candlestick',fn=sig_three_inside_down_short_1h,sides='short',tf='1h',research_only=True,reentry_lock=True,params=dict(THREEINDOWN1H),desc='C209 conventional Harami-core bearish confirmation below EMA200.',mgmt=dict(stop_atr=2.,tp_r=2.,max_bars=24))


# Research-only additions stay below the frozen strategy-source line anchors used by ML-TRAIN-01.
FIB = dict(left=3, right=3, min_impulse_atr=4.0, zone_lo=0.382, trigger=0.5, zone_hi=0.618, invalid=0.786)


def sig_fib_pullback(d, p, ctx):
    """Causal confirmed-swing Fibonacci pullback candidate; research-only."""
    left, right = int(p.get('left', FIB['left'])), int(p.get('right', FIB['right']))
    if left < 1 or right < 1: raise ValueError('fib_pullback: left/right must be >= 1')
    zlo, trig = float(p.get('zone_lo', FIB['zone_lo'])), float(p.get('trigger', FIB['trigger']))
    zhi, inval = float(p.get('zone_hi', FIB['zone_hi'])), float(p.get('invalid', FIB['invalid']))
    if not (0 < zlo < trig < zhi < inval < 1):
        raise ValueError('fib_pullback: require 0 < zone_lo < trigger < zone_hi < invalid < 1')
    n, w = len(d), left + right + 1
    ch, cl = d.h.shift(right), d.l.shift(right)
    ph = (ch == d.h.rolling(w).max()).fillna(False).to_numpy(bool)
    pl = (cl == d.l.rolling(w).min()).fillna(False).to_numpy(bool)
    h, l, o, c, a = (d[k].to_numpy(float) for k in ('h', 'l', 'o', 'c', 'atr'))
    e50, e200 = d.e50.to_numpy(float), d.e200.to_numpy(float)
    le = np.zeros(n, bool); se = np.zeros(n, bool); lx = np.zeros(n, bool); sx = np.zeros(n, bool)
    dist = np.asarray(a, float).copy()
    hi_i = lo_i = -1; hi = lo = np.nan
    long_arm = short_arm = False
    long_tag = short_tag = None
    min_x = float(p.get('min_impulse_atr', FIB['min_impulse_atr']))
    for i in range(n):
        j = i - right
        if ph[i] and j >= 0: hi_i, hi, long_arm = j, h[j], False
        if pl[i] and j >= 0: lo_i, lo, short_arm = j, l[j], False
        if lo_i >= 0 and hi_i > lo_i and np.isfinite(a[i]) and hi > lo:
            span = hi - lo; tag = (lo_i, hi_i)
            f382, f500, f618, f786 = hi - zlo * span, hi - trig * span, hi - zhi * span, hi - inval * span
            valid = span >= min_x * a[i] and e50[i] > e200[i]
            if long_tag != tag: long_tag, long_arm = tag, False
            if c[i] <= f786 or not valid: long_arm = False
            elif l[i] <= f382 and h[i] >= f618: long_arm = True
            if long_arm and i and c[i - 1] <= f500 < c[i] and c[i] > o[i]:
                le[i] = True; dist[i] = c[i] - f786; long_arm = False
            lx[i] = (c[i] <= f786) or (e50[i] <= e200[i])
        if hi_i >= 0 and lo_i > hi_i and np.isfinite(a[i]) and hi > lo:
            span = hi - lo; tag = (hi_i, lo_i)
            f382, f500, f618, f786 = lo + zlo * span, lo + trig * span, lo + zhi * span, lo + inval * span
            valid = span >= min_x * a[i] and e50[i] < e200[i]
            if short_tag != tag: short_tag, short_arm = tag, False
            if c[i] >= f786 or not valid: short_arm = False
            elif h[i] >= f382 and l[i] <= f618: short_arm = True
            if short_arm and i and c[i - 1] >= f500 > c[i] and c[i] < o[i]:
                se[i] = True; dist[i] = f786 - c[i]; short_arm = False
            sx[i] = (c[i] >= f786) or (e50[i] >= e200[i])
    out = _one_side(dict(le=pd.Series(le, d.index), se=pd.Series(se, d.index),
                         lx=pd.Series(lx, d.index), sx=pd.Series(sx, d.index)))
    out['atr'] = pd.Series(np.where(np.isfinite(dist) & (dist > 0), dist, np.nan), index=d.index)
    return out


STRATEGIES['fib_pullback'] = dict(
    name='[RESEARCH] Fibonacci pullback L/S', style='balanced', fn=sig_fib_pullback, sides='both', tf='4h',
    research_only=True, params=dict(FIB),
    desc='RESEARCH-ONLY: confirmed 3/3 swings, trend-qualified 0.382-0.618 retracement, 0.5 reclaim entry and 0.786 '
         'structural invalidation. Long/short mirrored; excluded from PAPER until historical, walk-forward and '
         'untouched-holdout gates pass.',
    mgmt=dict(stop_atr=1.0, tp1_r=1.0, tp1_frac=0.5, be_r=1.0, tp_r=2.0, max_bars=30),
)


# FIB-PULLBACK-02 (research-only, Codex 6486879287): v1 above stays byte-identical; this is a new key and a new frozen
# parameter set. The economic change is the structure/participation confirmation of the entry candle.
FIB02 = dict(left=2, right=2, min_impulse_atr=3.0, zone_lo=0.382, trigger=0.5, zone_hi=0.618, invalid=0.786,
             body_atr=0.35, vol_median_bars=20)


def fib02_pivots(d, left=FIB02['left'], right=FIB02['right']):
    """(ph, pl): ph[i] True when the candle i - right is a confirmed swing high (its high is the max of the `left` candles
    before it, itself and the `right` candles after it). It is set on candle i, i.e. only once the right-th candle after
    the pivot has CLOSED; pl mirrored. Rolling windows read candles <= i only."""
    w = left + right + 1
    ph = (d.h.shift(right) == d.h.rolling(w).max()).fillna(False).to_numpy(bool)
    pl = (d.l.shift(right) == d.l.rolling(w).min()).fillna(False).to_numpy(bool)
    return ph, pl


def sig_fib_pullback_02(d, p, ctx):
    """Causal confirmed 2/2-swing Fibonacci continuation with structure/participation confirmation; research-only.
    Per side and per confirmed impulse (latest confirmed low -> high for longs, high -> low for shorts):
      valid  = impulse >= min_impulse_atr x ATR(14) and EMA50/EMA200 aligned with the side (checked every candle);
      arming = a candle overlaps the zone_lo..zone_hi retracement while valid and closing beyond `invalid`; arming only
               counts from the NEXT candle on (the entry candle cannot arm itself);
      entry  = armed, valid, and the closed candle: crosses `trigger` in the trend direction (prior close at/through it),
               closes beyond the prior candle's high (long) / low (short), has a with-trend real body >= body_atr x ATR,
               and volume >= the median of the vol_median_bars candles before it. One entry per impulse;
      disarm = a close at/through `invalid` (0.786), or the impulse turning invalid.
    0.786 is the DECISION-TIME invalidation and the sizing anchor: the emitted 'atr' is the decision close's distance to
    it (mgmt stop_atr = 1). The backtester opens on the next candle with slippage and places its stop that distance from
    the fill, so the resting stop shifts by the entry gap / slippage and is not guaranteed to sit exactly at L(0.786).
    The exit signal (lx / sx) on a close at/through L(0.786) is kept."""
    left, right = int(p.get('left', FIB02['left'])), int(p.get('right', FIB02['right']))
    if left < 1 or right < 1: raise ValueError('fib_pullback_02: left/right must be >= 1')
    zlo, trig = float(p.get('zone_lo', FIB02['zone_lo'])), float(p.get('trigger', FIB02['trigger']))
    zhi, inval = float(p.get('zone_hi', FIB02['zone_hi'])), float(p.get('invalid', FIB02['invalid']))
    if not (0 < zlo < trig < zhi < inval < 1):
        raise ValueError('fib_pullback_02: require 0 < zone_lo < trigger < zone_hi < invalid < 1')
    min_x, body_x = float(p.get('min_impulse_atr', FIB02['min_impulse_atr'])), float(p.get('body_atr', FIB02['body_atr']))
    nvol = int(p.get('vol_median_bars', FIB02['vol_median_bars']))
    if nvol < 1: raise ValueError('fib_pullback_02: vol_median_bars must be >= 1')
    n = len(d)
    ph, pl = fib02_pivots(d, left, right)
    h, l, o, c, a = (d[k].to_numpy(float) for k in ('h', 'l', 'o', 'c', 'atr'))
    e50, e200 = d.e50.to_numpy(float), d.e200.to_numpy(float)
    v = d.v.astype(float)
    vmed = v.shift(1).rolling(nvol).median().to_numpy(float)       # the nvol candles BEFORE this one
    vol_ok = np.isfinite(vmed) & (v.to_numpy(float) >= vmed)
    le = np.zeros(n, bool); se = np.zeros(n, bool); lx = np.zeros(n, bool); sx = np.zeros(n, bool)
    dist = np.asarray(a, float).copy()                             # entries overwrite with the 0.786 distance (as v1)
    hi_i = lo_i = -1; hi = lo = np.nan
    long_arm = short_arm = False
    long_tag = short_tag = long_done = short_done = None
    for i in range(n):
        j = i - right
        if ph[i] and j >= 0: hi_i, hi = j, h[j]
        if pl[i] and j >= 0: lo_i, lo = j, l[j]
        ok_a = np.isfinite(a[i]) and a[i] > 0
        if lo_i >= 0 and hi_i > lo_i and ok_a and hi > lo:
            span = hi - lo; tag = (lo_i, hi_i)
            f382, f500, f618, f786 = hi - zlo * span, hi - trig * span, hi - zhi * span, hi - inval * span
            valid = span >= min_x * a[i] and e50[i] > e200[i]
            if long_tag != tag: long_tag, long_arm = tag, False
            if (long_arm and valid and long_done != tag and i and c[i] > f786
                    and c[i - 1] <= f500 < c[i]                      # reclaims 0.5 upward
                    and c[i] > h[i - 1]                              # closes above the prior candle's high
                    and c[i] - o[i] >= body_x * a[i]                 # bullish real body >= 0.35 ATR
                    and vol_ok[i]):                                  # volume >= trailing 20-candle median
                le[i] = True; dist[i] = c[i] - f786; long_arm = False; long_done = tag
            elif c[i] <= f786 or not valid: long_arm = False
            elif l[i] <= f382 and h[i] >= f618: long_arm = True     # armed for the following candles
            lx[i] = (c[i] <= f786) or (e50[i] <= e200[i])
        if hi_i >= 0 and lo_i > hi_i and ok_a and hi > lo:
            span = hi - lo; tag = (hi_i, lo_i)
            f382, f500, f618, f786 = lo + zlo * span, lo + trig * span, lo + zhi * span, lo + inval * span
            valid = span >= min_x * a[i] and e50[i] < e200[i]
            if short_tag != tag: short_tag, short_arm = tag, False
            if (short_arm and valid and short_done != tag and i and c[i] < f786
                    and c[i - 1] >= f500 > c[i]
                    and c[i] < l[i - 1]
                    and o[i] - c[i] >= body_x * a[i]
                    and vol_ok[i]):
                se[i] = True; dist[i] = f786 - c[i]; short_arm = False; short_done = tag
            elif c[i] >= f786 or not valid: short_arm = False
            elif h[i] >= f382 and l[i] <= f618: short_arm = True
            sx[i] = (c[i] >= f786) or (e50[i] >= e200[i])
    out = _one_side(dict(le=pd.Series(le, d.index), se=pd.Series(se, d.index),
                         lx=pd.Series(lx, d.index), sx=pd.Series(sx, d.index)))
    out['atr'] = pd.Series(np.where(np.isfinite(dist) & (dist > 0), dist, np.nan), index=d.index)
    return out


STRATEGIES['fib_pullback_02'] = dict(
    name='[RESEARCH] Fibonacci pullback v2 L/S 1h', style='balanced', fn=sig_fib_pullback_02, sides='both', tf='1h',
    research_only=True, params=dict(FIB02),
    desc='RESEARCH-ONLY (FIB-PULLBACK-02): 1h, confirmed 2/2 swings, impulse >= 3 ATR with aligned EMA50/EMA200, armed by '
         'a 0.382-0.618 overlap; entry only when a closed candle reclaims 0.5, closes beyond the prior candle, has a '
         '>= 0.35 ATR body and >= median-20 volume; 0.786 decision-time invalidation / sizing anchor with a close-through exit. Excluded from PAPER.',
    mgmt=dict(stop_atr=1.0, tp1_r=1.0, tp1_frac=0.5, be_r=1.0, tp_r=2.0, max_bars=30),
)
