"""LONGSCOUT-V36: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families (trend t-statistic, lottery MAX effect, volatility collapse).

R36A r36_trend_tstat_4h       both  t-statistic of the slope of log daily close on time over the last 30 completed days rises
                                    through +4 with the coin rising (R1) -> long; falls through -4 with the coin falling -> short.
                                    First 4h bar of the next day. Stop 2.5 ATR, 2.5R, 36 bars.
S36A s36_max_effect_4h        short the largest single-day close-to-close gain of the last 30 completed days is >= 25% (a lottery
                                    spike) and it happened 5-10 days ago, coin not rising (R1): fade the attention.
                                    First such day; first 4h bar of the next day. Stop 3 ATR, 2R, 30 bars.
L36A l36_vol_collapse_4h      long  10-day realized volatility of daily returns falls below 0.5x the 60-day realized volatility
                                    (first such day), coin rising (R1). First 4h bar of the next day. Stop 2.5 ATR, 3R, 60 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _tstat(y, n):
    x = np.arange(n, dtype=float); xc = x - x.mean(); sxx = (xc ** 2).sum()
    v = y.values; out = np.full(len(v), np.nan)
    for i in range(n - 1, len(v)):
        w = v[i - n + 1:i + 1]
        if np.isnan(w).any():
            continue
        b = (xc * (w - w.mean())).sum() / sxx
        res = w - w.mean() - b * xc
        se = np.sqrt((res ** 2).sum() / (n - 2) / sxx)
        out[i] = b / se if se > 0 else np.nan
    return pd.Series(out, index=y.index)


def _trend_t(d, p, up):
    D = L27._days_v(d)
    t = _tstat(np.log(D.c), p['n'])
    hit = (t >= p['t']) if up else (t <= -p['t'])
    ok = hit & ~hit.shift(fill_value=False) & L27._consec(D, p['n'])
    return L27._next_day(ok, D, d)


def sig_trend_t(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_trend_t(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_trend_t(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _max_effect(d, p):
    D = L27._days_v(d)
    r = D.c.pct_change().where(D.index.to_series().diff() == pd.Timedelta(days=1))
    n = p['n']
    mx = r.rolling(n, min_periods=n).max()
    age = r.rolling(n, min_periods=n).apply(lambda a: len(a) - 1 - int(np.nanargmax(a)), raw=True)
    hit = (mx >= p['spike']) & (age >= p['lo']) & (age <= p['hi'])
    ok = hit & ~hit.shift(fill_value=False)
    return L27._next_day(ok, D, d)


def sig_max_effect(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_max_effect(d, p) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


def _vol_collapse(d, p):
    D = L27._days_v(d)
    r = D.c.pct_change().where(D.index.to_series().diff() == pd.Timedelta(days=1))
    s = r.rolling(p['short'], min_periods=p['short']).std()
    lng = r.rolling(p['long'], min_periods=p['long']).std()
    hit = s < p['ratio'] * lng
    ok = hit & ~hit.shift(fill_value=False)
    return L27._next_day(ok, D, d)


def sig_vol_collapse(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_vol_collapse(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r36_trend_tstat_4h': dict(
        name='[RESEARCH] R36A 30-day trend t-statistic with regime L/S 4h', style='trend', fn=sig_trend_t,
        sides='both', tf='4h', research_only=True, params=dict(n=30, t=4.0), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    's36_max_effect_4h': dict(
        name='[RESEARCH] S36A Lottery MAX-day fade short 4h', style='reversal', fn=sig_max_effect,
        sides='short', tf='4h', research_only=True, params=dict(n=30, spike=0.25, lo=5, hi=10),
        mgmt=dict(stop_atr=3.0, tp_r=2.0, max_bars=30)),
    'l36_vol_collapse_4h': dict(
        name='[RESEARCH] L36A Volatility collapse in rising regime long 4h', style='trend', fn=sig_vol_collapse,
        sides='long', tf='4h', research_only=True, params=dict(short=10, long=60, ratio=0.5),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
