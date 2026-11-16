"""CX1: ordinal permutation-entropy collapse, frozen before outcomes.

New family relative to LONGSCOUT V1-V90: Shannon entropy of the six ordinal
patterns of THREE daily log returns. This measures temporal ordering rather
than marginal skew/kurtosis, sign counts, variance or linear autocorrelation.
60 patterns (63 closes) form each entropy observation, normalized by log(6).
Ties use stable chronological ordering. The first cross from > .95 to <= .95
signals only in the sign of the completed 20-day return and matching R1 regime.
Require all 64 consecutive closes for current AND prior entropy, and next-day
availability. Long and short are separate trials. Fixed stop 2.5 ATR, target
2R, cap 30 four-hour bars. Research only; no runtime registration.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def entropy(D, n):
    r = np.log(D.c / D.c.shift()).to_numpy()
    code = np.full(len(D), np.nan)
    patterns = {(0, 1, 2): 0, (0, 2, 1): 1, (1, 0, 2): 2,
                (1, 2, 0): 3, (2, 0, 1): 4, (2, 1, 0): 5}
    for j in range(3, len(r)):
        w = r[j-2:j+1]
        if np.isfinite(w).all():
            code[j] = patterns[tuple(np.argsort(w, kind='stable'))]
    def h(w):
        p = np.bincount(w.astype(int), minlength=6) / n
        p = p[p > 0]
        return -float(np.sum(p * np.log(p))) / np.log(6)
    return pd.Series(code, index=D.index).rolling(n, min_periods=n).apply(h, raw=True)


def daily_hit(D, p, up):
    e = entropy(D, p['n'])
    first = (e <= p['threshold']) & (e.shift() > p['threshold'])
    direction = D.c > D.c.shift(p['ret']) if up else D.c < D.c.shift(p['ret'])
    return (first & direction & L27._consec(D, p['n'] + 3)).fillna(False)


def signal(d, p, up):
    D = L27._days_v(d)
    hit = L27._next_day(daily_hit(D, p, up), D, d)
    hit = pd.Series(hit & (L1.coin_regime(d) == (1 if up else -1)), index=d.index)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(hit) if up else z, se=z if up else S.X(hit), lx=z, sx=z)


def sig_long(d, p, ctx):
    return signal(d, p, True)


def sig_short(d, p, ctx):
    return signal(d, p, False)


IP = dict(n=60, threshold=0.95, ret=20)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'cx001_entropy_collapse_long_4h': dict(name='[RESEARCH] CX001 ordinal-entropy collapse long',
        style='momentum', fn=sig_long, sides='long', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
    'cx002_entropy_collapse_short_4h': dict(name='[RESEARCH] CX002 ordinal-entropy collapse short',
        style='momentum', fn=sig_short, sides='short', tf='4h', research_only=True, params=dict(IP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
