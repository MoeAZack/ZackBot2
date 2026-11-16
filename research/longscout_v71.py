"""LONGSCOUT-V71: two new research-only 4h candidates (Claude Projects, 2026-11-15).

FROZEN before any backtest outcome of these rules, on any coin. Parameters fixed a priori; none tuned. Default-OFF,
research_only. Regime R1 and causal helpers unchanged from longscout_v1.py; day stamps compared with the unit-safe
longscout_v26 helper. New family: the relative-strength line LEADING price (coin/BTC ratio makes a new 50-day closing
extreme while the coin's own close has not). Earlier BTC-relative rules (V8, V20, V40) used ratio trend, dips or 90-day
momentum, not a lead/lag divergence.

L71A l71_rs_leads_high_4h  long  days j-50..j present for coin and BTC. Ratio R = coin close / BTC close. R_j above every R of days
                                 j-50..j-1, while the coin's close j is below the highest coin close of days j-50..j-1; and day
                                 j-1 did not meet both (first day). Coin rising (R1). First 4h bar of the next day (modeled fill
                                 04:00 UTC). Stop 2.5 ATR, 2R, 30 bars.
S71A s71_rs_leads_low_4h   short mirror: R_j below every R of days j-50..j-1 while the coin's close is above its 50-day lowest close;
                                 first day; coin falling (R1). Same management.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v33 as L33

set_btc = L1.set_btc


def _z(d):
    return pd.Series(False, index=d.index)


def _lead_v(D, J, p, up):
    n = p['n']
    R = J.c / J.b
    if up:
        ok = (R > R.shift().rolling(n, min_periods=n).max()) & (J.c < J.c.shift().rolling(n, min_periods=n).max())
    else:
        ok = (R < R.shift().rolling(n, min_periods=n).min()) & (J.c > J.c.shift().rolling(n, min_periods=n).min())
    ok = ok.fillna(False).astype(bool) & L27._consec(D, n).astype(bool)
    return ok & ~ok.shift(fill_value=False)


def _lead(d, p, up):
    D, J = L33._joint(d)
    return L27._next_day(_lead_v(D, J, p, up), D, d)


def sig_high(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=S.X(pd.Series(_lead(d, p, True) & (cr == 1), index=d.index)), se=_z(d), lx=_z(d), sx=_z(d))


def sig_low(d, p, ctx):
    cr = L1.coin_regime(d)
    return dict(le=_z(d), se=S.X(pd.Series(_lead(d, p, False) & (cr == -1), index=d.index)), lx=_z(d), sx=_z(d))


MG = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l71_rs_leads_high_4h': dict(
        name='[RESEARCH] L71A Coin/BTC ratio 50-day high before price long 4h', style='momentum', fn=sig_high,
        sides='long', tf='4h', research_only=True, params=dict(n=50), mgmt=dict(MG)),
    's71_rs_leads_low_4h': dict(
        name='[RESEARCH] S71A Coin/BTC ratio 50-day low before price short 4h', style='momentum', fn=sig_low,
        sides='short', tf='4h', research_only=True, params=dict(n=50), mgmt=dict(MG)),
}


def register():
    for k, v in CANDIDATES.items():
        S.STRATEGIES[k] = dict(v, desc=v['name'])
