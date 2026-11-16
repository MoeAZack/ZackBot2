"""LONGSCOUT-V21: four new research-only 4h candidates (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py. None re-parameterises r6/s16/r17.

R21A r21_autocorr_continuation_4h  both  lag-1 autocorrelation of 4h returns over the prior 42 bars >= 0.1 (returns persisting)
                                         and this bar moved >= 1 ATR close-to-close -> enter in that bar's direction. Ungated.
                                         Stop 2 ATR, 2R, 12 bars.
L21A l21_btc_lead_catchup_4h       long  BTC rose >= 1.5 of its ATRs this bar while the coin moved less than 0.5 of its own ATR,
                                         coin not falling (R1): buy the lag. Stop 2 ATR, 1.5R, 6 bars. Changed timeframe vs N6.
S21A s21_btc_lead_catchdown_4h     short mirror: BTC fell >= 1.5 ATRs, coin moved less than 0.5 ATR, coin not rising (R1).
L21B l21_four_month_high_4h        long  close above the prior 720-bar (120-day) high for the first time in 180 bars, coin
                                         rising (R1). Stop 3 ATR, 3R, 120 bars.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def sig_autocorr_continuation(d, p, ctx):
    r = d.c.pct_change()
    ac = r.rolling(p['n'], min_periods=p['n']).corr(r.shift()).shift()
    mv = d.c - d.c.shift()
    big = mv.abs() >= p['move_atr'] * d.atr.shift()
    ok = (ac >= p['ac']) & big
    return dict(le=S.X(ok & (mv > 0)), se=S.X(ok & (mv < 0)), lx=_z(d), sx=_z(d))


def _btc_move(d):
    f = L1._BTC['f']
    bi = S.indicators(f.reset_index(drop=True))
    idx = pd.to_datetime(d.t)
    bc = pd.Series(bi.c.values, index=pd.to_datetime(bi.t))
    ba = pd.Series(bi.atr.values, index=pd.to_datetime(bi.t))
    m = ((bc - bc.shift()) / ba.shift()).reindex(idx)
    return pd.Series(m.values, index=d.index)


def sig_btc_lead_catchup(d, p, ctx):
    cr = L1.coin_regime(d)
    own = (d.c - d.c.shift()) / d.atr.shift()
    le = (_btc_move(d) >= p['btc_atr']) & (own.abs() < p['own_atr']) & np.isin(cr, (0.0, 1.0))
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_btc_lead_catchdown(d, p, ctx):
    cr = L1.coin_regime(d)
    own = (d.c - d.c.shift()) / d.atr.shift()
    se = (_btc_move(d) <= -p['btc_atr']) & (own.abs() < p['own_atr']) & np.isin(cr, (0.0, -1.0))
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=_z(d))


def sig_four_month_high(d, p, ctx):
    cr = L1.coin_regime(d)
    hi = d.h.rolling(p['n'], min_periods=p['n']).max().shift()
    above = (d.c > hi).astype(float)
    fresh = above.shift().rolling(p['gap'], min_periods=p['gap']).sum() == 0
    return dict(le=S.X((above == 1) & fresh & (cr == 1)), se=_z(d), lx=_z(d), sx=_z(d))


LEAD = dict(btc_atr=1.5, own_atr=0.5)
CANDIDATES = {
    'r21_autocorr_continuation_4h': dict(
        name='[RESEARCH] R21A Return-autocorrelation continuation L/S 4h', style='momentum', fn=sig_autocorr_continuation,
        sides='both', tf='4h', research_only=True, params=dict(n=42, ac=0.1, move_atr=1.0),
        mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=12)),
    'l21_btc_lead_catchup_4h': dict(
        name='[RESEARCH] L21A BTC-lead catch-up long 4h', style='momentum', fn=sig_btc_lead_catchup,
        sides='long', tf='4h', research_only=True, params=dict(LEAD), mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=6)),
    's21_btc_lead_catchdown_4h': dict(
        name='[RESEARCH] S21A BTC-lead catch-down short 4h', style='momentum', fn=sig_btc_lead_catchdown,
        sides='short', tf='4h', research_only=True, params=dict(LEAD), mgmt=dict(stop_atr=2.0, tp_r=1.5, max_bars=6)),
    'l21_four_month_high_4h': dict(
        name='[RESEARCH] L21B First 120-day high in 30 days long 4h', style='trend', fn=sig_four_month_high,
        sides='long', tf='4h', research_only=True, params=dict(n=720, gap=180), mgmt=dict(stop_atr=3.0, tp_r=3.0, max_bars=120)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
