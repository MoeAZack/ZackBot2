"""LONGSCOUT-V30: three new research-only 4h candidates (Claude Projects, 2026-11-14). LAST round of the bounded batch.

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. moe set V30 as the ceiling; nothing after this round without moe.

L30A l30_monthly_close_breakout_4h long  the last completed UTC calendar month (every day complete) closed above the highest
                                         monthly close of the prior 6 complete months, coin not falling (R1); enter on the
                                         first 4h bar of the new month. Stop 3 ATR, 3R, 90 bars.
R30A r30_daily_macd_flip_4h        both  daily MACD(12,26,9) histogram turns positive at the last completed day, coin rising (R1)
                                         -> long on the first 4h bar of the next day; turns negative, coin falling -> short.
                                         Stop 2.5 ATR, 2R, 30 bars.
L30B l30_capitulation_dip_4h       long  coin rising (R1); the last 3 completed days fell >= 15% close-to-close and the last day's
                                         volume was >= 2x the median of the prior 30 days; buy the first 4h bar of the next day.
                                         Stop 3 ATR, 2R, 30 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v26 as L26
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _monthly(d, p):
    D = L17._days(d)
    mo = D.index.to_period('M')
    g = D.c.groupby(mo)
    size = g.size()
    need = pd.Series(size.index.days_in_month, index=size.index)
    M = g.last()[size == need]
    consec = pd.Series([x.ordinal for x in M.index], index=M.index, dtype=float).diff() == 1
    n = p['months']
    ok = (M > M.rolling(n, min_periods=n).max().shift()) & consec
    flag = pd.Series(0.0, index=D.index)
    last_day = pd.DatetimeIndex([x.end_time.normalize() for x in M.index])
    flag.loc[last_day[ok.values]] = 1.0
    t = pd.to_datetime(d.t)
    first = ((t.dt.day == 1) & (t.dt.hour == 0)).values
    return (L17._on(flag, d) == 1).values & first & L26._day_is_yesterday(D, d)


def sig_monthly_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_monthly(d, p) & np.isin(cr, (0.0, 1.0)), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _macd_flip(d, p, up):
    D = L27._days_v(d)
    ema = lambda s, n: s.ewm(span=n, adjust=False, min_periods=n).mean()
    macd = ema(D.c, p['fast']) - ema(D.c, p['slow'])
    hist = macd - ema(macd, p['sig'])
    pos = hist > 0
    neg = hist < 0
    ok = (pos & neg.shift(fill_value=False)) if up else (neg & pos.shift(fill_value=False))
    ok = ok & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_macd_flip(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_macd_flip(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_macd_flip(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _capitulation(d, p):
    D = L27._days_v(d)
    k = p['days']
    drop = D.c / D.c.shift(k) - 1 <= -p['fall']
    loud = D.v >= p['vol'] * D.v.rolling(p['base'], min_periods=p['base']).median().shift()
    ok = drop & loud & L27._consec(D, k + 1)
    return L27._next_day(ok, D, d)


def sig_capitulation(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_capitulation(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l30_monthly_close_breakout_4h': dict(
        name='[RESEARCH] L30A 6-month monthly-close breakout long 4h', style='trend', fn=sig_monthly_breakout,
        sides='long', tf='4h', research_only=True, params=dict(months=6), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=90)),
    'r30_daily_macd_flip_4h': dict(
        name='[RESEARCH] R30A Daily MACD histogram flip with regime L/S 4h', style='momentum', fn=sig_macd_flip,
        sides='both', tf='4h', research_only=True, params=dict(fast=12, slow=26, sig=9), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'l30_capitulation_dip_4h': dict(
        name='[RESEARCH] L30B Three-day capitulation dip in rising regime long 4h', style='reversal', fn=sig_capitulation,
        sides='long', tf='4h', research_only=True, params=dict(days=3, fall=0.15, vol=2.0, base=30),
        mgmt=dict(stop_atr=3.0, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
