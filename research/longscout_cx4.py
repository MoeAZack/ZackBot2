"""CX4: lagged return-to-volatility coupling (new family).

For completed daily close returns r_t=log(c_t/c_{t-1}),
rho_j=Pearson correlation of r_{t-1} with abs(r_t), t=j-59..j.
Long: rho crosses above +.30 and c_j>c_{j-20}; short: rho crosses
below -.30 and c_j<c_{j-20}. Matching R1 required, next-day first
4h signal. Require 63 consecutive closes for current/prior correlations.
Distinct from V85 contemporaneous RETURN-VOLUME correlation, V21 linear
return autocorrelation, V84 marginal semivariance, and CX1 ordinal entropy.
Tests a nonlinear lagged coupling, not a marginal volatility level.
Fixed 2.5 ATR stop, 2R target, 30-bar cap. Research only, frozen constants.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc=L1.set_btc
IP=dict(n=60,threshold=0.3,ret=20)
MG=dict(stop_atr=2.5,tp_r=2.0,max_bars=30)


def coupling(D,p):
    r=np.log(D.c/D.c.shift())
    return r.shift().rolling(p['n'],min_periods=p['n']).corr(r.abs())


def daily_hit(D,p,up):
    rho=coupling(D,p)
    if up: hit=(rho>p['threshold']) & (rho.shift()<=p['threshold']) & (D.c>D.c.shift(p['ret']))
    else: hit=(rho<-p['threshold']) & (rho.shift()>=-p['threshold']) & (D.c<D.c.shift(p['ret']))
    return (hit & L27._consec(D,p['n']+2)).fillna(False)


def signal(d,p,up):
    D=L27._days_v(d)
    hit=L27._next_day(daily_hit(D,p,up),D,d)
    hit=pd.Series(hit & (L1.coin_regime(d)==(1 if up else -1)),index=d.index)
    z=pd.Series(False,index=d.index)
    return dict(le=S.X(hit) if up else z,se=z if up else S.X(hit),lx=z,sx=z)


def sig_long(d,p,ctx): return signal(d,p,True)
def sig_short(d,p,ctx): return signal(d,p,False)
CANDIDATES={
    'cx007_volatility_coupling_long_4h':dict(name='[RESEARCH] CX007 lagged return-volatility coupling long',style='momentum',fn=sig_long,sides='long',tf='4h',research_only=True,params=dict(IP),mgmt=dict(MG)),
    'cx008_volatility_coupling_short_4h':dict(name='[RESEARCH] CX008 lagged return-volatility coupling short',style='momentum',fn=sig_short,sides='short',tf='4h',research_only=True,params=dict(IP),mgmt=dict(MG)),
}
def register():
    for k,v in CANDIDATES.items(): S.STRATEGIES[k]=dict(v,desc=v['name'])
