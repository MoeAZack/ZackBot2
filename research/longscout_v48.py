"""LONGSCOUT-V48: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: the coin's reaction to a large BTC day (contagion / relief), on completed UTC days.

L48A l48_btc_crash_day_buy_4h     long  BTC fell >= 5% on the last completed day while the coin is rising (R1): buy the
                                        contagion dip. First 4h bar of the next day. Stop 3 ATR, 2R, 30 bars.
S48A s48_btc_rally_day_fade_4h    short BTC rose >= 5% on the last completed day while the coin is falling (R1): fade the relief.
                                        Same management.
L48B l48_btc_high_coin_lag_4h     long  BTC closed at a new 20-day closing high on the last completed day while the coin closed
                                        below its own 20-day Donchian midline, coin not falling (R1): daily catch-up.
                                        First 4h bar of the next day. Stop 2.5 ATR, 2R, 30 bars.
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


def _btc_day(d, p, down):
    D, J = L33._joint(d)
    ok = (J.rb <= -p['move']) if down else (J.rb >= p['move'])
    return L27._next_day(ok.fillna(False), D, d)


def sig_crash_buy(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_btc_day(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_rally_fade(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_btc_day(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _lag(d, p):
    D, J = L33._joint(d)
    n = p['n']
    bhi = J.b > J.b.shift().rolling(n, min_periods=n).max()
    mid = (D.h.rolling(n, min_periods=n).max() + D.l.rolling(n, min_periods=n).min()) / 2
    ok = bhi & (D.c < mid) & L27._consec(D, 2)
    return L27._next_day(ok.fillna(False), D, d)


def sig_lag(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_lag(d, p) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


BD = dict(stop_atr=3.0, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l48_btc_crash_day_buy_4h': dict(
        name='[RESEARCH] L48A BTC -5% day contagion dip in rising coin long 4h', style='reversal', fn=sig_crash_buy,
        sides='long', tf='4h', research_only=True, params=dict(move=0.05), mgmt=dict(BD)),
    's48_btc_rally_day_fade_4h': dict(
        name='[RESEARCH] S48A BTC +5% day relief fade in falling coin short 4h', style='reversal', fn=sig_rally_fade,
        sides='short', tf='4h', research_only=True, params=dict(move=0.05), mgmt=dict(BD)),
    'l48_btc_high_coin_lag_4h': dict(
        name='[RESEARCH] L48B BTC 20-day high with lagging coin long 4h', style='momentum', fn=sig_lag,
        sides='long', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
