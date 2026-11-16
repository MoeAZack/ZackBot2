"""LONGSCOUT-V47: three new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. New family: intraday session timing on 4h
bars (UTC). Bars are labelled by open time; a signal on a bar is acted on at that bar's close.

R47A r47_us_session_hold_4h     both  at the close of the 08:00 bar (12:00 UTC, before the US session) go long if the coin is
                                      rising (R1), short if falling; hold 2 bars (through 20:00 UTC). Stop 2 ATR, no target.
L47A l47_asia_session_hold_4h   long  at the close of the 20:00 bar (00:00 UTC, the Asia open) go long if the coin is rising;
                                      hold 2 bars. Stop 2 ATR, no target.
L47B l47_strong_close_overnight_4h long the UTC day closes (20:00 bar) in the top 10% of that day's range, coin rising (R1): hold
                                      into the next day for 3 bars. Stop 2 ATR, no target.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _hour(d, h):
    return (pd.to_datetime(d.t).dt.hour == h).values


def sig_us(d, p, ctx):
    cr = L1.coin_regime(d)
    at = _hour(d, 8)
    return dict(le=S.X(pd.Series(at & (cr == 1), index=d.index)), se=S.X(pd.Series(at & (cr == -1), index=d.index)),
                lx=_z(d), sx=_z(d))


def sig_asia(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_hour(d, 20) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def _strong_close(d, p):
    t = pd.to_datetime(d.t)
    day = t.dt.normalize()
    g = pd.DataFrame({'h': d.h.values, 'l': d.l.values, 'day': day.values}).groupby('day')
    hi = g.h.cummax().values; lo = g.l.cummin().values                         # running day high/low up to this bar
    n = g.cumcount().values
    pos = (d.c.values - lo) / np.where(hi - lo > 0, hi - lo, np.nan)
    return (t.dt.hour.values == 20) & (n == 5) & (pos >= 1 - p['edge'])


def sig_strong_close(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_strong_close(d, p) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'r47_us_session_hold_4h': dict(
        name='[RESEARCH] R47A US-session hold with regime L/S 4h', style='calendar', fn=sig_us,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.0, max_bars=2)),
    'l47_asia_session_hold_4h': dict(
        name='[RESEARCH] L47A Asia-open hold in rising regime long 4h', style='calendar', fn=sig_asia,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.0, max_bars=2)),
    'l47_strong_close_overnight_4h': dict(
        name='[RESEARCH] L47B Strong UTC-day close held overnight long 4h', style='momentum', fn=sig_strong_close,
        sides='long', tf='4h', research_only=True, params=dict(edge=0.1), mgmt=dict(stop_atr=2.0, max_bars=3)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
