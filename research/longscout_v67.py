"""LONGSCOUT-V67: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families: linear-regression CHANNEL break (fit +/- 2 residual sd), and the first 52-week
closing high / low in 90 days (earlier rounds used 20-180 day windows).

R67A r67_regression_channel_break_4h both  least-squares line through the closes of days j-50..j-1, residual sd s; projected
                                            value at day j = P. Close j > P + 2s and close j-1 <= its own band -> long if the
                                            coin is rising (R1); close j < P - 2s (first) -> short if falling. First 4h bar of
                                            the next day (modeled fill 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
L67A l67_52w_high_4h                 long  close j above every close of days j-365..j-1, and no such new 365-day closing high
                                            in days j-90..j-1; coin rising (R1). Same management.
S67A s67_52w_low_4h                  short mirror: first 365-day closing low in 90 days; coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _chan(D, n):
    x = np.arange(n, dtype=float); xm = x.mean(); sxx = ((x - xm) ** 2).sum()
    def fit(a):
        b = ((x - xm) * (a - a.mean())).sum() / sxx
        r = a - (a.mean() + b * (x - xm))
        return a.mean() + b * (n - xm), r.std(ddof=2)
    ys = D.c.shift()
    out = ys.rolling(n, min_periods=n).apply(lambda a: fit(a)[0], raw=True)
    sd = ys.rolling(n, min_periods=n).apply(lambda a: fit(a)[1], raw=True)
    return out, sd


def _chan_v(D, p, up):
    P, sd = _chan(D, p['n'])
    hi, lo = P + p['k'] * sd, P - p['k'] * sd
    ok = ((D.c > hi) & (D.c.shift() <= hi.shift())) if up else ((D.c < lo) & (D.c.shift() >= lo.shift()))
    return (ok & L27._consec(D, p['n'] + 1)).fillna(False)


def _year_v(D, p, up):
    n, quiet = p['n'], p['quiet']
    if up:
        new = D.c > D.c.shift().rolling(n, min_periods=n).max()
    else:
        new = D.c < D.c.shift().rolling(n, min_periods=n).min()
    new = new.fillna(False)
    recent = new.astype(float).shift().rolling(quiet, min_periods=quiet).sum() > 0
    return (new & ~recent & L27._consec(D, n)).fillna(False)


def _on(fn):
    def f(d, p, up):
        D = L27._days_v(d)
        return L27._next_day(fn(D, p, up), D, d)
    return f


_channel, _year = _on(_chan_v), _on(_year_v)


def sig_chan(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_channel(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_channel(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def sig_high(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_year(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_low(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_year(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


YP = dict(n=365, quiet=90)
MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r67_regression_channel_break_4h': dict(
        name='[RESEARCH] R67A Daily 50-day regression channel (2 sd) break with regime L/S 4h', style='trend',
        fn=sig_chan, sides='both', tf='4h', research_only=True, params=dict(n=50, k=2.0), mgmt=dict(MG)),
    'l67_52w_high_4h': dict(
        name='[RESEARCH] L67A First 52-week closing high in 90 days long 4h', style='momentum', fn=sig_high,
        sides='long', tf='4h', research_only=True, params=dict(YP), mgmt=dict(MG)),
    's67_52w_low_4h': dict(
        name='[RESEARCH] S67A First 52-week closing low in 90 days short 4h', style='momentum', fn=sig_low,
        sides='short', tf='4h', research_only=True, params=dict(YP), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
