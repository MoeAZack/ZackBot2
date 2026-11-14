"""LONGSCOUT-V12: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None is r6_regime_flip_4h with a filter added.

L12A l12_strong_week_continuation_4h  long  first bar where the coin's 42-bar (7-day) return is >= +30% while it is rising
                                            (R1); hold to a 3 ATR stop or 42 bars. Short-horizon momentum, no target.
S12A s12_weak_week_continuation_4h    short first bar where the 7-day return is <= -25% while the coin is falling (R1); hold to
                                            a 3 ATR stop or 42 bars.
L12B l12_prior_week_high_break_4h     long  first close of the UTC week (Monday start) above the previous complete week's high
                                            while the coin is rising (R1). Stop 2.5 ATR, 3R, 42 bars.
S12B s12_prior_week_low_break_4h      short mirror: first close of the week below the previous week's low while falling (R1).
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


def sig_strong_week(d, p, ctx):
    cr = L1.coin_regime(d)
    r = d.c / d.c.shift(p['n']) - 1
    return dict(le=_first((r >= p['ret']) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_weak_week(d, p, ctx):
    cr = L1.coin_regime(d)
    r = d.c / d.c.shift(p['n']) - 1
    return dict(le=_z(d), se=_first((r <= -p['ret']) & (cr == -1)), lx=_z(d), sx=_z(d))


def _prior_week(d):
    t = pd.to_datetime(d.t)
    wk = t.dt.normalize() - pd.to_timedelta(t.dt.weekday, unit='D')
    hi, lo = d.h.groupby(wk).max(), d.l.groupby(wk).min()
    prev = wk - pd.Timedelta(days=7)
    return wk, pd.Series(hi.reindex(prev).values, index=d.index), pd.Series(lo.reindex(prev).values, index=d.index)


def _first_in_week(hit, wk):
    hit = S.X(hit)
    return hit & (hit.astype(int).groupby(wk.values).cumsum().values == 1)


def sig_prior_week_high_break(d, p, ctx):
    cr = L1.coin_regime(d)
    wk, ph, _ = _prior_week(d)
    return dict(le=_first_in_week((d.c > ph) & (cr == 1), wk), se=_z(d), lx=_z(d), sx=_z(d))


def sig_prior_week_low_break(d, p, ctx):
    cr = L1.coin_regime(d)
    wk, _, pl = _prior_week(d)
    return dict(le=_z(d), se=_first_in_week((d.c < pl) & (cr == -1), wk), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l12_strong_week_continuation_4h': dict(
        name='[RESEARCH] L12A Strong-week continuation long 4h', style='momentum', fn=sig_strong_week,
        sides='long', tf='4h', research_only=True, params=dict(n=42, ret=0.30), mgmt=dict(stop_atr=3.0, max_bars=42)),
    's12_weak_week_continuation_4h': dict(
        name='[RESEARCH] S12A Weak-week continuation short 4h', style='momentum', fn=sig_weak_week,
        sides='short', tf='4h', research_only=True, params=dict(n=42, ret=0.25), mgmt=dict(stop_atr=3.0, max_bars=42)),
    'l12_prior_week_high_break_4h': dict(
        name='[RESEARCH] L12B Prior-week-high break long 4h', style='trend', fn=sig_prior_week_high_break,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=42)),
    's12_prior_week_low_break_4h': dict(
        name='[RESEARCH] S12B Prior-week-low break short 4h', style='trend', fn=sig_prior_week_low_break,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=42)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
