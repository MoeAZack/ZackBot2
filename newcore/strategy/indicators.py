"""Pure, causal indicator math for NEWCORE strategies (float internally, stdlib only).

Every function takes plain sequences of floats (closed candles, oldest first) and returns a tuple of the same length.
Value i depends only on inputs 0..i, so truncating the future never changes a past value (bit-for-bit: the recursions
below run in a fixed order with no look-ahead and no ambient state). Undefined values are None, never NaN.

Definitions (parity with legacy strategies.py, checked to 1e-9 by tests/newcore_strategy):
  ema(x, n)        pandas `x.ewm(span=n, adjust=False).mean()`: y0 = x0, y_i = (1-a) * y_{i-1} + a * x_i, a = 2/(n+1)
  true_range       max(h-l, |h-c_{i-1}|, |l-c_{i-1}|); bar 0 has no previous close, so TR0 = h0 - l0 (pandas skips NaN)
  wilder_atr(n)    `TR.ewm(alpha=1/n, adjust=False).mean()` seeded at TR0, exactly as legacy `indicators()['atr']`
  pct_return(c,k)  c_i / c_{i-k} - 1, None while i < k
  cross_up(a, b)   a_i > b_i and a_{i-1} <= b_{i-1}   (legacy `xup`; False at bar 0 or where any input is None)

Note on the recursions: pandas evaluates `((1-a)*y + a*x) / ((1-a) + a)`; the denominator is 1 up to one rounding, so the
two agree to ~1e-15 relative. Parity is asserted at 1e-9 against pandas, never bitwise.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

FloatSeq = Sequence[float]
OptTuple = Tuple[Optional[float], ...]


def ema(x: FloatSeq, n: int) -> Tuple[float, ...]:
    if n < 1:
        raise ValueError(f'ema length must be >= 1, got {n}')
    a = 2.0 / (n + 1.0)
    out = []
    y = None
    for v in x:
        v = float(v)
        y = v if y is None else (1.0 - a) * y + a * v
        out.append(y)
    return tuple(out)


def true_range(h: FloatSeq, l: FloatSeq, c: FloatSeq) -> Tuple[float, ...]:
    if not (len(h) == len(l) == len(c)):
        raise ValueError('h, l, c must have equal length')
    out = []
    for i in range(len(c)):
        hi, lo = float(h[i]), float(l[i])
        if i == 0:
            out.append(hi - lo)
        else:
            pc = float(c[i - 1])
            out.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
    return tuple(out)


def wilder_atr(h: FloatSeq, l: FloatSeq, c: FloatSeq, n: int = 14) -> Tuple[float, ...]:
    if n < 1:
        raise ValueError(f'atr length must be >= 1, got {n}')
    a = 1.0 / n
    out = []
    y = None
    for tr in true_range(h, l, c):
        y = tr if y is None else (1.0 - a) * y + a * tr
        out.append(y)
    return tuple(out)


def pct_return(c: FloatSeq, k: int) -> OptTuple:
    if k < 1:
        raise ValueError(f'return lookback must be >= 1 bar, got {k}')
    return tuple(None if i < k else float(c[i]) / float(c[i - k]) - 1.0 for i in range(len(c)))


def cross_up(a: Sequence[Optional[float]], b: Sequence[Optional[float]]) -> Tuple[bool, ...]:
    if len(a) != len(b):
        raise ValueError('cross_up inputs must have equal length')
    out = [False] * len(a)
    for i in range(1, len(a)):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            continue
        out[i] = a[i] > b[i] and a[i - 1] <= b[i - 1]
    return tuple(out)
