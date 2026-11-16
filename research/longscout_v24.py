"""LONGSCOUT-V24: three new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. Not a regime-flip variant.

L24A l24_breakaway_day_4h          long  the last completed UTC day rose >= 8% close-to-close on volume >= 3x the median of the
                                         prior 30 days; enter on the first 4h bar of the next day. Ungated (event-driven).
                                         Stop 2.5 ATR, 2R, 30 bars.
S24A s24_breakdown_day_4h          short mirror: the day fell >= 8% on >= 3x median volume.
R24A r24_return_rank_4h            both  the coin's 180-bar (30-day) return ranks above the 90th percentile of its own prior
                                         2,160 bars (one year) of 180-bar returns -> long; below the 10th -> short; exit when
                                         the rank crosses back through the median. Ungated.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def _event_day(d, p, up):
    t = pd.to_datetime(d.t)
    g = pd.DataFrame({'c': d.c.values, 'v': d.v.values, 'day': t.dt.normalize().values}).groupby('day')
    full = g.size() == 6
    D = pd.DataFrame({'c': g.c.last(), 'v': g.v.sum()})[full]
    prev_ok = D.index.to_series().diff() == pd.Timedelta(days=1)
    ret = D.c / D.c.shift() - 1
    loud = D.v >= p['vol'] * D.v.rolling(p['days'], min_periods=p['days']).median().shift()
    ok = loud & prev_ok & ((ret >= p['move']) if up else (ret <= -p['move']))
    first_bar = (t.dt.hour == 0).values
    return (L17._on(ok, d) == 1) & first_bar


def sig_breakaway_day(d, p, ctx):
    return dict(le=S.X(_event_day(d, p, True)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_breakdown_day(d, p, ctx):
    return dict(le=_z(d), se=S.X(_event_day(d, p, False)), lx=_z(d), sx=_z(d))


def _rank(d, p):
    r = d.c / d.c.shift(p['n']) - 1
    hist = r.shift()
    win = p['win']
    vals = r.values; hv = hist.values
    rank = np.full(len(d), np.nan)
    for i in range(win, len(d)):
        h = hv[i - win + 1:i + 1]
        h = h[~np.isnan(h)]
        if len(h) >= win // 2 and not np.isnan(vals[i]):
            rank[i] = (h < vals[i]).mean()
    return pd.Series(rank, index=d.index)


def sig_return_rank(d, p, ctx):
    rk = _rank(d, p)
    hi, lo = rk > p['hi'], rk < 1 - p['hi']
    return dict(le=_first(hi), se=_first(lo), lx=S.X(rk < 0.5), sx=S.X(rk > 0.5))


EV = dict(move=0.08, vol=3.0, days=30)
CANDIDATES = {
    'l24_breakaway_day_4h': dict(
        name='[RESEARCH] L24A Breakaway high-volume up-day long 4h', style='momentum', fn=sig_breakaway_day,
        sides='long', tf='4h', research_only=True, params=dict(EV), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    's24_breakdown_day_4h': dict(
        name='[RESEARCH] S24A Breakdown high-volume down-day short 4h', style='momentum', fn=sig_breakdown_day,
        sides='short', tf='4h', research_only=True, params=dict(EV), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30)),
    'r24_return_rank_4h': dict(
        name='[RESEARCH] R24A One-year return-rank extremes L/S 4h', style='momentum', fn=sig_return_rank,
        sides='both', tf='4h', research_only=True, params=dict(n=180, win=2160, hi=0.9),
        mgmt=dict(stop_atr=3.0, max_bars=120)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
