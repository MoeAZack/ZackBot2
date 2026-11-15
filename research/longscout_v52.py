"""LONGSCOUT-V52: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori (textbook defaults); none
tuned. Default-OFF, research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with
the unit-safe longscout_v26 helper. New families: the Choppiness Index trend-start trigger, and a touch of the weekly
EMA10 (a weekly moving average was never used before).

R52A r52_daily_chop_trend_4h   both  daily Choppiness Index(14) crosses below 38.2 (range turning into trend): long if the coin
                                     is rising (R1), short if falling. First 4h bar of the next day. Stop 2.5 ATR, 2R, 30 bars.
L52A l52_weekly_ema_touch_4h   long  coin rising (R1); the weekly EMA10 of complete-week closes (latest week that ended before
                                     today) is rising; the last completed day's low touched it and the day closed above it.
                                     First 4h bar of the next day. Stop 2 ATR, 2R, 30 bars.
S52A s52_weekly_ema_touch_4h   short mirror: coin falling; weekly EMA10 falling; the day's high touched it and it closed below.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _chop_v(D, n):
    tr = pd.concat([D.h - D.l, (D.h - D.c.shift()).abs(), (D.l - D.c.shift()).abs()], axis=1).max(axis=1)
    rng = D.h.rolling(n, min_periods=n).max() - D.l.rolling(n, min_periods=n).min()
    return 100 * np.log10(tr.rolling(n, min_periods=n).sum() / rng.replace(0, np.nan)) / np.log10(n)


def _chop(d, p):
    D = L27._days_v(d)
    x = _chop_v(D, p['n'])
    ok = (x < p['lvl']) & (x.shift() >= p['lvl'])
    return L27._next_day(ok & L27._consec(D, p['n'] + 1), D, d)


def _weekly_ema(D, n):
    """Per day: (EMA value, previous EMA value) of the latest complete Mon-Sun week that ended BEFORE that day's week."""
    mon = pd.Series(D.index - pd.to_timedelta(D.index.dayofweek, unit='D'), index=D.index)   # Monday of each day's week
    g = D.c.groupby(mon.values)
    W = pd.DataFrame({'c': g.last(), 'n': g.size()})
    W = W[W.n == 7]                                                       # complete weeks only
    step = W.index.to_series().diff() == pd.Timedelta(days=7)
    e = S.ema(W.c, n).where(step.rolling(n, min_periods=n).sum() == n)   # n consecutive complete weeks behind it
    prev = e.shift().where(step)
    nxt = W.index + pd.Timedelta(days=7)                                  # usable during the following week only
    cur = mon.map(pd.Series(e.values, index=nxt))
    old = mon.map(pd.Series(prev.values, index=nxt))
    return cur, old


def _touch(d, p, up):
    D = L27._days_v(d)
    e, eo = _weekly_ema(D, p['n'])
    if up:
        ok = (e > eo) & (D.l <= e) & (D.c > e)
    else:
        ok = (e < eo) & (D.h >= e) & (D.c < e)
    return L27._next_day(ok.fillna(False), D, d)


def sig_chop(d, p, ctx):
    cr = L1.coin_regime(d)
    q = _chop(d, p)
    return dict(le=S.X(pd.Series(q & (cr == 1), index=d.index)), se=S.X(pd.Series(q & (cr == -1), index=d.index)),
                lx=_z(d), sx=_z(d))


def sig_touch_long(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_touch(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_touch_short(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_touch(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


WM = dict(stop_atr=2.0, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'r52_daily_chop_trend_4h': dict(
        name='[RESEARCH] R52A Daily Choppiness(14) below 38.2 with regime L/S 4h', style='trend', fn=sig_chop,
        sides='both', tf='4h', research_only=True, params=dict(n=14, lvl=38.2),
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'l52_weekly_ema_touch_4h': dict(
        name='[RESEARCH] L52A Weekly EMA10 touch in rising regime long 4h', style='trend', fn=sig_touch_long,
        sides='long', tf='4h', research_only=True, params=dict(n=10), mgmt=dict(WM)),
    's52_weekly_ema_touch_4h': dict(
        name='[RESEARCH] S52A Weekly EMA10 touch in falling regime short 4h', style='trend', fn=sig_touch_short,
        sides='short', tf='4h', research_only=True, params=dict(n=10), mgmt=dict(WM)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
