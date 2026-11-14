"""Deterministic closed-candle market-structure features (REVIEW-ONLY research; not used by the bot).

Every value at bar t uses only candles 0..t (all closed). Appending future candles never changes a past value
(tests/test_structure_features.py). N is fixed up front at 3 - no parameter search.

Definitions (N = 3):
  - swing high at bar j: h[j] > max(h[j-N..j-1]) and h[j] >= max(h[j+1..j+N]); swing low mirrored. It is CONFIRMED at the
    close of bar j+N (confirmation lag N bars = 12h on 4h) and only from then on is it visible.
  - sh / sl: price of the latest confirmed swing high / low (the structural zone [sl, sh]).
  - break up at t: close[t] > sh while that swing high is still unbroken (each swing can be broken once). Break down mirrored.
  - state: +1 bullish / -1 bearish / 0 none yet. A break in the direction of the state (or from 0) is a BOS; a break
    against it is an MSS (CHOCH-equivalent) and flips the state.
  - event: 'BOS' / 'MSS' of the latest break; bars_event = bars since it; bars_conf = bars since the latest swing
    confirmation (high or low).
  - zone_pos: +1 close above sh, -1 below sl, 0 inside [sl, sh]; dist_atr = distance outside the zone in ATR (0 inside),
    ATR = Wilder 14 (strategies.indicators definition).
"""
import numpy as np
import pandas as pd

N = 3


def wilder_atr(h, l, c, n=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def structure(df, n=N):
    """df: closed candles with columns h, l, c (index order = time). Returns a DataFrame aligned to df.index."""
    h, l, c = (np.asarray(df[k], dtype=float) for k in ('h', 'l', 'c'))
    m = len(c)
    atr = wilder_atr(pd.Series(h), pd.Series(l), pd.Series(c)).to_numpy()
    out = {k: np.full(m, np.nan) for k in ('sh', 'sl', 'state', 'bars_event', 'bars_conf', 'zone_pos', 'dist_atr')}
    event = np.array([''] * m, dtype=object)
    sh = sl = np.nan; sh_live = sl_live = False
    state, ev, t_ev, t_conf = 0, '', None, None
    for t in range(m):
        j = t - n                                            # the candidate pivot confirmed at the close of bar t
        if j - n >= 0:
            if h[j] > h[j - n:j].max() and h[j] >= h[j + 1:t + 1].max():
                sh, sh_live, t_conf = h[j], True, t
            if l[j] < l[j - n:j].min() and l[j] <= l[j + 1:t + 1].min():
                sl, sl_live, t_conf = l[j], True, t
        if sh_live and c[t] > sh:
            ev = 'BOS' if state >= 0 else 'MSS'; state, t_ev, sh_live = 1, t, False
        elif sl_live and c[t] < sl:
            ev = 'BOS' if state <= 0 else 'MSS'; state, t_ev, sl_live = -1, t, False
        out['sh'][t], out['sl'][t], out['state'][t] = sh, sl, state
        event[t] = ev
        out['bars_event'][t] = np.nan if t_ev is None else t - t_ev
        out['bars_conf'][t] = np.nan if t_conf is None else t - t_conf
        if not (np.isnan(sh) or np.isnan(sl)):
            zp = 1 if c[t] > sh else (-1 if c[t] < sl else 0)
            out['zone_pos'][t] = zp
            d = (c[t] - sh) if zp == 1 else ((sl - c[t]) if zp == -1 else 0.0)
            out['dist_atr'][t] = d / atr[t] if atr[t] > 0 else np.nan
    res = pd.DataFrame(out, index=df.index)
    res['event'] = event
    return res
