"""Neutral market spec -> {symbol: DataFrame t,o,h,l,c,v}.

v1 implements one base: "flat" = every candle o = c = px with h = px + wick, l = px - wick, so the true range is 2 x wick on
every bar and any ATR (Wilder, SMA or EMA) of the warm series is exactly 2 x wick in BOTH legacy models. Declared bars
override single candles; the candles after a declared bar stay flat at its close (same wick) until the next declared bar,
the same shape as tests/test_bt_intrabar_path.py.
"""
import numpy as np
import pandas as pd

from .schema import TFS, clock_ms


def build(case):
    tf = TFS[case['tf']]
    start = pd.Timestamp(clock_ms(case), unit='ms')          # integer UTC ms -> naive UTC timestamp (what the legacy models use)
    out = {}
    for sym, spec in case['market'].items():
        b = spec['base']
        n, px, w = int(b['n']), float(b['px']), float(b['wick'])
        t = pd.date_range(start, periods=n, freq=f'{tf}s')
        o = np.full(n, px); c = np.full(n, px); h = np.full(n, px + w); l = np.full(n, px - w)
        bars = {int(k): tuple(map(float, v)) for k, v in (spec.get('bars') or {}).items()}
        last = None
        for j in range(n):
            if j in bars:
                o[j], h[j], l[j], c[j] = bars[j]
                last = c[j]
            elif last is not None:
                o[j] = c[j] = last; h[j] = last + w; l[j] = last - w
        out[sym] = pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))
    return out


def signal_arrays(case, raw):
    """{symbol: {'le','se','lx','sx': bool array}} from the case's signals (bar index = the candle whose CLOSE fires it)."""
    from .schema import SIGNAL_KINDS
    out = {}
    for s, d in raw.items():
        out[s] = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
    for g in case['signals']:
        out[g['sym']][SIGNAL_KINDS[g['kind']]][int(g['bar'])] = True
    return out
