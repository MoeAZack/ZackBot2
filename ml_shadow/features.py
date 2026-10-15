"""Causal decision-time features. `features_at(df, i, ctx)` reads ONLY rows 0..i of df (it slices first), never fills.

df: candles with columns t (open time: datetime/Timestamp/ISO str, or int = epoch MILLISECONDS), o, h, l, c, v and optionally qv (quote volume). Row i is the decision candle.
ctx (optional, whitelisted keys only - anything else, including secrets, is ignored):
  regime (str), funding (float), btc_dir (float, -1..+1) or btc_df (candles; only rows with t <= df.t[i] are used),
  breadth (float), rank (int), exposure_count (int), corr_count (int).
Missing values are explicit None and listed in `missing` (the missing-mask). No forward/back fill anywhere.
"""
import math
import numpy as np
import pandas as pd

from .common import to_utc

EMA_FAST, EMA_SLOW, SLOPE_LAG, ATR_N, VOL_N, HI_N, VWAP_N = 20, 50, 5, 14, 20, 20, 20
NUMERIC = ['ema_slope', 'atr_pct', 'vol_ratio', 'qv_ratio', 'breakout_dist', 'stretch', 'vwap_dist', 'btc_dir',
           'funding', 'breadth', 'rank', 'exposure_count', 'corr_count']
CATEGORICAL = ['regime', 'session']
FEATURE_NAMES = NUMERIC + CATEGORICAL
_CTX_NUM = ('funding', 'breadth', 'rank', 'exposure_count', 'corr_count')


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ema_last(x, n):
    """EMA of x evaluated at the last element (seeded with SMA of the first n). None if not enough data."""
    if len(x) < n:
        return None
    a = 2.0 / (n + 1)
    e = float(np.mean(x[:n]))
    for v in x[n:]:
        e = a * float(v) + (1 - a) * e
    return e


def _session(ts):
    h = ts.hour
    if h < 8:
        return 'ASIA'
    if h < 13:
        return 'EU'
    if h < 21:
        return 'NY'
    return 'LATE'


def _dir(c):
    """Direction of a close series at its last bar: sign of EMA20 slope over SLOPE_LAG bars (None if too short)."""
    if len(c) < EMA_FAST + SLOPE_LAG:
        return None
    e_now, e_prev = _ema_last(c, EMA_FAST), _ema_last(c[:-SLOPE_LAG], EMA_FAST)
    return float(np.sign(e_now - e_prev)) if e_prev else None


def features_at(df, i, ctx=None):
    """Return {'features': {...}, 'missing': [names]} computed from df rows 0..i and whitelisted ctx only."""
    ctx = ctx or {}
    if i < 0 or i >= len(df):
        raise IndexError('decision index out of range')
    d = df.iloc[:i + 1]                                   # causality: nothing after the decision candle exists here
    c = d['c'].to_numpy(float); h = d['h'].to_numpy(float); l = d['l'].to_numpy(float); v = d['v'].to_numpy(float)
    n = len(c); px = c[-1]
    f = {k: None for k in FEATURE_NAMES}

    ema_f = _ema_last(c, EMA_FAST)
    if n >= EMA_FAST + SLOPE_LAG:
        prev = _ema_last(c[:-SLOPE_LAG], EMA_FAST)
        f['ema_slope'] = _num((ema_f - prev) / prev) if prev else None
    atr = None
    if n >= ATR_N + 1:
        tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
        atr = float(np.mean(tr[-ATR_N:]))
        f['atr_pct'] = _num(atr / px) if px else None
    if n >= VOL_N + 1:
        base = float(np.mean(v[-VOL_N - 1:-1]))
        f['vol_ratio'] = _num(v[-1] / base) if base > 0 else None
        if 'qv' in d:
            qv = d['qv'].to_numpy(float)
            qb = float(np.mean(qv[-VOL_N - 1:-1]))
            f['qv_ratio'] = _num(qv[-1] / qb) if qb > 0 else None
    if atr and atr > 0:
        if n >= HI_N + 1:
            f['breakout_dist'] = _num((px - float(np.max(h[-HI_N - 1:-1]))) / atr)
        if ema_f is not None:
            f['stretch'] = _num((px - ema_f) / atr)
        if n >= VWAP_N:
            tp = (h[-VWAP_N:] + l[-VWAP_N:] + c[-VWAP_N:]) / 3.0
            vs = float(np.sum(v[-VWAP_N:]))
            if vs > 0:
                f['vwap_dist'] = _num((px - float(np.sum(tp * v[-VWAP_N:])) / vs) / atr)

    if ctx.get('regime') is not None:
        f['regime'] = str(ctx['regime'])
    else:
        ema_s = _ema_last(c, EMA_SLOW)
        if ema_s is not None and f['ema_slope'] is not None:
            f['regime'] = ('TREND_UP' if ema_f > ema_s and f['ema_slope'] > 0 else
                           'TREND_DOWN' if ema_f < ema_s and f['ema_slope'] < 0 else 'RANGE')

    t_i = to_utc(d['t'].iloc[-1])                      # ints = epoch ms (see common.to_utc)
    f['session'] = _session(t_i)

    if ctx.get('btc_dir') is not None:
        f['btc_dir'] = _num(ctx['btc_dir'])
    elif ctx.get('btc_df') is not None:
        b = ctx['btc_df']
        keep = np.array([to_utc(x) <= t_i for x in b['t']], bool)
        f['btc_dir'] = _dir(b['c'].to_numpy(float)[keep])
    for k in _CTX_NUM:
        if ctx.get(k) is not None:
            f[k] = _num(ctx[k])

    missing = sorted(k for k, val in f.items() if val is None)
    return {'features': f, 'missing': missing}
