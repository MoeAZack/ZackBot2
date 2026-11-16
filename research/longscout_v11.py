"""LONGSCOUT-V11: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None is r6_regime_flip_4h with a filter added.

L11A l11_btc_capitulation_buy_4h      long  a BTC 4h close-to-close drop of >= 3 BTC ATRs while the coin is rising (R1): buy
                                            the coin at that bar's close. Stop 2.5 ATR, 2R, 30 bars. Changed timeframe and
                                            trigger vs L2B (daily, 2 ATR, BTC rising).
R11A r11_vwap30_regime_cross_4h       both  close crosses above the rolling 180-bar (30-day) VWAP while the coin is rising (R1)
                                            -> long; mirror while falling -> short; exit on a close back through the VWAP.
L11B l11_mature_trend_breakout_4h     long  coin rising (R1) on every one of the last 180 bars (30 days) and BTC not falling;
                                            close crosses the prior 20-bar high. Stop 2.5 ATR, 3R, 60 bars.
S11A s11_mature_downtrend_breakdown_4h short mirror: coin falling on every one of the last 180 bars and BTC not rising; close
                                            crosses below the prior 20-bar low.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def sig_btc_capitulation_buy(d, p, ctx):
    cr = L1.coin_regime(d)
    f = L1._BTC['f']
    bi = S.indicators(f.reset_index(drop=True))
    idx = pd.to_datetime(d.t)
    bc = pd.Series(bi.c.values, index=pd.to_datetime(bi.t))
    ba = pd.Series(bi.atr.values, index=pd.to_datetime(bi.t))
    drop = (bc - bc.shift()) <= -p['atr_k'] * ba.shift()
    on = pd.Series(drop.reindex(idx).fillna(False).values, index=d.index)
    return dict(le=S.X(on & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_vwap30_regime_cross(d, p, ctx):
    cr = L1.coin_regime(d)
    tp = (d.h + d.l + d.c) / 3
    vw = (tp * d.v).rolling(p['n'], min_periods=p['n']).sum() / d.v.rolling(p['n'], min_periods=p['n']).sum()
    xu, xd = S.xup(d.c, vw), S.xup(vw, d.c)
    return dict(le=S.X(xu & (cr == 1)), se=S.X(xd & (cr == -1)), lx=S.X(d.c < vw), sx=S.X(d.c > vw))


def _held(cr, state, n):
    return pd.Series((cr == state).astype(float)).rolling(n, min_periods=n).sum().values == n


def sig_mature_trend_breakout(d, p, ctx):
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    le = S.xup(d.c, d.hh20) & _held(cr, 1, p['held']) & np.isin(br, (0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_mature_downtrend_breakdown(d, p, ctx):
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    se = S.xup(-d.c, -d.ll20) & _held(cr, -1, p['held']) & np.isin(br, (0.0, -1.0))
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l11_btc_capitulation_buy_4h': dict(
        name='[RESEARCH] L11A BTC 4h capitulation buy of rising coins long 4h', style='pullback', fn=sig_btc_capitulation_buy,
        sides='long', tf='4h', research_only=True, params=dict(atr_k=3.0), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'r11_vwap30_regime_cross_4h': dict(
        name='[RESEARCH] R11A Regime 30-day VWAP cross L/S 4h', style='trend', fn=sig_vwap30_regime_cross,
        sides='both', tf='4h', research_only=True, params=dict(n=180), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l11_mature_trend_breakout_4h': dict(
        name='[RESEARCH] L11B Mature-uptrend 20-bar breakout long 4h', style='trend', fn=sig_mature_trend_breakout,
        sides='long', tf='4h', research_only=True, params=dict(held=180), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    's11_mature_downtrend_breakdown_4h': dict(
        name='[RESEARCH] S11A Mature-downtrend 20-bar breakdown short 4h', style='trend', fn=sig_mature_downtrend_breakdown,
        sides='short', tf='4h', research_only=True, params=dict(held=180), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
