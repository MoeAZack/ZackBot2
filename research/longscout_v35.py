"""LONGSCOUT-V35: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. New families (UTC-day opening range,
prior-week high).

L35A l35_day_open_range_break_4h   long  a later 4h close of the same UTC day crosses above the high of that day's 00:00 bar
                                         (first such cross that day), coin rising (R1). Stop 2 ATR, 1.5R, 6 bars.
S35A s35_day_open_range_break_4h   short mirror: close crosses below the 00:00 bar's low, coin falling (R1).
L35B l35_prior_week_high_break_4h  long  a 4h close crosses above the high of the last completed UTC week (Mon-Sun, all 7 days
                                         complete), the first such cross in the current week, coin rising (R1).
                                         Stop 2.5 ATR, 2.5R, 42 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17

set_btc = L1.set_btc

_DAY_NS = 86_400_000_000_000


def _z(d):
    return pd.Series(False, index=d.index)


def _first_in(group, x):
    x = pd.Series(np.asarray(x, bool), index=group.index)
    return (x & (x.astype(int).groupby(group.values).cumsum() == 1)).values


def _orb(d, up):
    t = pd.to_datetime(d.t)
    day = t.dt.normalize()
    first = t.dt.hour == 0
    lvl = (d.h if up else d.l).where(first)
    lvl = lvl.groupby(day.values).transform('first')               # NaN for a day whose 00:00 bar is missing
    has = first.groupby(day.values).transform('any')
    lvl = lvl.where(has & ~first)
    same = pd.Series(day.values, index=d.index).shift() == pd.Series(day.values, index=d.index)
    x = ((d.c > lvl) & (d.c.shift() <= lvl)) if up else ((d.c < lvl) & (d.c.shift() >= lvl))
    x = np.asarray(x & same, bool) & lvl.notna().values
    return _first_in(day, x)


def sig_orb_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_orb(d, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_orb_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_orb(d, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


def _week_high(d):
    D = L17._days(d)
    wk = D.index - pd.to_timedelta(D.index.weekday, unit='D')
    g = D.h.groupby(wk)
    W = g.max()[g.size() == 7]
    sun = W.index + pd.Timedelta(days=6)
    hi = pd.Series(np.nan, index=D.index); hi.loc[sun] = W.values
    nxt = pd.Series(np.nan, index=D.index)
    nxt.loc[sun] = (pd.DatetimeIndex(W.index + pd.Timedelta(days=7)).astype('datetime64[ns]').asi8 // _DAY_NS).astype(float)
    keep = hi.notna()
    H = L17._on(hi[keep], d); M = L17._on(nxt[keep], d)
    t = pd.to_datetime(d.t)
    mon = (t - pd.to_timedelta(t.dt.weekday, unit='D')).dt.normalize().astype('datetime64[ns]').values.astype('int64') // _DAY_NS
    return H.where(M.values == mon), pd.Series(mon, index=d.index)


def sig_week_high(d, p, ctx):
    cr = L1.coin_regime(d)
    H, mon = _week_high(d)
    x = np.asarray((d.c > H) & (d.c.shift() <= H), bool) & H.notna().values
    return dict(le=S.X(pd.Series(_first_in(mon, x) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l35_day_open_range_break_4h': dict(
        name='[RESEARCH] L35A UTC-day opening-range break in rising regime long 4h', style='breakout', fn=sig_orb_long,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=6)),
    's35_day_open_range_break_4h': dict(
        name='[RESEARCH] S35A UTC-day opening-range break in falling regime short 4h', style='breakout', fn=sig_orb_short,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=6)),
    'l35_prior_week_high_break_4h': dict(
        name='[RESEARCH] L35B Prior-week high break in rising regime long 4h', style='breakout', fn=sig_week_high,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=42)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
