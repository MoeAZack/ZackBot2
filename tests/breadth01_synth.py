"""BREADTH-01 B1: deterministic synthetic OHLCV(+qv) tapes for the registry-wide quick-test harness.

Synthetic only: no store, archive, network or real candles. A tape is a slow drift with a sine swing on top (trend
pullbacks, channel breakouts, range reversions) plus small seeded noise; `mirror` reflects prices so every long shape has
an exact short twin. Volume grows with the candle's move (volume surges on breakouts) and qv = typical price x volume.
"""
import numpy as np
import pandas as pd

FREQ = {'4h': '4h', '1h': '1h', '15m': '15min'}


def tape(n=600, dr=0.004, A=0.10, P=40.0, seed=1, noise=0.003, tf='4h', scale=100.0, t0='2024-01-01'):
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    r = rng.normal(0, noise, n)
    c = scale * np.exp(dr * i + A * np.sin(2 * np.pi * i / P) + np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, noise, n)) * c + 1e-6 * c
    h, l = np.maximum(o, c) + w, np.minimum(o, c) - w
    mv = np.abs(np.log(c / o))
    v = 200.0 + 1000.0 * (mv / (mv.mean() + 1e-12)) ** 1.5
    t = pd.date_range(t0, periods=n, freq=FREQ[tf])
    return pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=v, qv=(h + l + c) / 3 * v))


def mirror(raw, M=None):
    """Price reflection about M: o,c -> M-o, M-c; h <-> M-l. Volume unchanged, qv recomputed from the new typical price."""
    M = 2.0 * float(raw.h.max()) if M is None else M
    out = pd.DataFrame(dict(t=raw.t, o=M - raw.o, h=M - raw.l, l=M - raw.h, c=M - raw.c, v=raw.v))
    out['qv'] = (out.h + out.l + out.c) / 3 * out.v
    return out


def book_raw(shape, syms=('BTCUSDT', 'ETHUSDT', 'SOLUSDT'), short=False):
    """One tape per symbol (same timestamps, different seed / scale); short=True mirrors every symbol (BTC down)."""
    out = {}
    for j, s in enumerate(syms):
        kw = dict(shape); kw['seed'] = kw.get('seed', 1) + 7 * j; kw['scale'] = 100.0 * (j + 1)
        raw = tape(**kw)
        out[s] = mirror(raw) if short else raw
    return out
