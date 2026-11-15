"""LONGSCOUT-V58: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe longscout_v26 helper.
New family: price/oscillator divergence on completed daily bars (never used before). No regime filter (the pattern is
itself the reversal hypothesis).

S58A s58_rsi_bear_divergence_4h short day j closes at a new 20-day closing high (above every close of days j-20..j-1); the
                                      highest close of days j-20..j-5 also set its own 20-day closing high on its day; daily
                                      RSI14 on day j is at least 5 points below RSI14 on that earlier high day. First 4h bar
                                      of the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L58A l58_rsi_bull_divergence_4h long  mirror: new 20-day closing low with RSI14 at least 5 points above the RSI at the earlier
                                      20-day closing low in days j-20..j-5.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _div_v(D, p, bear):
    n, gap, k = p['n'], p['gap'], p['pts']
    c = D.c.values
    r = S.rsi(D.c, 14).values
    if bear:
        ext = D.c > D.c.shift().rolling(n, min_periods=n).max()
    else:
        ext = D.c < D.c.shift().rolling(n, min_periods=n).min()
    ext = ext.values
    full = L27._consec(D, 2 * n).values
    out = np.zeros(len(c), bool)
    for j in range(2 * n, len(c)):
        if not (ext[j] and full[j]):
            continue
        w = np.arange(j - n, j - gap + 1)
        i = w[np.argmax(c[w])] if bear else w[np.argmin(c[w])]
        if not ext[i] or np.isnan(r[i]) or np.isnan(r[j]):
            continue
        out[j] = (r[j] <= r[i] - k) if bear else (r[j] >= r[i] + k)
    return pd.Series(out, index=D.index)


def _div(d, p, bear):
    D = L27._days_v(d)
    return L27._next_day(_div_v(D, p, bear), D, d)


def sig_bear(d, p, ctx):
    return dict(le=_z(d), se=S.X(pd.Series(_div(d, p, True), index=d.index)), lx=_z(d), sx=_z(d))


def sig_bull(d, p, ctx):
    return dict(le=S.X(pd.Series(_div(d, p, False), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


DV = dict(n=20, gap=5, pts=5.0)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    's58_rsi_bear_divergence_4h': dict(
        name='[RESEARCH] S58A Daily RSI14 bearish divergence at a 20-day closing high short 4h', style='reversal',
        fn=sig_bear, sides='short', tf='4h', research_only=True, params=dict(DV), mgmt=dict(MG)),
    'l58_rsi_bull_divergence_4h': dict(
        name='[RESEARCH] L58A Daily RSI14 bullish divergence at a 20-day closing low long 4h', style='reversal',
        fn=sig_bull, sides='long', tf='4h', research_only=True, params=dict(DV), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
