"""LONGSCOUT-V25: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. Not variants of r6/s16/r17/l24.

L25A l25_weekly_close_breakout_4h   long  the last completed UTC week (Mon-Sun, all 7 days complete) closed above the highest
                                          weekly close of the prior 12 completed weeks; enter on the first 4h bar of the next
                                          Monday, coin not falling (R1). Stop 3 ATR, 3R, 60 bars.
S25A s25_weekly_close_breakdown_4h  short mirror: weekly close below the lowest of the prior 12 weekly closes, coin not rising.
L25B l25_turn_of_month_4h           long  first 4h bar of each calendar month (UTC), coin rising (R1). Stop 2.5 ATR, no target,
                                          18 bars (three days). Calendar effect.
R25A r25_daily_heikin_flip_4h       both  daily Heikin-Ashi candle turns bullish after >= 3 bearish completed days, coin rising
                                          (R1) -> long on the first 4h bar of the next day; mirror (bearish after >= 3 bullish,
                                          coin falling) -> short. Stop 2.5 ATR, 2R, 30 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _day_is_yesterday(D, d):
    """True on rows whose most recent completed day (as on_rows sees it) is exactly the previous calendar day."""
    seen = L17._on(pd.Series(D.index.view('int64'), index=D.index), d)
    t = pd.to_datetime(d.t).dt.normalize() - pd.Timedelta(days=1)
    return (seen.values == t.values.view('int64'))


def _weekly(d, p, up):
    D = L17._days(d)
    wk = D.index - pd.to_timedelta(D.index.weekday, unit='D')
    g = D.c.groupby(wk)
    full = g.size() == 7
    W = g.last()[full]
    consec = W.index.to_series().diff() == pd.Timedelta(days=7)
    n = p['weeks']
    ref = (W.rolling(n, min_periods=n).max() if up else W.rolling(n, min_periods=n).min()).shift()
    ok = ((W > ref) if up else (W < ref)) & consec
    flag = pd.Series(0.0, index=D.index)
    sun = W.index + pd.Timedelta(days=6)
    flag.loc[sun[ok.values]] = 1.0
    t = pd.to_datetime(d.t)
    first = ((t.dt.weekday == 0) & (t.dt.hour == 0)).values
    return (L17._on(flag, d) == 1).values & first & _day_is_yesterday(D, d)


def sig_weekly_close_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_weekly(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=_z(d), lx=_z(d), sx=_z(d))


def sig_weekly_close_breakdown(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_weekly(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)),
                lx=_z(d), sx=_z(d))


def sig_turn_of_month(d, p, ctx):
    cr = L1.coin_regime(d)
    t = pd.to_datetime(d.t)
    first = ((t.dt.day == 1) & (t.dt.hour == 0)).values
    return dict(le=S.X(pd.Series(first & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _heikin(D):
    hc = ((D.o + D.h + D.l + D.c) / 4).values
    ho = np.empty(len(D))
    if len(D):
        ho[0] = (D.o.values[0] + D.c.values[0]) / 2
    for i in range(1, len(D)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return pd.Series(np.sign(hc - ho), index=D.index)


def _ha_flip(d, p, up):
    D = L17._days(d)
    s = _heikin(D)
    want, other = (1.0, -1.0) if up else (-1.0, 1.0)
    run = pd.Series(True, index=D.index)
    for k in range(1, p['run'] + 1):
        run &= (s.shift(k) == other)
    consec = (D.index.to_series().diff() == pd.Timedelta(days=1)).rolling(p['run'] + 1, min_periods=p['run'] + 1).sum() \
        == p['run'] + 1
    ok = (s == want) & run & consec
    first = (pd.to_datetime(d.t).dt.hour == 0).values
    return (L17._on(ok, d) == 1).values & first & _day_is_yesterday(D, d)


def sig_heikin_flip(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_ha_flip(d, p, True) & (cr == 1), index=d.index)),
                se=S.X(pd.Series(_ha_flip(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


WK = dict(weeks=12)
CANDIDATES = {
    'l25_weekly_close_breakout_4h': dict(
        name='[RESEARCH] L25A 12-week weekly-close breakout long 4h', style='trend', fn=sig_weekly_close_breakout,
        sides='long', tf='4h', research_only=True, params=dict(WK), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    's25_weekly_close_breakdown_4h': dict(
        name='[RESEARCH] S25A 12-week weekly-close breakdown short 4h', style='trend', fn=sig_weekly_close_breakdown,
        sides='short', tf='4h', research_only=True, params=dict(WK), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    'l25_turn_of_month_4h': dict(
        name='[RESEARCH] L25B Turn-of-month in rising regime long 4h', style='calendar', fn=sig_turn_of_month,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=18)),
    'r25_daily_heikin_flip_4h': dict(
        name='[RESEARCH] R25A Daily Heikin-Ashi flip with regime L/S 4h', style='reversal', fn=sig_heikin_flip,
        sides='both', tf='4h', research_only=True, params=dict(run=3), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
