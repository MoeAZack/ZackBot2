"""LONGSCOUT-V6: six new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults where one
exists); none tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py.

R6A r6_regime_flip_4h             both  enter on the first 4h bar after the coin's own R1 regime turns rising (long) or
                                        falling (short); exit when the regime leaves that state. Tests R1 itself as a
                                        position rule.
L6A l6_dual_regime_hold_4h        long  enter on the first 4h bar where BTC and the coin are both rising (R1); exit when
                                        either stops rising.
R6B r6_tsmom_4h                   both  time-series momentum: 180-bar and 42-bar returns both positive -> long (first bar
                                        of agreement); both negative -> short; exit when the 42-bar return changes sign.
                                        Changed hypothesis vs exp ema_mom (no EMA cross / EMA200 trigger).
R6C r6_heikin_ashi_trend_4h       both  second consecutive green Heikin-Ashi bar with no lower wick while the coin is rising
                                        (R1) -> long; mirror while falling -> short; exit on the first opposite-colour HA bar.
S6A s6_lower_high_breakdown_4h    short market-structure shift down: last two confirmed swing highs falling (3/3 fractal),
                                        close crosses below the last confirmed swing low, coin and BTC not rising (R1).
                                        Mirror of LC4 (long); a changed side is a new candidate.
L6B l6_rsi_bull_range_4h          long  RSI14 has stayed above 40 for the prior 42 bars (bull range), then crosses above 60;
                                        coin not falling (R1).
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


def sig_regime_flip(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index)
    up, dn = cr == 1, cr == -1
    return dict(le=_first(up), se=_first(dn), lx=S.X(~up), sx=S.X(~dn))


def sig_dual_regime_hold(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index); br = pd.Series(L1.btc_regime(d), index=d.index)
    both = (cr == 1) & (br == 1)
    return dict(le=_first(both), se=_z(d), lx=S.X(~both), sx=_z(d))


def sig_tsmom(d, p, ctx):
    rl = d.c / d.c.shift(p['long_n']) - 1; rs = d.c / d.c.shift(p['short_n']) - 1
    up, dn = (rl > 0) & (rs > 0), (rl < 0) & (rs < 0)
    return dict(le=_first(up), se=_first(dn), lx=S.X(rs < 0), sx=S.X(rs > 0))


def _heikin_ashi(d):
    o, h, l, c = d.o.values, d.h.values, d.l.values, d.c.values
    hc = (o + h + l + c) / 4
    ho = np.empty(len(d)); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(d)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    hh = np.maximum(h, np.maximum(ho, hc)); hl = np.minimum(l, np.minimum(ho, hc))
    return [pd.Series(x, index=d.index) for x in (ho, hh, hl, hc)]


def sig_heikin_ashi_trend(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index)
    ho, hh, hl, hc = _heikin_ashi(d)
    green, red = hc > ho, hc < ho
    le = green & green.shift(fill_value=False) & ~green.shift(2, fill_value=False) & (hl >= ho) & (cr == 1)
    se = red & red.shift(fill_value=False) & ~red.shift(2, fill_value=False) & (hh <= ho) & (cr == -1)
    return dict(le=S.X(le), se=S.X(se), lx=S.X(red), sx=S.X(green))


def sig_lower_high_breakdown(d, p, ctx):
    h, l, c, a = d.h.values, d.l.values, d.c.values, d.atr.values
    k, n = p['k'], len(d)
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    se = np.zeros(n, bool); dist = np.full(n, np.nan)
    highs, sl, sl_used = [], np.nan, True
    for i in range(2 * k, n):
        j = i - k                                            # pivot candidate, confirmed at bar i (k bars after it)
        if h[j] > h[j - k:j].max() and h[j] >= h[j + 1:i + 1].max():
            highs.append(h[j])
        if l[j] < l[j - k:j].min() and l[j] <= l[j + 1:i + 1].min():
            sl, sl_used = l[j], False
        if (not sl_used and len(highs) >= 2 and highs[-1] < highs[-2] and c[i] < sl and c[i - 1] >= sl
                and cr[i] in (0.0, -1.0) and br[i] in (0.0, -1.0)):
            dd = (highs[-1] + p['buf_atr'] * a[i]) - c[i]
            if p['min_atr'] * a[i] <= dd <= p['max_atr'] * a[i]:
                se[i] = True; dist[i] = dd
            sl_used = True
        elif not sl_used and c[i] < sl:
            sl_used = True                                   # the level broke without a valid setup: wait for a new one
    return dict(le=_z(d), se=pd.Series(se, d.index), lx=_z(d), sx=_z(d), atr=dist)


def sig_rsi_bull_range(d, p, ctx):
    cr = L1.coin_regime(d)
    held = (d.rsi14 > p['floor']).astype(float).rolling(p['look'], min_periods=p['look']).sum().shift() == p['look']
    le = S.xup(d.rsi14, d.rsi14 * 0 + p['trigger']) & held & np.isin(cr, (0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r6_regime_flip_4h': dict(
        name='[RESEARCH] R6A Coin R1 regime flip L/S 4h', style='trend', fn=sig_regime_flip,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=3.0, max_bars=120)),
    'l6_dual_regime_hold_4h': dict(
        name='[RESEARCH] L6A BTC+coin rising regime hold long 4h', style='trend', fn=sig_dual_regime_hold,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=3.0, max_bars=120)),
    'r6_tsmom_4h': dict(
        name='[RESEARCH] R6B Time-series momentum 180/42 L/S 4h', style='trend', fn=sig_tsmom,
        sides='both', tf='4h', research_only=True, params=dict(long_n=180, short_n=42),
        mgmt=dict(stop_atr=2.5, max_bars=120)),
    'r6_heikin_ashi_trend_4h': dict(
        name='[RESEARCH] R6C Regime Heikin-Ashi trend L/S 4h', style='trend', fn=sig_heikin_ashi_trend,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=120)),
    's6_lower_high_breakdown_4h': dict(
        name='[RESEARCH] S6A Lower-high structure breakdown short 4h', style='structure', fn=sig_lower_high_breakdown,
        sides='short', tf='4h', research_only=True, params=dict(k=3, buf_atr=0.25, min_atr=1.0, max_atr=4.0),
        mgmt=dict(stop_atr=1.0, tp_r=2.5, max_bars=60)),
    'l6_rsi_bull_range_4h': dict(
        name='[RESEARCH] L6B RSI bull-range momentum long 4h', style='momentum', fn=sig_rsi_bull_range,
        sides='long', tf='4h', research_only=True, params=dict(floor=40.0, look=42, trigger=60.0),
        mgmt=dict(stop_atr=2.0, tp_r=2.5, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
