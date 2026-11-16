"""LONGSCOUT-V7: six new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults where one
exists); none tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py.
Written after V6's r6_regime_flip_4h screen pass; none of these is a re-parameterisation of it (no coin-R1 flip entry).

L7A l7_nr7_breakout_4h            long  the prior bar was an NR7 (narrowest range of the last 7 bars); this bar closes above
                                        that bar's high; coin rising (R1). Stop 2 ATR, 2R, 24 bars.
R7A r7_ema_ribbon_alignment_4h    both  first bar where EMA9 > EMA21 > EMA50 > EMA100 > EMA200 -> long (mirror -> short);
                                        exit when EMA9 crosses back through EMA21. Ungated.
R7B r7_regression_tstat_4h        both  t-statistic of the 60-bar log-price regression slope crosses above +2 -> long, below
                                        -2 -> short; exit when it crosses zero. Ungated.
S7A s7_blowoff_fade_short_4h      short coin up >= 50% over 42 bars (7 days) with RSI14 > 75 in the last 3 bars; first red bar
                                        closing below the prior bar's low. Stop over the 3-bar high, 2R, 24 bars.
L7B l7_btc_regime_turn_long_4h    long  first 4h bar after BTC's daily R1 regime turns rising, coin not falling (R1); exit when
                                        BTC stops rising. Market-regime timing, not the coin's own regime.
R7C r7_aroon_regime_4h            both  Aroon(25) up crosses above Aroon down with Aroon up >= 70 while the coin is rising (R1)
                                        -> long; mirror while falling -> short; exit on the opposite Aroon cross.
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


def sig_nr7_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    rng = d.h - d.l
    nr7 = rng <= rng.rolling(p['n'], min_periods=p['n']).min()
    le = nr7.shift(fill_value=False) & (d.c > d.h.shift()) & (cr == 1)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_ema_ribbon(d, p, ctx):
    up = (d.e9 > d.e21) & (d.e21 > d.e50) & (d.e50 > d.e100) & (d.e100 > d.e200)
    dn = (d.e9 < d.e21) & (d.e21 < d.e50) & (d.e50 < d.e100) & (d.e100 < d.e200)
    return dict(le=_first(up), se=_first(dn), lx=S.X(d.e9 < d.e21), sx=S.X(d.e9 > d.e21))


def sig_regression_tstat(d, p, ctx):
    n = p['n']
    y = np.log(d.c)
    x = pd.Series(np.arange(len(d), dtype=float), index=d.index)
    r = y.rolling(n, min_periods=n).corr(x).clip(-0.999999, 0.999999)
    t = r * np.sqrt((n - 2) / (1 - r * r))
    lim = pd.Series(p['t'], index=d.index)
    return dict(le=S.X(S.xup(t, lim)), se=S.X(S.xup(-t, lim)), lx=S.X(S.xup(-t, lim * 0)), sx=S.X(S.xup(t, lim * 0)))


def sig_blowoff_fade(d, p, ctx):
    run = d.c / d.c.shift(p['run_n']) - 1
    hot = (d.rsi14 > p['rsi']).astype(float).rolling(p['hot_n'], min_periods=1).max() > 0
    se = (run >= p['run']) & hot & (d.c < d.o) & (d.c < d.l.shift())
    se = _first(se)
    hi = d.h.rolling(p['hot_n'], min_periods=1).max()
    dist = np.where(se, np.clip((hi.values + p['buf_atr'] * d.atr.values) - d.c.values,
                                p['min_atr'] * d.atr.values, p['max_atr'] * d.atr.values), np.nan)
    return dict(le=_z(d), se=se, lx=_z(d), sx=_z(d), atr=dist)


def sig_btc_regime_turn(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index); br = pd.Series(L1.btc_regime(d), index=d.index)
    bup = br == 1
    le = _first(bup) & cr.isin((0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=S.X(~bup), sx=_z(d))


def _aroon(d, n):
    hi = d.h.rolling(n + 1, min_periods=n + 1).apply(np.argmax, raw=True)
    lo = d.l.rolling(n + 1, min_periods=n + 1).apply(np.argmin, raw=True)
    return 100 * hi / n, 100 * lo / n                      # index n = the current bar -> 100


def sig_aroon_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    up, dn = _aroon(d, p['n'])
    xu, xd = S.xup(up, dn), S.xup(dn, up)
    return dict(le=S.X(xu & (up >= p['min']) & (cr == 1)), se=S.X(xd & (dn >= p['min']) & (cr == -1)),
                lx=S.X(xd), sx=S.X(xu))


CANDIDATES = {
    'l7_nr7_breakout_4h': dict(
        name='[RESEARCH] L7A NR7 breakout long 4h', style='momentum', fn=sig_nr7_breakout,
        sides='long', tf='4h', research_only=True, params=dict(n=7), mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24)),
    'r7_ema_ribbon_alignment_4h': dict(
        name='[RESEARCH] R7A EMA ribbon full alignment L/S 4h', style='trend', fn=sig_ema_ribbon,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=120)),
    'r7_regression_tstat_4h': dict(
        name='[RESEARCH] R7B 60-bar regression slope t-stat L/S 4h', style='trend', fn=sig_regression_tstat,
        sides='both', tf='4h', research_only=True, params=dict(n=60, t=2.0), mgmt=dict(stop_atr=2.5, max_bars=120)),
    's7_blowoff_fade_short_4h': dict(
        name='[RESEARCH] S7A Blow-off top fade short 4h', style='reversal', fn=sig_blowoff_fade,
        sides='short', tf='4h', research_only=True,
        params=dict(run_n=42, run=0.5, rsi=75.0, hot_n=3, buf_atr=0.25, min_atr=1.0, max_atr=4.0),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24)),
    'l7_btc_regime_turn_long_4h': dict(
        name='[RESEARCH] L7B BTC regime turn-up long 4h', style='trend', fn=sig_btc_regime_turn,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=3.0, max_bars=120)),
    'r7_aroon_regime_4h': dict(
        name='[RESEARCH] R7C Regime Aroon(25) cross L/S 4h', style='trend', fn=sig_aroon_regime,
        sides='both', tf='4h', research_only=True, params=dict(n=25, min=70.0), mgmt=dict(stop_atr=2.5, max_bars=120)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
