"""LONGSCOUT-V10: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None is r6_regime_flip_4h with a filter added.

L10A l10_base_breakout_4h          long  the prior 90 bars (15 days) traded in a range no wider than 25% of its low; the close
                                         crosses above that range's high; coin not falling (R1). Stop 2.5 ATR, 3R, 60 bars.
S10A s10_base_breakdown_4h         short mirror: tight 90-bar range, close crosses below its low, coin not rising (R1).
L10B l10_decorrelated_rise_4h      long  first bar where the coin is rising (R1) while the 180-bar correlation of its 4h returns
                                         with BTC's is below 0.5 (its own story, not beta); exit when either stops holding.
R10A r10_efficiency_trend_4h       both  42-bar Kaufman efficiency ratio crosses above 0.4 with a positive 42-bar change while
                                         the coin is rising (R1) -> long; mirror -> short; exit when the 42-bar change flips.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def _base(d, p):
    hi = d.h.rolling(p['n'], min_periods=p['n']).max().shift()
    lo = d.l.rolling(p['n'], min_periods=p['n']).min().shift()
    return hi, lo, (hi - lo) / lo <= p['width']


def sig_base_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    hi, lo, tight = _base(d, p)
    return dict(le=S.X(S.xup(d.c, hi) & tight & np.isin(cr, (0.0, 1.0))), se=_z(d), lx=_z(d), sx=_z(d))


def sig_base_breakdown(d, p, ctx):
    cr = L1.coin_regime(d)
    hi, lo, tight = _base(d, p)
    return dict(le=_z(d), se=S.X(S.xup(-d.c, -lo) & tight & np.isin(cr, (0.0, -1.0))), lx=_z(d), sx=_z(d))


def sig_decorrelated_rise(d, p, ctx):
    f = L1._BTC['f']
    cr = pd.Series(L1.coin_regime(d), index=d.index)
    bc = pd.Series(f.c.values, index=pd.to_datetime(f.t)).reindex(pd.to_datetime(d.t)).values
    rb = pd.Series(bc, index=d.index).pct_change()
    rho = d.c.pct_change().rolling(p['n'], min_periods=p['n']).corr(rb)
    on = (cr == 1) & (rho < p['rho'])
    return dict(le=_first(on), se=_z(d), lx=S.X(~on), sx=_z(d))


def sig_efficiency_trend(d, p, ctx):
    cr = L1.coin_regime(d)
    n = p['n']
    chg = d.c - d.c.shift(n)
    er = chg.abs() / d.c.diff().abs().rolling(n, min_periods=n).sum().replace(0, np.nan)
    xu = S.xup(er, er * 0 + p['er'])
    return dict(le=S.X(xu & (chg > 0) & (cr == 1)), se=S.X(xu & (chg < 0) & (cr == -1)),
                lx=S.X(chg < 0), sx=S.X(chg > 0))


BASE = dict(n=90, width=0.25)
CANDIDATES = {
    'l10_base_breakout_4h': dict(
        name='[RESEARCH] L10A Tight 15-day base breakout long 4h', style='trend', fn=sig_base_breakout,
        sides='long', tf='4h', research_only=True, params=dict(BASE), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    's10_base_breakdown_4h': dict(
        name='[RESEARCH] S10A Tight 15-day base breakdown short 4h', style='trend', fn=sig_base_breakdown,
        sides='short', tf='4h', research_only=True, params=dict(BASE), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    'l10_decorrelated_rise_4h': dict(
        name='[RESEARCH] L10B Low-BTC-correlation rising hold long 4h', style='trend', fn=sig_decorrelated_rise,
        sides='long', tf='4h', research_only=True, params=dict(n=180, rho=0.5), mgmt=dict(stop_atr=3.0, max_bars=120)),
    'r10_efficiency_trend_4h': dict(
        name='[RESEARCH] R10A Regime efficiency-ratio trend L/S 4h', style='trend', fn=sig_efficiency_trend,
        sides='both', tf='4h', research_only=True, params=dict(n=42, er=0.4), mgmt=dict(stop_atr=2.5, max_bars=120)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
