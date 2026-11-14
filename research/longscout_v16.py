"""LONGSCOUT-V16: five new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None is r6_regime_flip_4h with a filter added.

L16A l16_btc_breakout_participation_4h  long  first 4h bar where BTC closes above its prior 55-bar high; buy coins that are
                                              rising (R1) at that close. Stop 3 ATR, 3R, 60 bars.
S16A s16_btc_breakdown_participation_4h short mirror: BTC closes below its prior 55-bar low; short coins that are falling (R1).
R16A r16_hull_turn_regime_4h            both  Hull MA(55) turns up while the coin is rising (R1) -> long; turns down while
                                              falling -> short; exit on the opposite turn.
L16B l16_inside_day_breakout_4h         long  the last completed UTC day was an inside day (high and low inside the day before);
                                              a 4h close crosses above its high while the coin is rising (R1). Stop 2.5 ATR,
                                              2.5R, 30 bars.
S16B s16_inside_day_breakdown_4h        short mirror below the inside day's low while falling (R1).
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def _btc_break(d, n):
    f = L1._BTC['f']
    b = f.set_index(pd.to_datetime(f.t))
    up = b.c > b.h.rolling(n).max().shift(); dn = b.c < b.l.rolling(n).min().shift()
    idx = pd.to_datetime(d.t)
    return (pd.Series(up.reindex(idx).fillna(False).values, index=d.index),
            pd.Series(dn.reindex(idx).fillna(False).values, index=d.index))


def sig_btc_breakout_participation(d, p, ctx):
    cr = L1.coin_regime(d)
    up, _ = _btc_break(d, p['n'])
    return dict(le=S.X(_first(up) & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_btc_breakdown_participation(d, p, ctx):
    cr = L1.coin_regime(d)
    _, dn = _btc_break(d, p['n'])
    return dict(le=_z(d), se=S.X(_first(dn) & (cr == -1)), lx=_z(d), sx=_z(d))


def _wma(x, n):
    w = np.arange(1, n + 1, dtype=float)
    return x.rolling(n, min_periods=n).apply(lambda a: (a * w).sum() / w.sum(), raw=True)


def sig_hull_turn_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    n = p['n']
    hma = _wma(2 * _wma(d.c, n // 2) - _wma(d.c, n), int(np.sqrt(n)))
    up = (hma > hma.shift()) & (hma.shift() <= hma.shift(2))
    dn = (hma < hma.shift()) & (hma.shift() >= hma.shift(2))
    return dict(le=S.X(up & (cr == 1)), se=S.X(dn & (cr == -1)), lx=S.X(dn), sx=S.X(up))


def _inside_day(d):
    t = pd.to_datetime(d.t)
    g = pd.DataFrame({'h': d.h.values, 'l': d.l.values, 'day': t.dt.normalize().values}).groupby('day')
    full = g.size() == 6
    H, Lo = g.h.max()[full], g.l.min()[full]
    prev_ok = (H.index.to_series().diff() == pd.Timedelta(days=1))
    inside = ((H < H.shift()) & (Lo > Lo.shift()) & prev_ok).astype(float)
    on = lambda s: pd.Series(L1.on_rows(s, d.t), index=d.index)
    return on(inside) == 1, on(H), on(Lo)


def sig_inside_day_breakout(d, p, ctx):
    cr = L1.coin_regime(d)
    ins, H, _ = _inside_day(d)
    return dict(le=S.X(S.xup(d.c, H) & ins & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_inside_day_breakdown(d, p, ctx):
    cr = L1.coin_regime(d)
    ins, _, Lo = _inside_day(d)
    return dict(le=_z(d), se=S.X(S.xup(-d.c, -Lo) & ins & (cr == -1)), lx=_z(d), sx=_z(d))


ID = dict(stop_atr=2.5, tp_r=2.5, max_bars=30)
CANDIDATES = {
    'l16_btc_breakout_participation_4h': dict(
        name='[RESEARCH] L16A BTC 55-bar breakout participation long 4h', style='trend', fn=sig_btc_breakout_participation,
        sides='long', tf='4h', research_only=True, params=dict(n=55), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    's16_btc_breakdown_participation_4h': dict(
        name='[RESEARCH] S16A BTC 55-bar breakdown participation short 4h', style='trend', fn=sig_btc_breakdown_participation,
        sides='short', tf='4h', research_only=True, params=dict(n=55), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=60)),
    'r16_hull_turn_regime_4h': dict(
        name='[RESEARCH] R16A Regime Hull MA(55) turn L/S 4h', style='trend', fn=sig_hull_turn_regime,
        sides='both', tf='4h', research_only=True, params=dict(n=55), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l16_inside_day_breakout_4h': dict(
        name='[RESEARCH] L16B Inside-day breakout long 4h', style='trend', fn=sig_inside_day_breakout,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(ID)),
    's16_inside_day_breakdown_4h': dict(
        name='[RESEARCH] S16B Inside-day breakdown short 4h', style='trend', fn=sig_inside_day_breakdown,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(ID)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
