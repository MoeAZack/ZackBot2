"""LONGSCOUT-V29: three new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. Part of the bounded batch ending at V30.

R29A r29_daily_squeeze_release_4h both  the prior completed day was in a squeeze (Bollinger 20/2sd inside Keltner 20/1.5 ATR on
                                        daily bars) and the last completed day closed outside its Bollinger band: above ->
                                        long if coin not falling (R1); below -> short if coin not rising. First 4h bar of the
                                        next day. Stop 2.5 ATR, 2.5R, 36 bars.
L29A l29_weekly_rsi_cross_4h      long  weekly RSI14 (complete Mon-Sun weeks) crosses above 50 at the last completed week, coin
                                        rising (R1); enter on the Monday 00:00 bar. Stop 3 ATR, 3R, 60 bars.
S29A s29_ema20_reclaim_fail_4h    short coin falling (R1); a completed day closed above the daily EMA20 and the next completed day
                                        closed back below it (failed reclaim); first 4h bar of the following day.
                                        Stop 2.5 ATR, 2R, 30 bars.
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


def _squeeze(d, p, up):
    D = L27._days_v(d)
    n = p['n']
    ma = D.c.rolling(n, min_periods=n).mean(); sd = D.c.rolling(n, min_periods=n).std()
    tr = pd.concat([D.h - D.l, (D.h - D.c.shift()).abs(), (D.l - D.c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n, min_periods=n).mean()
    sq = (ma + p['bb'] * sd < ma + p['kc'] * atr) & (ma - p['bb'] * sd > ma - p['kc'] * atr)
    rel = (D.c > ma + p['bb'] * sd) if up else (D.c < ma - p['bb'] * sd)
    ok = sq.shift(fill_value=False) & rel & L27._consec(D, n)
    return L27._next_day(ok, D, d)


def sig_squeeze_release(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_squeeze(d, p, True) & np.isin(cr, (0.0, 1.0)), index=d.index)),
                se=S.X(pd.Series(_squeeze(d, p, False) & np.isin(cr, (0.0, -1.0)), index=d.index)), lx=_z(d), sx=_z(d))


def _weekly_rsi(d, p):
    D = L17._days(d)
    wk = D.index - pd.to_timedelta(D.index.weekday, unit='D')
    g = D.c.groupby(wk)
    W = g.last()[g.size() == 7]
    consec = W.index.to_series().diff() == pd.Timedelta(days=7)
    r = S.rsi(W, p['n'])
    ok = (r > p['lvl']) & (r.shift() <= p['lvl']) & consec & consec.shift(fill_value=False)
    flag = pd.Series(0.0, index=D.index)
    flag.loc[(W.index + pd.Timedelta(days=6))[ok.values]] = 1.0
    t = pd.to_datetime(d.t)
    first = ((t.dt.weekday == 0) & (t.dt.hour == 0)).values
    return (L17._on(flag, d) == 1).values & first & L26._day_is_yesterday(D, d)


def sig_weekly_rsi_cross(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_weekly_rsi(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _reclaim_fail(d, p):
    D = L27._days_v(d)
    e = D.c.ewm(span=p['n'], adjust=False, min_periods=p['n']).mean()
    above = D.c > e
    ok = above.shift(fill_value=False) & ~above & e.notna() & L27._consec(D, 2)
    return L27._next_day(ok, D, d)


def sig_reclaim_fail(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_reclaim_fail(d, p) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r29_daily_squeeze_release_4h': dict(
        name='[RESEARCH] R29A Daily BB-in-Keltner squeeze release L/S 4h', style='breakout', fn=sig_squeeze_release,
        sides='both', tf='4h', research_only=True, params=dict(n=20, bb=2.0, kc=1.5), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'l29_weekly_rsi_cross_4h': dict(
        name='[RESEARCH] L29A Weekly RSI14 cross above 50 in rising regime long 4h', style='trend', fn=sig_weekly_rsi_cross,
        sides='long', tf='4h', research_only=True, params=dict(n=14, lvl=50.0), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    's29_ema20_reclaim_fail_4h': dict(
        name='[RESEARCH] S29A Failed daily EMA20 reclaim in falling regime short 4h', style='reversal', fn=sig_reclaim_fail,
        sides='short', tf='4h', research_only=True, params=dict(n=20), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
