"""MLDATA-01: causal cross-sectional decision context for features_at (funding, rank, breadth, corr_count).

Everything is computed for ONE decision candle of `sym` with open time T, from data at or before that candle only:
  frames   {symbol: candles (t = open time, c = close)} - every frame is sliced to rows with t <= T before use;
  funding  [(fundingTime ms, rate, ...)] settled rows of sym - only rows with fundingTime <= the decision candle CLOSE count;
  others   symbols of the currently open lots (corr_count).
A value that cannot be computed is None (features_at puts it in the missing-mask); nothing is filled or defaulted. Pure: no
I/O, no clock, never raises (a failed piece is None)."""
import math

import numpy as np
import pandas as pd

from .common import to_utc

RANK_N = 42            # return lookback of the cross-sectional rank (strategies.build_context uses the same 42 bars)
BREADTH_EMA = 50       # breadth = share of symbols closing above their EMA50 at T
CORR_N = 30            # return window of corr_count
CORR_MIN = 0.7         # an open lot counts as correlated at or above this
MIN_SYMBOLS = 2        # rank / breadth need at least this many symbols with enough history at T


def _upto(df, t_ns):
    """Closes of df with open time <= T (int ns), or None."""
    if df is None or not len(df) or 'c' not in df or 't' not in df: return None
    col = df['t']
    try:                                                # vectorised; ints are epoch MILLISECONDS (common.to_utc's rule)
        ts = (pd.to_datetime(col, unit='ms', utc=True) if pd.api.types.is_integer_dtype(col)
              else pd.to_datetime(col, utc=True))
        keep = (ts <= pd.Timestamp(t_ns, tz='UTC')).to_numpy()   # compared as datetimes: any resolution (ns/us/ms) is safe
    except Exception:
        return None
    c = df['c'].to_numpy(float)[keep]
    return c if len(c) else None


def _ema_last(c, n):
    if len(c) < n: return None
    a = 2.0 / (n + 1); e = float(np.mean(c[:n]))
    for x in c[n:]: e = a * float(x) + (1 - a) * e
    return e


def _finite(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def funding_at(rows, close_ms):
    """Last settled rate with fundingTime <= close_ms (never a later or predicted one), else None."""
    best = None
    for r in rows or ():
        try:
            t = int(r[0])
        except (TypeError, ValueError, IndexError):
            continue
        if t <= close_ms and (best is None or t > best[0]): best = (t, r[1])
    return _finite(best[1]) if best else None


def rank_at(closes, sym):
    """1 = the highest RANK_N-bar return among the symbols with enough history at T; None when sym is not ranked."""
    ret = {s: c[-1] / c[-RANK_N - 1] - 1 for s, c in closes.items() if c is not None and len(c) > RANK_N and c[-RANK_N - 1] > 0}
    ret = {s: r for s, r in ret.items() if math.isfinite(r)}
    if sym not in ret or len(ret) < MIN_SYMBOLS: return None
    return 1 + sum(1 for s, r in ret.items() if r > ret[sym])


def breadth_at(closes):
    """Share of symbols whose close is above their EMA50 at T; None with fewer than MIN_SYMBOLS measurable symbols."""
    above = [c[-1] > e for c in closes.values() if c is not None for e in [_ema_last(c, BREADTH_EMA)] if e is not None]
    return (sum(above) / len(above)) if len(above) >= MIN_SYMBOLS else None


def corr_count_at(closes, sym, others):
    """Open lots (other symbols) whose CORR_N-bar return correlation with sym is >= CORR_MIN at T. 0 with no other lots;
    None when sym, or any open-lot symbol, lacks the history to measure it (an unmeasured lot is never counted as 0)."""
    others = sorted({o for o in others or () if o != sym})
    if not others: return 0
    def rets(c):
        return np.diff(np.log(c[-CORR_N - 1:])) if c is not None and len(c) > CORR_N and np.all(c[-CORR_N - 1:] > 0) else None
    a = rets(closes.get(sym))
    if a is None: return None
    n = 0
    for o in others:
        b = rets(closes.get(o))
        if b is None or np.std(a) == 0 or np.std(b) == 0: return None
        if float(np.corrcoef(a, b)[0, 1]) >= CORR_MIN: n += 1
    return n


def decision_context(sym, t, tf_s, frames, funding_rows=None, others=()):
    """-> {'funding', 'rank', 'breadth', 'corr_count'} for the decision candle of sym with open time t (each None when
    unavailable). tf_s: candle length in seconds (the decision candle closes at t + tf_s)."""
    out = dict(funding=None, rank=None, breadth=None, corr_count=None)
    try:
        t_ns = to_utc(t).value
    except Exception:
        return out
    closes = {}
    for s, df in (frames or {}).items():
        try: closes[s] = _upto(df, t_ns)
        except Exception: closes[s] = None
    for k, fn in (('funding', lambda: funding_at(funding_rows, t_ns // 1_000_000 + int(tf_s) * 1000)),
                  ('rank', lambda: rank_at(closes, sym)), ('breadth', lambda: breadth_at(closes)),
                  ('corr_count', lambda: corr_count_at(closes, sym, others))):
        try: out[k] = fn()
        except Exception: out[k] = None
    return out
