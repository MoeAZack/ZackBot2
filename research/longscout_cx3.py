"""CX3: within-day realized-return ENERGY concentration (new family).

For six completed 4h bars of a UTC day, r_i=log(c_i/o_i),
Q=max(r_i^2)/sum(r_i^2); zero energy has Q=0. First Q>=.60 after
Q<.60 on the immediately previous complete calendar day. Go in the sign
of sum(r_i), only in matching R1. Next-day first 4h signal. Distinct from
V89 dollar-VOLUME concentration across 30 days, V47 session timing, V90
return-size/volume shock, and CX1 ordinal entropy. No volume/size threshold.
Fixed 2.5 ATR stop, 2R target, 30-bar cap. Research-only, a priori constants.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27

set_btc=L1.set_btc
IP=dict(concentration=0.6)
MG=dict(stop_atr=2.5,tp_r=2.0,max_bars=30)


def days(d):
    t=pd.to_datetime(d.t)
    r=np.log(d.c/d.o)
    g=pd.DataFrame(dict(day=t.dt.normalize(),r=r,e=r*r,h=d.h,l=d.l,c=d.c,v=d.v)).groupby('day')
    D=g.agg(h=('h','max'),l=('l','min'),c=('c','last'),v=('v','sum'),r=('r','sum'),energy=('e','sum'),peak=('e','max'))
    D=D[g.size()==6].copy()
    D['q']=D.peak/D.energy.replace(0,np.nan)
    D.loc[D.energy==0,'q']=0.0
    return D


def daily_hit(D,p,up):
    hit=(D.q>=p['concentration']) & (D.q.shift()<p['concentration'])
    return (hit & ((D.r>0) if up else (D.r<0)) & L27._consec(D,1)).fillna(False)


def signal(d,p,up):
    D=days(d)
    hit=L27._next_day(daily_hit(D,p,up),D,d)
    hit=pd.Series(hit & (L1.coin_regime(d)==(1 if up else -1)),index=d.index)
    z=pd.Series(False,index=d.index)
    return dict(le=S.X(hit) if up else z,se=z if up else S.X(hit),lx=z,sx=z)


def sig_long(d,p,ctx): return signal(d,p,True)
def sig_short(d,p,ctx): return signal(d,p,False)
CANDIDATES={
    'cx005_energy_concentration_long_4h':dict(name='[RESEARCH] CX005 intraday return-energy concentration long',style='momentum',fn=sig_long,sides='long',tf='4h',research_only=True,params=dict(IP),mgmt=dict(MG)),
    'cx006_energy_concentration_short_4h':dict(name='[RESEARCH] CX006 intraday return-energy concentration short',style='momentum',fn=sig_short,sides='short',tf='4h',research_only=True,params=dict(IP),mgmt=dict(MG)),
}
def register():
    for k,v in CANDIDATES.items(): S.STRATEGIES[k]=dict(v,desc=v['name'])
