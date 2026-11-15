"""LONGSCOUT-V42: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New families (single-day divergence from BTC, money flow index extremes).

L42A l42_strength_on_btc_down_day_4h long  the last completed UTC day the coin rose >= 5% while BTC fell >= 2%, coin not falling
                                           (R1). First 4h bar of the next day. Stop 2.5 ATR, 2.5R, 36 bars.
S42A s42_weakness_on_btc_up_day_4h   short mirror: the coin fell >= 5% while BTC rose >= 2%, coin not rising (R1).
R42A r42_daily_mfi_extreme_4h        both  daily Money Flow Index(14) falls below 20 with the coin rising (R1) -> long (washed-out
                                           dip in an uptrend); rises above 80 with the coin falling -> short. First such day;
                                           first 4h bar of the next day. Stop 2.5 ATR, 2R, 30 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v33 as L33

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _diverge(d, p, up):
    D, J = L33._joint(d)
    if up:
        ok = (J.rc >= p['coin']) & (J.rb <= -p['btc'])
    else:
        ok = (J.rc <= -p['coin']) & (J.rb >= p['btc'])
    return L27._next_day(ok.fillna(False), D, d)


def sig_strength(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_diverge(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_weakness(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_diverge(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


def _mfi(d, p, low):
    D = L27._days_v(d)
    n = p['n']
    tp = (D.h + D.l + D.c) / 3
    mf = tp * D.v
    step = D.index.to_series().diff() == pd.Timedelta(days=1)
    pos = mf.where((tp > tp.shift()) & step, 0.0).rolling(n, min_periods=n).sum()
    neg = mf.where((tp < tp.shift()) & step, 0.0).rolling(n, min_periods=n).sum()
    mfi = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    mfi = mfi.where(neg > 0, 100.0).where(pos.notna())
    hit = (mfi < p['lo']) if low else (mfi > p['hi'])
    ok = hit & ~hit.shift(fill_value=False) & L27._consec(D, n + 1)
    return L27._next_day(ok, D, d)


def sig_mfi(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_mfi(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_mfi(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


DV = dict(coin=0.05, btc=0.02)
CANDIDATES = {
    'l42_strength_on_btc_down_day_4h': dict(
        name='[RESEARCH] L42A Coin up 5% on a BTC down day long 4h', style='momentum', fn=sig_strength,
        sides='long', tf='4h', research_only=True, params=dict(DV), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    's42_weakness_on_btc_up_day_4h': dict(
        name='[RESEARCH] S42A Coin down 5% on a BTC up day short 4h', style='momentum', fn=sig_weakness,
        sides='short', tf='4h', research_only=True, params=dict(DV), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'r42_daily_mfi_extreme_4h': dict(
        name='[RESEARCH] R42A Daily MFI(14) extreme with regime L/S 4h', style='reversal', fn=sig_mfi,
        sides='both', tf='4h', research_only=True, params=dict(n=14, lo=20.0, hi=80.0), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
