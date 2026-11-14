"""LONGSCOUT-V8: five new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None re-parameterises V6's r6_regime_flip_4h.

L8A l8_calm_uptrend_hold_4h       long  first 4h bar where the coin is rising (R1) AND its 42-bar realised volatility is
                                        below its own prior 180-bar median; exit when either stops holding.
L8B l8_near_high_momentum_4h      long  first close within 5% of the 180-bar (30-day) high while the coin is rising (R1);
                                        exit on a close more than 15% below that high. Anchor ("near the high") momentum.
R8A r8_ratio_regime_flip_4h       both  R1 computed on the coin/BTC daily close ratio: first bar after it turns rising ->
                                        long, falling -> short; exit when it leaves that state. Relative-strength regime.
S8A s8_dual_falling_hold_4h       short first 4h bar where BTC and the coin are both falling (R1); exit when either stops
                                        falling. Side mirror of L6A (a changed side is a new candidate).
L8C l8_up_streak_4h               long  five consecutive higher 4h closes (first such bar), coin not falling (R1).
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


def sig_calm_uptrend_hold(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index)
    rv = np.log(d.c).diff().rolling(p['vol_n'], min_periods=p['vol_n']).std()
    med = rv.rolling(p['med_n'], min_periods=p['med_n']).median().shift()
    on = (cr == 1) & (rv < med)
    return dict(le=_first(on), se=_z(d), lx=S.X(~on), sx=_z(d))


def sig_near_high_momentum(d, p, ctx):
    cr = L1.coin_regime(d)
    hi = d.h.rolling(p['n'], min_periods=p['n']).max()
    near = (d.c >= (1 - p['near']) * hi) & (cr == 1)
    return dict(le=_first(near), se=_z(d), lx=S.X(d.c < (1 - p['exit']) * hi), sx=_z(d))


def sig_ratio_regime_flip(d, p, ctx):
    f = L1._BTC['f']
    cd, bd = L1.daily_closes(d.t, d.c), L1.daily_closes(f.t, f.c)
    ratio = (cd / bd.reindex(cd.index)).dropna()
    rr = pd.Series(L1.on_rows(L1.regime_daily(ratio), d.t), index=d.index)
    up, dn = rr == 1, rr == -1
    return dict(le=_first(up), se=_first(dn), lx=S.X(~up), sx=S.X(~dn))


def sig_dual_falling_hold(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index); br = pd.Series(L1.btc_regime(d), index=d.index)
    both = (cr == -1) & (br == -1)
    return dict(le=_z(d), se=_first(both), lx=_z(d), sx=S.X(~both))


def sig_up_streak(d, p, ctx):
    cr = L1.coin_regime(d)
    up = (d.c > d.c.shift()).astype(float)
    streak = up.rolling(p['n'], min_periods=p['n']).sum() == p['n']
    return dict(le=_first(streak & np.isin(cr, (0.0, 1.0))), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l8_calm_uptrend_hold_4h': dict(
        name='[RESEARCH] L8A Calm-uptrend hold long 4h', style='trend', fn=sig_calm_uptrend_hold,
        sides='long', tf='4h', research_only=True, params=dict(vol_n=42, med_n=180), mgmt=dict(stop_atr=3.0, max_bars=120)),
    'l8_near_high_momentum_4h': dict(
        name='[RESEARCH] L8B Near-30-day-high momentum long 4h', style='trend', fn=sig_near_high_momentum,
        sides='long', tf='4h', research_only=True, params=dict(n=180, near=0.05, exit=0.15),
        mgmt=dict(stop_atr=3.0, max_bars=120)),
    'r8_ratio_regime_flip_4h': dict(
        name='[RESEARCH] R8A Coin/BTC ratio regime flip L/S 4h', style='trend', fn=sig_ratio_regime_flip,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=3.0, max_bars=120)),
    's8_dual_falling_hold_4h': dict(
        name='[RESEARCH] S8A BTC+coin falling regime hold short 4h', style='trend', fn=sig_dual_falling_hold,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=3.0, max_bars=120)),
    'l8_up_streak_4h': dict(
        name='[RESEARCH] L8C Five-higher-closes streak long 4h', style='momentum', fn=sig_up_streak,
        sides='long', tf='4h', research_only=True, params=dict(n=5), mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
