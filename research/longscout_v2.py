"""LONGSCOUT-V2: six new research-only 4h / daily candidates, long and regime-switching first (Claude Projects, 2026-11-14).

FROZEN before any backtest outcome of these rules, on any coin. Every parameter was fixed a priori; none was tuned.
Default-OFF, research_only, registered only into this process's strategies.STRATEGIES copy. No hourly rule.
Regime R1 and the causal daily helpers are reused unchanged from research/longscout_v1.py (frozen at a4e3b759).

L2A l2_crash_base_reclaim_1d    long  coin fell >= 60% from its 180-day high at some point in the last 60 days, then
                                      makes its first close above the daily EMA50 after 30+ days below it; BTC not
                                      falling. A base after a capitulation, not a breakout of a healthy trend.
L2B l2_btc_flush_leader_1d      long  BTC is rising (R1) but has a down day of >= 2 BTC daily ATRs; buy coins that are
                                      themselves rising (R1) at that close. A market-wide shakeout inside an uptrend.
L2C l2_weekly_rs_breakout_1d    long  on the week's last daily close (Sunday UTC), the weekly close breaks the prior
                                      12 weekly closes and the coin's 84-day return beats BTC's; exit when a weekly
                                      close falls below the prior 4 weekly closes.
R2A r2_regime_donchian55_1d     both  55-day breakout long only while the coin is rising (R1), 55-day breakdown short only
                                      while it is falling; exit on the opposite 20-day extreme. Changed hypothesis vs
                                      CP-N3 (20/10, ungated) and LC1 (long only, BTC+coin gate).
R2B r2_alt_btc_ratio_trend_1d   both  the coin/BTC price ratio (daily) closes above its prior 20-day ratio high while
                                      the ratio is above its rising EMA50 -> long; the mirror -> short; exit when the
                                      ratio closes back through its EMA50. Changed hypothesis vs CP-X2 (4h, ungated).
S2A s2_high_rollover_short_4h   short the coin printed its 30-day high within the last 10 days, then the 4h EMA20 crosses
                                      below the EMA50 with the close below the EMA50; exit when EMA20 crosses back above.
                                      Written after seeing that exp_trend's shorts earn most after rising regimes on the
                                      31 coins, so its fresh-coin result is the test that counts.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _btc_on(d):
    """BTC daily frame aligned to the coin's daily rows by date (NaN where BTC has no row)."""
    f = L1._BTC['f']
    return f.set_index(pd.to_datetime(f.t)).reindex(pd.to_datetime(d.t))


def sig_crash_base_reclaim(d, p, ctx):
    br = L1.btc_regime(d)
    dd = d.c / d.c.rolling(p['peak_n'], min_periods=p['peak_n']).max() - 1
    crashed = dd.rolling(p['look'], min_periods=1).min() <= -p['crash']
    below = (d.c < d.e50).astype(float).rolling(p['below_n'], min_periods=p['below_n']).sum().shift() == p['below_n']
    le = S.xup(d.c, d.e50) & below & crashed & (br >= 0)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_btc_flush_leader(d, p, ctx):
    cr, br = L1.coin_regime(d), L1.btc_regime(d)
    b = _btc_on(d)
    bi = S.indicators(L1._BTC['f'].reset_index(drop=True))
    batr = pd.Series(bi.atr.values, index=pd.to_datetime(bi.t)).reindex(pd.to_datetime(d.t)).values
    drop = (b.c.values - b.c.shift().values) <= -p['flush_atr'] * pd.Series(batr).shift().values
    le = pd.Series(drop, index=d.index) & (br == 1) & (cr == 1)
    return dict(le=S.X(le), se=_z(d), lx=_z(d), sx=_z(d))


def sig_weekly_rs_breakout(d, p, ctx):
    t = pd.to_datetime(d.t)
    wk_end = (t.dt.weekday == 6).values
    wc = d.c.where(wk_end)
    wser = wc.dropna()
    hi = wser.rolling(p['weeks']).max().shift()
    lo = wser.rolling(p['exit_weeks']).min().shift()
    b = _btc_on(d)
    rs = (d.c / d.c.shift(p['rs_days']) - 1) > (b.c.values / b.c.shift(p['rs_days']).values - 1)
    le = pd.Series(False, index=d.index); lx = pd.Series(False, index=d.index)
    le.loc[wser.index] = (wser > hi).values
    lx.loc[wser.index] = (wser < lo).values
    return dict(le=S.X(le & rs), se=_z(d), lx=S.X(lx), sx=_z(d))


def sig_regime_donchian55(d, p, ctx):
    cr = L1.coin_regime(d)
    le = S.xup(d.c, d.hh55) & (cr == 1)
    se = S.xup(-d.c, -d.ll55) & (cr == -1)
    return dict(le=S.X(le), se=S.X(se), lx=S.X(d.c < d.ll20), sx=S.X(d.c > d.hh20))


def sig_alt_btc_ratio_trend(d, p, ctx):
    b = _btc_on(d)
    r = pd.Series(d.c.values / b.c.values, index=d.index)
    e = S.ema(r.ffill(), p['ema'])
    up_e = e > e.shift(p['slope'])
    hi = r.rolling(p['look']).max().shift(); lo = r.rolling(p['look']).min().shift()
    le = S.xup(r, hi) & (r > e) & up_e
    se = S.xup(-r, -lo) & (r < e) & ~up_e
    return dict(le=S.X(le & ~se), se=S.X(se & ~le), lx=S.X(r < e), sx=S.X(r > e))


def sig_high_rollover_short(d, p, ctx):
    recent_high = d.h.rolling(p['recent']).max() >= d.h.rolling(p['high_n']).max()
    se = recent_high & S.xup(d.e50, d.e20) & (d.c < d.e50)
    return dict(le=_z(d), se=S.X(se), lx=_z(d), sx=S.X(S.xup(d.e20, d.e50)))


CANDIDATES = {
    'l2_crash_base_reclaim_1d': dict(
        name='[RESEARCH] L2A Crash-base EMA50 reclaim long daily', style='reversal', fn=sig_crash_base_reclaim,
        sides='long', tf='1d', research_only=True, params=dict(peak_n=180, look=60, crash=0.60, below_n=30),
        mgmt=dict(stop_atr=2.5, tp_r=4.0, max_bars=40)),
    'l2_btc_flush_leader_1d': dict(
        name='[RESEARCH] L2B BTC-flush leader buy long daily', style='pullback', fn=sig_btc_flush_leader,
        sides='long', tf='1d', research_only=True, params=dict(flush_atr=2.0),
        mgmt=dict(stop_atr=2.0, tp_r=2.0, max_bars=10)),
    'l2_weekly_rs_breakout_1d': dict(
        name='[RESEARCH] L2C Weekly relative-strength breakout long daily', style='trend', fn=sig_weekly_rs_breakout,
        sides='long', tf='1d', research_only=True, params=dict(weeks=12, exit_weeks=4, rs_days=84),
        mgmt=dict(stop_atr=3.0, max_bars=120)),
    'r2_regime_donchian55_1d': dict(
        name='[RESEARCH] R2A Regime-gated Donchian 55/20 L/S daily', style='trend', fn=sig_regime_donchian55,
        sides='both', tf='1d', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, max_bars=120)),
    'r2_alt_btc_ratio_trend_1d': dict(
        name='[RESEARCH] R2B Alt/BTC ratio trend L/S daily', style='trend', fn=sig_alt_btc_ratio_trend,
        sides='both', tf='1d', research_only=True, params=dict(ema=50, slope=10, look=20),
        mgmt=dict(stop_atr=2.5, max_bars=40)),
    's2_high_rollover_short_4h': dict(
        name='[RESEARCH] S2A 30-day-high rollover short 4h', style='trend', fn=sig_high_rollover_short,
        sides='short', tf='4h', research_only=True, params=dict(recent=60, high_n=180),
        mgmt=dict(stop_atr=2.5, tp_r=3.0, max_bars=60)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
