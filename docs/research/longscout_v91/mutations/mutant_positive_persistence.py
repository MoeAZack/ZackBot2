"""LONGSCOUT V91: two preregistered research-only hypotheses, default OFF.

L91: Rogers-Satchell-normalized positive daily impulse. RS_t =
log(H/O)*log(H/C) + log(L/O)*log(L/C). sigma_j is sqrt(mean RS over
the PRIOR 20 complete days). log(C_j/C_j-1) > 1.5*sigma_j and C_j>C_j-20.
Coin R1 rising or sideways. Related to earlier shock/range rules; this tests
a new volatility estimator, not a wholly independent economic thesis.

R91: anti-persistent range fade. rho_j = Corr(r_t,r_t-1), t=j-29..j,
r_t=log(C_t/C_t-1). Require rho<=-0.3 and coin R1 sideways. Long when
C_j < mean(C_j-10..j-1)-sd(C_j-10..j-1), short above mean+sd (sample sd).
First day of each FULL statistical condition (regime is applied afterward).
This is a changed hypothesis relative to V21 persistence and V45 band fades,
and is counted separately; no prior near-miss has been tuned.

Both: 33 consecutive completed UTC days required (conservative common
warm-up), signal on next day's first 4h bar; next-bar modeled fill.
Stop 2.5 ATR, target 2R, maximum 30 bars. No parameter search.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v17 as L17
import longscout_v27 as L27

set_btc = L1.set_btc


def daily_conditions(D, p):
    valid = ((D[['o', 'h', 'l', 'c']] > 0).all(axis=1)
             & (D.h >= D[['o', 'c', 'l']].max(axis=1))
             & (D.l <= D[['o', 'c', 'h']].min(axis=1)))
    rs = (np.log(D.h / D.o) * np.log(D.h / D.c)
          + np.log(D.l / D.o) * np.log(D.l / D.c)).where(valid)
    sigma = np.sqrt(rs.shift().rolling(p['rs_n'], min_periods=p['rs_n']).mean().clip(lower=0))
    r = np.log(D.c / D.c.shift()).where(valid & valid.shift(fill_value=False))
    long = (r > p['shock'] * sigma) & (sigma > 0) & (D.c > D.c.shift(p['rs_n']))
    rho = r.rolling(p['corr_n'], min_periods=p['corr_n']).corr(r.shift())
    prior = D.c.shift()
    mu = prior.rolling(p['band_n'], min_periods=p['band_n']).mean()
    sd = prior.rolling(p['band_n'], min_periods=p['band_n']).std(ddof=1)
    lo = (rho >= -p['rho']) & (sd > 0) & (D.c < mu - p['band_sd'] * sd)
    hi = (rho >= -p['rho']) & (sd > 0) & (D.c > mu + p['band_sd'] * sd)
    ready = L27._consec(D, 32) & valid.rolling(33, min_periods=33).min().eq(1)
    return dict(impulse=(long & ready).fillna(False),
                range_long=(lo & ~lo.shift(fill_value=False) & ready).fillna(False),
                range_short=(hi & ~hi.shift(fill_value=False) & ready).fillna(False))


def _entries(d, p, keys, allowed):
    D = L17._days(d)
    cond = daily_conditions(D, p)
    regime = L1.coin_regime(d)
    z = pd.Series(False, index=d.index)
    out = dict(le=z.copy(), se=z.copy(), lx=z.copy(), sx=z.copy())
    for side, key in keys:
        out[side] = S.X(pd.Series(L27._next_day(cond[key], D, d)
                                 & np.isin(regime, allowed), index=d.index))
    return out


def sig_impulse(d, p, ctx):
    return _entries(d, p, [('le', 'impulse')], (0., 1.))


def sig_range(d, p, ctx):
    return _entries(d, p, [('le', 'range_long'), ('se', 'range_short')], (0.,))


PARAMS = dict(rs_n=20, shock=1.5, corr_n=30, rho=-0.3, band_n=10, band_sd=1.0)
MGMT = dict(stop_atr=2.5, tp_r=2.0, max_bars=30)
CANDIDATES = {
    'l91_rs_impulse_4h': dict(name='[RESEARCH] L91 RS-normalized positive daily impulse',
                            style='momentum', fn=sig_impulse, sides='long', tf='4h',
                            research_only=True, params=dict(PARAMS), mgmt=dict(MGMT)),
    'r91_antipersistent_range_4h': dict(name='[RESEARCH] R91 anti-persistent daily range fade',
                                     style='range', fn=sig_range, sides='both', tf='4h',
                                     research_only=True, params=dict(PARAMS), mgmt=dict(MGMT)),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
