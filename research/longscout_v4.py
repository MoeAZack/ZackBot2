"""LONGSCOUT-V4: six new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers come unchanged from longscout_v1.py. None is a re-parameterisation of a
V1-V3 rule; each tests a different market idea.

L4A l4_rs_leader_ema20_reclaim_4h  long  coin's 30-day return beats BTC's by >= 15 points; after >= 6 bars below the 4h
                                         EMA20 the close crosses back above it; BTC not falling. Stop 2 ATR, 2.5R, 40 bars.
L4B l4_failed_breakdown_long_4h    long  a close below the prior 20-bar low is reclaimed (close back above that level)
                                         within 3 bars, coin not falling (R1); stop under the lowest low since the break.
S4A s4_rs_laggard_short_4h         short coin's 30-day return trails BTC's by >= 15 points; close crosses below the 4h
                                         EMA50 with EMA50 < EMA200. Stop 2.5 ATR, 3R, 60 bars.
S4B s4_failed_breakout_short_4h    short a close above the prior 20-bar high fails back below that level within 3 bars,
                                         coin not rising (R1); stop over the highest high since the break.
R4A r4_supertrend_regime_4h        both  Supertrend(10,3) flips in the coin's R1 direction; exit on the opposite flip.
R4B r4_donchian55_trail_4h         both  close through the prior 55-bar extreme in the coin's R1 direction; 3 ATR
                                         chandelier trail, 180-bar cap.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _rs(d, n):
    return (L1.ret_days(d, n) - L1.ret_days(d, n, L1._BTC['f']))


def sig_rs_leader_reclaim(d, p, ctx):
    br = L1.btc_regime(d)
    below = (d.c < d.e20).astype(float).rolling(p['below'], min_periods=p['below']).sum().shift() == p['below']
    le = S.xup(d.c, d.e20) & below & (_rs(d, p['rs_days']) >= p['rs_gap']) & (br >= 0)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def _failed_break(d, p, side):
    """side=+1: failed breakdown (long); side=-1: failed breakout (short). Returns (signal, stop distance)."""
    c, h, l, a = d.c.values, d.h.values, d.l.values, d.atr.values
    lvl_src = d.ll20.values if side == 1 else d.hh20.values
    n = len(d); sig = np.zeros(n, bool); dist = np.full(n, np.nan)
    live = None                                      # (level, bar of the break, extreme since)
    for i in range(1, n):
        lv = lvl_src[i]
        broke = (c[i] < lv and c[i - 1] >= lvl_src[i - 1]) if side == 1 else (c[i] > lv and c[i - 1] <= lvl_src[i - 1])
        if broke and lv == lv:
            live = [lv, i, l[i] if side == 1 else h[i]]
            continue
        if live is None: continue
        live[2] = min(live[2], l[i]) if side == 1 else max(live[2], h[i])
        if i - live[1] > p['within']:
            live = None; continue
        back = c[i] > live[0] if side == 1 else c[i] < live[0]
        if back:
            dd = (c[i] - (live[2] - p['buf_atr'] * a[i])) if side == 1 else ((live[2] + p['buf_atr'] * a[i]) - c[i])
            sig[i] = True; dist[i] = max(dd, p['min_atr'] * a[i])
            live = None
    return sig, dist


def sig_failed_breakdown_long(d, p, ctx):
    cr = L1.coin_regime(d)
    s, dist = _failed_break(d, p, 1)
    le = s & np.isin(cr, (0.0, 1.0))
    return dict(le=pd.Series(le, d.index), se=_z(d), lx=_z(d), sx=_z(d), atr=np.where(le, dist, np.nan))


def sig_failed_breakout_short(d, p, ctx):
    cr = L1.coin_regime(d)
    s, dist = _failed_break(d, p, -1)
    se = s & np.isin(cr, (0.0, -1.0))
    return dict(le=_z(d), se=pd.Series(se, d.index), lx=_z(d), sx=_z(d), atr=np.where(se, dist, np.nan))


def sig_rs_laggard_short(d, p, ctx):
    se = S.xup(d.e50, d.c) & (d.e50 < d.e200) & (_rs(d, p['rs_days']) <= -p['rs_gap'])
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


def sig_supertrend_regime(d, p, ctx):
    cr = L1.coin_regime(d)
    up = (d.st == 1) & (d.st.shift() == -1); dn = (d.st == -1) & (d.st.shift() == 1)
    return dict(le=S.X(up & (cr == 1)), se=S.X(dn & (cr == -1)), lx=S.X(dn), sx=S.X(up))


def sig_donchian55_trail(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(S.xup(d.c, d.hh55) & (cr == 1)), se=S.X(S.xup(-d.c, -d.ll55) & (cr == -1)), lx=_z(d), sx=_z(d))


FB = dict(within=3, buf_atr=0.25, min_atr=1.0)
CANDIDATES = {
    'l4_rs_leader_ema20_reclaim_4h': dict(
        name='[RESEARCH] L4A RS-leader EMA20 reclaim long 4h', style='pullback', fn=sig_rs_leader_reclaim,
        sides='long', tf='4h', research_only=True, params=dict(below=6, rs_days=30, rs_gap=0.15),
        mgmt=dict(stop_atr=2.0, tp_r=2.5, max_bars=40)),
    'l4_failed_breakdown_long_4h': dict(
        name='[RESEARCH] L4B Failed-breakdown reclaim long 4h', style='reversal', fn=sig_failed_breakdown_long,
        sides='long', tf='4h', research_only=True, params=dict(FB), mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=30)),
    's4_rs_laggard_short_4h': dict(
        name='[RESEARCH] S4A RS-laggard EMA50 loss short 4h', style='trend', fn=sig_rs_laggard_short,
        sides='short', tf='4h', research_only=True, params=dict(rs_days=30, rs_gap=0.15),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
    's4_failed_breakout_short_4h': dict(
        name='[RESEARCH] S4B Failed-breakout short 4h', style='reversal', fn=sig_failed_breakout_short,
        sides='short', tf='4h', research_only=True, params=dict(FB), mgmt=dict(stop_atr=1.0, tp_r=2.0, max_bars=30)),
    'r4_supertrend_regime_4h': dict(
        name='[RESEARCH] R4A Regime-direction Supertrend flip L/S 4h', style='trend', fn=sig_supertrend_regime,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, max_bars=120)),
    'r4_donchian55_trail_4h': dict(
        name='[RESEARCH] R4B Regime-direction 55-bar breakout with ATR trail L/S 4h', style='trend', fn=sig_donchian55_trail,
        sides='both', tf='4h', research_only=True, params={}, mgmt=dict(stop_atr=2.5, trail_atr=3.0, max_bars=180)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
