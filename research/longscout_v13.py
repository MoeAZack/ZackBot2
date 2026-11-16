"""LONGSCOUT-V13: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None is r6_regime_flip_4h with a filter added.

L13A l13_daily_higher_lows_4h      long  the last 3 completed UTC days each made a higher low than the day before, and a 4h
                                         close crosses above the last completed day's high; coin not falling (R1).
                                         Stop 2.5 ATR, 2.5R, 36 bars.
S13A s13_daily_lower_highs_4h      short mirror: 3 completed days of lower highs, 4h close crosses below the last day's low;
                                         coin not rising (R1).
R13A r13_rsi50_regime_4h           both  RSI14 crosses above 50 while the coin is rising (R1) -> long; crosses below 50 while
                                         falling -> short; exit on the opposite 50 cross.
L13B l13_strong_close_streak_4h    long  three consecutive 4h bars each closing in the top 25% of its range (first such bar),
                                         coin rising (R1). Stop 2 ATR, 2R, 24 bars.
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


def _daily_hl(d):
    """Completed-day highs and lows (days with all six 4h bars), indexed by day; usable from day + 1 00:00 via on_rows."""
    t = pd.to_datetime(d.t)
    day = t.dt.normalize()
    g = pd.DataFrame({'h': d.h.values, 'l': d.l.values, 'day': day.values}).groupby('day')
    full = g.size() == 6
    return g.h.max()[full], g.l.min()[full]


def _stairs(d, n, up):
    hi, lo = _daily_hl(d)
    s = lo if up else hi
    step = (s > s.shift()) if up else (s < s.shift())
    ok = step.astype(float).rolling(n, min_periods=n).sum() == n
    return (pd.Series(L1.on_rows(ok.astype(float), d.t), index=d.index) == 1,
            pd.Series(L1.on_rows(hi, d.t), index=d.index), pd.Series(L1.on_rows(lo, d.t), index=d.index))


def sig_daily_higher_lows(d, p, ctx):
    cr = L1.coin_regime(d)
    ok, ph, _ = _stairs(d, p['days'], True)
    le = S.xup(d.c, ph) & ok & np.isin(cr, (0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_daily_lower_highs(d, p, ctx):
    cr = L1.coin_regime(d)
    ok, _, pl = _stairs(d, p['days'], False)
    se = S.xup(-d.c, -pl) & ok & np.isin(cr, (0.0, -1.0))
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


def sig_rsi50_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    mid = d.rsi14 * 0 + 50
    xu, xd = S.xup(d.rsi14, mid), S.xup(mid, d.rsi14)
    return dict(le=S.X(xu & (cr == 1)), se=S.X(xd & (cr == -1)), lx=S.X(xd), sx=S.X(xu))


def sig_strong_close_streak(d, p, ctx):
    cr = L1.coin_regime(d)
    rng = (d.h - d.l).replace(0, np.nan)
    strong = ((d.c - d.l) / rng >= p['top']).astype(float)
    streak = strong.rolling(p['n'], min_periods=p['n']).sum() == p['n']
    return dict(le=_first(streak & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


CANDIDATES = {
    'l13_daily_higher_lows_4h': dict(
        name='[RESEARCH] L13A Daily higher-lows staircase breakout long 4h', style='structure', fn=sig_daily_higher_lows,
        sides='long', tf='4h', research_only=True, params=dict(days=3), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    's13_daily_lower_highs_4h': dict(
        name='[RESEARCH] S13A Daily lower-highs staircase breakdown short 4h', style='structure', fn=sig_daily_lower_highs,
        sides='short', tf='4h', research_only=True, params=dict(days=3), mgmt=dict(stop_atr=2.5, tp_r=2.5, max_bars=36)),
    'r13_rsi50_regime_4h': dict(
        name='[RESEARCH] R13A Regime RSI14 50-cross L/S 4h', style='trend', fn=sig_rsi50_regime,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l13_strong_close_streak_4h': dict(
        name='[RESEARCH] L13B Strong-close streak long 4h', style='momentum', fn=sig_strong_close_streak,
        sides='long', tf='4h', research_only=True, params=dict(n=3, top=0.75), mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=24)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
