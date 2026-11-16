"""LONGSCOUT-V17: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None re-parameterises r6_regime_flip_4h or
s16_inside_day_breakdown_4h (different daily pattern, different side logic).

L17A l17_outside_day_bull_4h       long  the last completed UTC day engulfed the day before (higher high, lower low) and closed
                                         in the top 25% of its range; a 4h close crosses above that day's high while the coin is
                                         rising (R1). Stop 2.5 ATR, 2.5R, 30 bars.
S17A s17_outside_day_bear_4h       short mirror: engulfing day closing in its bottom 25%, 4h close below its low, coin falling.
R17A r17_ema50_envelope_regime_4h  both  close crosses above EMA50 * 1.05 while the coin is rising (R1) -> long; below
                                         EMA50 * 0.95 while falling -> short; exit on a close back through EMA50.
L17B l17_three_green_days_4h       long  the last three completed UTC days each closed above its open, coin rising (R1) and BTC
                                         not falling; enter on the first 4h bar of the next day. Stop 2.5 ATR, 2R, 24 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _days(d):
    """Completed UTC days (all six 4h bars) with o/h/l/c, plus 'prev_ok' = the previous calendar day is present."""
    t = pd.to_datetime(d.t)
    g = pd.DataFrame({'o': d.o.values, 'h': d.h.values, 'l': d.l.values, 'c': d.c.values,
                      'day': t.dt.normalize().values}).groupby('day')
    full = g.size() == 6
    D = pd.DataFrame({'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last()})[full]
    D['prev_ok'] = D.index.to_series().diff() == pd.Timedelta(days=1)
    return D


def _on(s, d):
    return pd.Series(L1.on_rows(s.astype(float), d.t), index=d.index)


def _outside(d, bull):
    D = _days(d)
    rng = (D.h - D.l).replace(0, np.nan)
    eng = (D.h > D.h.shift()) & (D.l < D.l.shift()) & D.prev_ok
    pos = (D.c - D.l) / rng
    ok = eng & ((pos >= 0.75) if bull else (pos <= 0.25))
    return _on(ok, d) == 1, _on(D.h, d), _on(D.l, d)


def sig_outside_day_bull(d, p, ctx):
    cr = L1.coin_regime(d)
    ok, H, _ = _outside(d, True)
    return dict(le=S.X(S.xup(d.c, H) & ok & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_outside_day_bear(d, p, ctx):
    cr = L1.coin_regime(d)
    ok, _, Lo = _outside(d, False)
    return dict(le=_z(d), se=S.X(S.xup(-d.c, -Lo) & ok & (cr == -1)), lx=_z(d), sx=_z(d))


def sig_ema50_envelope_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    up, dn = d.e50 * (1 + p['band']), d.e50 * (1 - p['band'])
    return dict(le=S.X(S.xup(d.c, up) & (cr == 1)), se=S.X(S.xup(-d.c, -dn) & (cr == -1)),
                lx=S.X(d.c < d.e50), sx=S.X(d.c > d.e50))


def sig_three_green_days(d, p, ctx):
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    D = _days(d)
    green = (D.c > D.o) & D.prev_ok
    run = green.astype(float).rolling(p['days'], min_periods=p['days']).sum() == p['days']
    on = _on(run, d) == 1
    t = pd.to_datetime(d.t)
    first_bar = (t.dt.hour == 0).values                    # first 4h bar of the UTC day (opens 00:00)
    le = on & first_bar & (cr == 1) & np.isin(br, (0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


OD = dict(stop_atr=2.5, tp_r=2.5, max_bars=30)
CANDIDATES = {
    'l17_outside_day_bull_4h': dict(
        name='[RESEARCH] L17A Bullish outside-day breakout long 4h', style='trend', fn=sig_outside_day_bull,
        sides='long', tf='4h', research_only=True, params={}, mgmt=dict(OD)),
    's17_outside_day_bear_4h': dict(
        name='[RESEARCH] S17A Bearish outside-day breakdown short 4h', style='trend', fn=sig_outside_day_bear,
        sides='short', tf='4h', research_only=True, params={}, mgmt=dict(OD)),
    'r17_ema50_envelope_regime_4h': dict(
        name='[RESEARCH] R17A Regime EMA50 5% envelope breakout L/S 4h', style='trend', fn=sig_ema50_envelope_regime,
        sides='both', tf='4h', research_only=True, params=dict(band=0.05), mgmt=dict(stop_atr=2.5, max_bars=120)),
    'l17_three_green_days_4h': dict(
        name='[RESEARCH] L17B Three green days continuation long 4h', style='momentum', fn=sig_three_green_days,
        sides='long', tf='4h', research_only=True, params=dict(days=3), mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=24)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
