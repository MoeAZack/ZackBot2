"""LONGSCOUT-V41: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook definitions); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New families (TD-style sequential setup, volume shock without price change).

R41A r41_daily_setup9_4h        both  a daily buy setup completes: 9 consecutive completed days each closing below the close 4
                                      days earlier, with the coin rising (R1) -> long (exhausted dip in an uptrend); a sell setup
                                      (9 closes above the close 4 days earlier) with the coin falling -> short. First 4h bar of
                                      the next day. Stop 2.5 ATR, 2R, 30 bars.
L41A l41_quiet_volume_shock_4h  long  the last completed day had volume >= 3x the median of the prior 30 days but a close-to-close
                                      move under 1.5% (absorbed volume), coin rising (R1). First 4h bar of the next day.
                                      Stop 2.5 ATR, 2.5R, 36 bars.
S41A s41_quiet_volume_shock_4h  short the same day pattern with the coin falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _setup9(d, p, buy):
    D = L27._days_v(d)
    lag, k = p['lag'], p['count']
    cond = (D.c < D.c.shift(lag)) if buy else (D.c > D.c.shift(lag))
    run = cond.astype(float).rolling(k, min_periods=k).sum() == k
    ok = run & ~run.shift(fill_value=False) & L27._consec(D, k + lag)
    return L27._next_day(ok, D, d)


def sig_setup9(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_setup9(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_setup9(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _shock(d, p):
    D = L27._days_v(d)
    med = D.v.rolling(p['base'], min_periods=p['base']).median().shift()
    move = (D.c / D.c.shift() - 1).abs()
    ok = (D.v >= p['vol'] * med) & (move < p['flat']) & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_shock_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_shock(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_shock_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_shock(d, p) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


SH = dict(base=30, vol=3.0, flat=0.015)
CANDIDATES = {
    'r41_daily_setup9_4h': dict(
        name='[RESEARCH] R41A Daily 9-count setup exhaustion with regime L/S 4h', style='reversal', fn=sig_setup9,
        sides='both', tf='4h', research_only=True, params=dict(lag=4, count=9), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'l41_quiet_volume_shock_4h': dict(
        name='[RESEARCH] L41A Absorbed volume shock in rising regime long 4h', style='momentum', fn=sig_shock_long,
        sides='long', tf='4h', research_only=True, params=dict(SH), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    's41_quiet_volume_shock_4h': dict(
        name='[RESEARCH] S41A Absorbed volume shock in falling regime short 4h', style='momentum', fn=sig_shock_short,
        sides='short', tf='4h', research_only=True, params=dict(SH), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
