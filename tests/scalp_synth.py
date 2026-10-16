"""SCALP-4 synthetic 15m fixtures (deterministic, no network / real data). Each builder returns raw candles (t,o,h,l,c,v)
whose LAST candle is a designed long setup for one scalp strategy; mirror() reflects prices so the same candle becomes the
designed short setup (h <-> l swap, volume unchanged)."""
import numpy as np
import pandas as pd

import strategies as S

N = 300
K = 200.0                                           # reflection level: p -> K - p (prices stay positive around 100)


def _frame(o, h, l, c, v):
    t = pd.date_range('2026-01-05', periods=len(c), freq='15min')
    return pd.DataFrame(dict(t=t, o=np.asarray(o, float), h=np.asarray(h, float), l=np.asarray(l, float),
                             c=np.asarray(c, float), v=np.asarray(v, float)))


def range_base(n=N, base=100.0, amp=1.0, period=16, seed=1):
    """A flat oscillating range: no drift, period-16 sine, small deterministic noise, steady volume."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    c = base + amp * np.sin(2 * np.pi * i / period) + rng.normal(0, 0.03, n)
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.15, np.minimum(o, c) - 0.15
    v = 1000 + rng.uniform(-50, 50, n)
    return _frame(o, h, l, c, v)


def trend_base(n=N, base=100.0, slope=0.04, seed=2):
    """A steady up-trend with a small oscillation (EMA20 > EMA50, close > EMA50)."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    c = base + slope * i + 0.25 * np.sin(2 * np.pi * i / 10) + rng.normal(0, 0.02, n)
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.08, np.minimum(o, c) - 0.08
    v = 1000 + rng.uniform(-50, 50, n)
    return _frame(o, h, l, c, v)


def append(df, o, h, l, c, v=None):
    t = df.t.iloc[-1] + pd.Timedelta(minutes=15)
    row = pd.DataFrame(dict(t=[t], o=[float(o)], h=[float(h)], l=[float(l)], c=[float(c)],
                            v=[float(df.v.iloc[-20:].mean() if v is None else v)]))
    return pd.concat([df, row], ignore_index=True)


def mirror(df):
    return _frame(K - df.o, K - df.l, K - df.h, K - df.c, df.v).assign(t=df.t.values)


def shift_price(df, k):
    """Same shapes at a higher price level (same ATR in price units -> a much smaller ATR %)."""
    return df.assign(o=df.o + k, h=df.h + k, l=df.l + k, c=df.c + k)


# ------------------------------------------------------------------ designed long setups (last candle)
def range_long(df=None, n=S.RS['n']):
    df = range_base() if df is None else df
    hi, lo = df.h.iloc[-n:].max(), df.l.iloc[-n:].min()
    w = hi - lo
    o, c = lo + 0.06 * w, lo + 0.18 * w
    return append(df, o, c + 0.02 * w, lo + 0.02 * w, c)


def failed_breakout_long(df=None, n=S.FB['n']):
    """Two candles: a close below the 20-candle range low (break), then a close back inside (the failed break)."""
    df = range_base() if df is None else df
    lo, hi = df.l.iloc[-n:].min(), df.h.iloc[-n:].max()
    last = df.c.iloc[-1]
    df = append(df, last, last + 0.05, lo - 0.9, lo - 0.6)                  # break: close 0.6 below the range low
    return append(df, lo - 0.6, lo + 0.4, lo - 0.7, lo + 0.3)                # back inside


def momentum_long(df=None):
    """An impulse candle: range ~4x and volume 3x the 20-candle averages, closing at its top."""
    df = range_base() if df is None else df
    rng = float((df.h - df.l).iloc[-20:].mean())
    o = float(df.c.iloc[-1])
    return append(df, o, o + 4 * rng, o - 0.05 * rng, o + 3.9 * rng, v=3 * float(df.v.iloc[-20:].mean()))


def qbank_long(df=None):
    """Up-trend; the last candle dips well below EMA9 and closes back above the previous EMA9 as a bullish candle."""
    df = trend_base() if df is None else df
    d = S.indicators(df)
    e9, pc = float(d.e9.iloc[-1]), float(df.c.iloc[-1])
    c = max(pc, e9) + 0.05
    return append(df, e9 - 0.05, c + 0.03, e9 - 0.4, c)


# ------------------------------------------------------------------ Wave B1: htf_pullback_scalp
def htf_base(n=1100, base=78.0, slope=0.04, seed=3):
    """A steady 15m up-trend (4h close > EMA50, EMA20 > EMA50 after warm-up) with a period-16 swing; starts on a 4h
    boundary. Prices stay near 100 so the mirror (K - p) has the same cost-gate scale."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    c = base + slope * i + 0.5 * np.sin(2 * np.pi * i / 16) + rng.normal(0, 0.02, n)
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.3, np.minimum(o, c) - 0.3
    v = 1000 + rng.uniform(-50, 50, n)
    return _frame(o, h, l, c, v)


def htf_long(df=None):
    """Up-trend; two candles pull back and close below the 15m EMA20, then the last candle closes back above it at its top."""
    df = htf_base() if df is None else df
    for _ in range(2):
        e20 = float(S.indicators(df).e20.iloc[-1])
        o = float(df.c.iloc[-1]); c = min(o, e20) - 0.4
        df = append(df, o, o + 0.1, c - 0.1, c)
    e20 = float(S.indicators(df).e20.iloc[-1])
    o = float(df.c.iloc[-1]); c = e20 + 0.6
    return append(df, o, c + 0.05, o - 0.1, c)
