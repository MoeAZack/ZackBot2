"""LONGSCOUT-V3: six new research-only 4h candidates, long and regime-switching first (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. 4h only this round: daily rules gave too few closes under max-one in rounds 1-2 (an observation about
sample size, not about any rule's profit). Regime R1 and the causal helpers come unchanged from longscout_v1.py.

L3A l3_dual_rising_ema50_pullback_4h  long  BTC and coin both rising (R1), 4h EMA20 > EMA50, the bar dips to the EMA50
                                            and closes green above it (first such bar); 2R target, 30 bars.
L3B l3_rising_breakout_4h             long  coin rising (R1), BTC not falling, close crosses the prior 20-bar high with
                                            ADX >= 20; exit on a close below the prior 10-bar low. Changed hypothesis of
                                            exp_breakout (ungated, both sides).
L3C l3_impulse_continuation_4h        long  coin rising (R1): a green 4h bar with range >= 2.5 ATR closing in the top 20%
                                            of its range; stop under that bar's low, 2R target, 24 bars.
R3A r3_regime_ema_cross_4h            both  EMA20/EMA50 cross taken only in the coin's R1 direction (long when rising, short
                                            when falling); exit on the opposite cross. No EMA200/momentum filter (unlike
                                            exp_trend / RS1).
R3B r3_sideways_band_fade_4h          both  only while the coin is sideways (R1): long below the lower 20-bar Bollinger band
                                            with RSI14 < 30, short above the upper band with RSI14 > 70; exit at the middle
                                            band. Changed hypothesis of exp_range (ungated).
S3A s3_btc_up_laggard_breakdown_4h    short BTC rising (R1) while the coin is not rising; close crosses below the prior
                                            20-bar low. Shorting alts that fail while BTC is strong.
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


def sig_dual_rising_pullback(d, p, ctx):
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    le = (cr == 1) & (br == 1) & (d.e20 > d.e50) & (d.l <= d.e50) & (d.c > d.e50) & (d.c > d.o)
    return dict(le=_first(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_rising_breakout(d, p, ctx):
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    le = S.xup(d.c, d.hh20) & (d.adx >= p['adx']) & (cr == 1) & (br >= 0)
    return dict(le=S.X(le), se=_z(d), lx=S.X(d.c < d.ll10), sx=_z(d))


def sig_impulse_continuation(d, p, ctx):
    cr = L1.coin_regime(d)
    rng = d.h - d.l
    le = (cr == 1) & (d.c > d.o) & (rng >= p['rng_atr'] * d.atr.shift()) & ((d.c - d.l) >= p['top'] * rng)
    dist = np.where(le, np.maximum(d.c.values - (d.l.values - p['buf_atr'] * d.atr.values), p['min_atr'] * d.atr.values), np.nan)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d), atr=dist)


def sig_regime_ema_cross(d, p, ctx):
    cr = L1.coin_regime(d)
    up, dn = S.xup(d.e20, d.e50), S.xup(d.e50, d.e20)
    return dict(le=S.X(up & (cr == 1)), se=S.X(dn & (cr == -1)), lx=S.X(dn), sx=S.X(up))


def sig_sideways_band_fade(d, p, ctx):
    cr = L1.coin_regime(d)
    side = cr == 0
    le = side & (d.c < d.bbl) & (d.rsi14 < p['rsi_lo'])
    se = side & (d.c > d.bbu) & (d.rsi14 > p['rsi_hi'])
    return dict(le=_first(le), se=_first(se), lx=S.X(d.c >= d.bbm), sx=S.X(d.c <= d.bbm))


def sig_btc_up_laggard_breakdown(d, p, ctx):
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    se = S.xup(-d.c, -d.ll20) & (br == 1) & np.isin(cr, (0.0, -1.0))
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l3_dual_rising_ema50_pullback_4h': dict(
        name='[RESEARCH] L3A Dual-rising EMA50 pullback long 4h', style='pullback', fn=sig_dual_rising_pullback,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=30)),
    'l3_rising_breakout_4h': dict(
        name='[RESEARCH] L3B Rising-regime 20-bar breakout long 4h', style='trend', fn=sig_rising_breakout,
        sides='long', tf='4h', research_only=True, params=dict(adx=20.0), mgmt=dict(stop_atr=2.5, max_bars=90)),
    'l3_impulse_continuation_4h': dict(
        name='[RESEARCH] L3C Impulse-bar continuation long 4h', style='momentum', fn=sig_impulse_continuation,
        sides='long', tf='4h', research_only=True, params=dict(rng_atr=2.5, top=0.8, buf_atr=0.1, min_atr=1.0),
        mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=24)),
    'r3_regime_ema_cross_4h': dict(
        name='[RESEARCH] R3A Regime-direction EMA20/50 cross L/S 4h', style='trend', fn=sig_regime_ema_cross,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=120)),
    'r3_sideways_band_fade_4h': dict(
        name='[RESEARCH] R3B Sideways-regime Bollinger fade L/S 4h', style='range', fn=sig_sideways_band_fade,
        sides='both', tf='4h', research_only=True, params=dict(rsi_lo=30.0, rsi_hi=70.0),
        mgmt=dict(stop_atr=2.0, max_bars=20)),
    's3_btc_up_laggard_breakdown_4h': dict(
        name='[RESEARCH] S3A BTC-up laggard breakdown short 4h', style='trend', fn=sig_btc_up_laggard_breakdown,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
