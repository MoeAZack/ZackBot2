"""LONGSCOUT-V14: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None is r6_regime_flip_4h with a filter added.

L14A l14_ema200_reclaim_btc_up_4h     long  after >= 30 bars closing below the 4h EMA200, the close crosses back above it while
                                            BTC is rising (R1). Stop 2.5 ATR, 3R, 60 bars.
S14A s14_ema200_loss_btc_down_4h      short after >= 30 bars closing above the 4h EMA200, the close crosses below it while BTC
                                            is falling (R1). Stop 2.5 ATR, 3R, 60 bars.
R14A r14_dmi_cross_regime_4h          both  +DI(14) crosses above -DI(14) with ADX >= 20 while the coin is rising (R1) -> long;
                                            mirror while falling -> short; exit on the opposite DI cross.
L14B l14_volume_breakout_55_4h        long  close crosses the prior 55-bar high on volume >= 2x its 42-bar mean, coin rising (R1).
                                            Stop 2.5 ATR, 3R, 60 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _stayed(x, n):
    return x.astype(float).rolling(n, min_periods=n).sum().shift() == n


def sig_ema200_reclaim_btc_up(d, p, ctx):
    br = L1.btc_regime(d)
    le = S.xup(d.c, d.e200) & _stayed(d.c < d.e200, p['n']) & (br == 1)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_ema200_loss_btc_down(d, p, ctx):
    br = L1.btc_regime(d)
    se = S.xup(d.e200, d.c) & _stayed(d.c > d.e200, p['n']) & (br == -1)
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


def _dmi(d):
    up, dn = d.h.diff(), -d.l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), d.index).ewm(alpha=1 / 14, adjust=False).mean()
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), d.index).ewm(alpha=1 / 14, adjust=False).mean()
    return 100 * pdm / d.atr, 100 * ndm / d.atr


def sig_dmi_cross_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    pdi, ndi = _dmi(d)
    xu, xd = S.xup(pdi, ndi), S.xup(ndi, pdi)
    strong = d.adx >= p['adx']
    return dict(le=S.X(xu & strong & (cr == 1)), se=S.X(xd & strong & (cr == -1)), lx=S.X(xd), sx=S.X(xu))


def sig_volume_breakout_55(d, p, ctx):
    cr = L1.coin_regime(d)
    le = S.xup(d.c, d.hh55) & (d.vol_ratio >= p['vol']) & (cr == 1)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l14_ema200_reclaim_btc_up_4h': dict(
        name='[RESEARCH] L14A EMA200 reclaim with BTC rising long 4h', style='reversal', fn=sig_ema200_reclaim_btc_up,
        sides='long', tf='4h', research_only=True, params=dict(n=30), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    's14_ema200_loss_btc_down_4h': dict(
        name='[RESEARCH] S14A EMA200 loss with BTC falling short 4h', style='trend', fn=sig_ema200_loss_btc_down,
        sides='short', tf='4h', research_only=True, params=dict(n=30), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    'r14_dmi_cross_regime_4h': dict(
        name='[RESEARCH] R14A Regime DMI cross with ADX>=20 L/S 4h', style='trend', fn=sig_dmi_cross_regime,
        sides='both', tf='4h', research_only=True, params=dict(adx=20.0), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l14_volume_breakout_55_4h': dict(
        name='[RESEARCH] L14B High-volume 55-bar breakout long 4h', style='trend', fn=sig_volume_breakout_55,
        sides='long', tf='4h', research_only=True, params=dict(vol=2.0), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
