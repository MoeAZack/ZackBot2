"""LONGSCOUT-V26: the three V25 day/week rules re-frozen after an implementation defect (Claude Projects, 2026-11-14).

V25 (rules f1fc8dde) compared day stamps as raw int64 views. On the real data the time column is datetime64[ms] while the
shifted row date is not, so the units differed, the "previous day" check was never true, and l25 weekly breakout,
s25 weekly breakdown and r25 Heikin flip opened ZERO trades on all 165 coins. No trade return of those three rules was
ever seen. The synthetic tests used ns timestamps, so they missed it. This module fixes only the comparison (both sides
converted to whole UTC day numbers) and adds ms-timestamp tests. Hypotheses, parameters and management are unchanged
from V25; new keys so the V25 record stays as run. FROZEN before any backtest outcome of these rules. research_only.

L26A l26_weekly_close_breakout_4h   = L25A.   S26A s26_weekly_close_breakdown_4h = S25A.   R26A r26_daily_heikin_flip_4h = R25A.
(L25B turn-of-month was not affected and keeps its V25 result.)
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
    day_no = (pd.DatetimeIndex(D.index).astype('datetime64[ns]').asi8 // 86_400_000_000_000).astype(float)
    seen = L17._on(pd.Series(day_no, index=D.index), d).values
    want = pd.to_datetime(d.t).astype('datetime64[ns]').values.astype('int64') // 86_400_000_000_000 - 1
    return seen == want


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
    'l26_weekly_close_breakout_4h': dict(
        name='[RESEARCH] L26A 12-week weekly-close breakout long 4h (V25 L25A fixed)', style='trend',
        fn=sig_weekly_close_breakout, sides='long', tf='4h', research_only=True, params=dict(WK),
        mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    's26_weekly_close_breakdown_4h': dict(
        name='[RESEARCH] S26A 12-week weekly-close breakdown short 4h (V25 S25A fixed)', style='trend',
        fn=sig_weekly_close_breakdown, sides='short', tf='4h', research_only=True, params=dict(WK),
        mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    'r26_daily_heikin_flip_4h': dict(
        name='[RESEARCH] R26A Daily Heikin-Ashi flip with regime L/S 4h (V25 R25A fixed)', style='reversal',
        fn=sig_heikin_flip, sides='both', tf='4h', research_only=True, params=dict(run=3),
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
