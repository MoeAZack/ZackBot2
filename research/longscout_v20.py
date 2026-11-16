"""LONGSCOUT-V20: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. Outside the trend-in-regime family of r6/r17.

L20A l20_relative_dip_in_uptrend_4h   long  coin rising (R1) but its price relative to BTC fell >= 10% over 42 bars (first such
                                            bar): relative weakness inside an own uptrend. Stop 2.5 ATR, 2R, 30 bars.
S20A s20_relative_pop_in_downtrend_4h short mirror: coin falling (R1) but its BTC-relative price rose >= 10% over 42 bars.
L20B l20_quiet_volume_breakout_4h     long  20-bar mean volume below half its 180-bar mean on the prior bar (a quiet market), then
                                            the close crosses the prior 20-bar high on volume >= 1.5x its 42-bar mean; coin not
                                            falling (R1). Stop 2.5 ATR, 3R, 60 bars.
S20B s20_quiet_volume_breakdown_4h    short mirror below the prior 20-bar low; coin not rising (R1).
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


def _rel_change(d, n):
    f = L1._BTC['f']
    bc = pd.Series(f.c.values, index=pd.to_datetime(f.t)).reindex(pd.to_datetime(d.t)).values
    r = pd.Series(d.c.values / bc, index=d.index)
    return r / r.shift(n) - 1


def sig_relative_dip_in_uptrend(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_first((_rel_change(d, p['n']) <= -p['move']) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_relative_pop_in_downtrend(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=_first((_rel_change(d, p['n']) >= p['move']) & (cr == -1)), lx=_z(d), sx=_z(d))


def _quiet(d, p):
    return (d.v.rolling(p['short'], min_periods=p['short']).mean()
            < p['quiet'] * d.v.rolling(p['long'], min_periods=p['long']).mean()).shift(fill_value=False)


def sig_quiet_volume_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    le = S.xup(d.c, d.hh20) & _quiet(d, p) & (d.vol_ratio >= p['vol']) & np.isin(cr, (0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_quiet_volume_breakdown(d, p, ctx):
    cr = L1.coin_regime(d)
    se = S.xup(-d.c, -d.ll20) & _quiet(d, p) & (d.vol_ratio >= p['vol']) & np.isin(cr, (0.0, -1.0))
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


REL = dict(n=42, move=0.10)
QV = dict(short=20, long=180, quiet=0.5, vol=1.5)
CANDIDATES = {
    'l20_relative_dip_in_uptrend_4h': dict(
        name='[RESEARCH] L20A BTC-relative dip in rising regime long 4h', style='pullback', fn=sig_relative_dip_in_uptrend,
        sides='long', tf='4h', research_only=True, params=dict(REL), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    's20_relative_pop_in_downtrend_4h': dict(
        name='[RESEARCH] S20A BTC-relative pop in falling regime short 4h', style='pullback', fn=sig_relative_pop_in_downtrend,
        sides='short', tf='4h', research_only=True, params=dict(REL), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'l20_quiet_volume_breakout_4h': dict(
        name='[RESEARCH] L20B Quiet-volume breakout long 4h', style='trend', fn=sig_quiet_volume_breakout,
        sides='long', tf='4h', research_only=True, params=dict(QV), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    's20_quiet_volume_breakdown_4h': dict(
        name='[RESEARCH] S20B Quiet-volume breakdown short 4h', style='trend', fn=sig_quiet_volume_breakdown,
        sides='short', tf='4h', research_only=True, params=dict(QV), mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
