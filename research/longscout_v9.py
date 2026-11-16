"""LONGSCOUT-V9: five new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None re-parameterises r6_regime_flip_4h.

R9A r9_obv_trend_regime_4h        both  on-balance volume crosses above its EMA50 while the coin is rising (R1) -> long;
                                        mirror while falling -> short; exit on the opposite OBV/EMA50 cross.
L9A l9_decoupled_strength_4h      long  first 4h bar where the coin is rising (R1) while BTC is falling; exit when the coin
                                        stops rising. Strength that does not need the market.
L9B l9_weekly_range_breakout_4h   long  after the week's first UTC day (Monday) closes, the first close that week above
                                        that day's high while the coin is rising (R1). Stop 2.5 ATR, 3R, 42 bars.
R9B r9_keltner_regime_4h          both  close crosses above EMA20 + 2 ATR while the coin is rising (R1) -> long; mirror below
                                        EMA20 - 2 ATR while falling -> short; exit on a close back through EMA20.
S9A s9_alt_weakness_hold_4h       short first 4h bar where the coin is falling (R1) while BTC is rising; exit when the coin
                                        stops falling. Written knowing exp_trend's shorts earned in alt weakness (V1 regime
                                        split), so only its result on these frozen rules counts.
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


def sig_obv_trend_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    obv = (np.sign(d.c.diff().fillna(0)) * d.v).cumsum()
    e = S.ema(obv, p['ema'])
    xu, xd = S.xup(obv, e), S.xup(e, obv)
    return dict(le=S.X(xu & (cr == 1)), se=S.X(xd & (cr == -1)), lx=S.X(xd), sx=S.X(xu))


def sig_decoupled_strength(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index); br = pd.Series(L1.btc_regime(d), index=d.index)
    on = (cr == 1) & (br == -1)
    return dict(le=_first(on), se=_z(d), lx=S.X(cr != 1), sx=_z(d))


def sig_weekly_range_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    t = pd.to_datetime(d.t)
    wk = (t.dt.normalize() - pd.to_timedelta(t.dt.weekday, unit='D'))
    day1 = (t - wk) < pd.Timedelta(days=1)
    wh = d.h.where(day1).groupby(wk).cummax().groupby(wk).ffill()
    hit = S.X((~day1) & (d.c > wh) & (cr == 1))
    le = hit & (hit.astype(int).groupby(wk).cumsum() == 1)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_keltner_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    up, dn = d.e20 + p['k'] * d.atr, d.e20 - p['k'] * d.atr
    return dict(le=S.X(S.xup(d.c, up) & (cr == 1)), se=S.X(S.xup(-d.c, -dn) & (cr == -1)),
                lx=S.X(d.c < d.e20), sx=S.X(d.c > d.e20))


def sig_alt_weakness_hold(d, p, ctx):
    cr = pd.Series(L1.coin_regime(d), index=d.index); br = pd.Series(L1.btc_regime(d), index=d.index)
    on = (cr == -1) & (br == 1)
    return dict(le=_z(d), se=_first(on), lx=_z(d), sx=S.X(cr != -1))


CANDIDATES = {
    'r9_obv_trend_regime_4h': dict(
        name='[RESEARCH] R9A Regime OBV/EMA50 cross L/S 4h', style='trend', fn=sig_obv_trend_regime,
        sides='both', tf='4h', research_only=True, params=dict(ema=50), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l9_decoupled_strength_4h': dict(
        name='[RESEARCH] L9A Coin rising while BTC falling hold long 4h', style='trend', fn=sig_decoupled_strength,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=3.0, max_bars=120)),
    'l9_weekly_range_breakout_4h': dict(
        name='[RESEARCH] L9B Monday-range breakout long 4h', style='trend', fn=sig_weekly_range_breakout,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=42)),
    'r9_keltner_regime_4h': dict(
        name='[RESEARCH] R9B Regime Keltner(20, 2 ATR) breakout L/S 4h', style='trend', fn=sig_keltner_regime,
        sides='both', tf='4h', research_only=True, params=dict(k=2.0), mgmt=dict(stop_atr=2.5, max_bars=120)),
    's9_alt_weakness_hold_4h': dict(
        name='[RESEARCH] S9A Coin falling while BTC rising hold short 4h', style='trend', fn=sig_alt_weakness_hold,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=3.0, max_bars=120)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
