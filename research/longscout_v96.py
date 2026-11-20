"""LONGSCOUT V96 draft: sequential evidence and ordinal oscillation motifs.

L96 truncated CUSUM: r_j=log(C_j/C_j-1), z_j=(r_j-prior60 mean)/prior60
populationSD. G_j=max(0,max_{k=1..20}sum_{i=j-k+1..j}(z_i-.5)). First cross
from G<5 to>=5,82 complete consecutive daily closes; R1 rising/sideways.
Sequential evidence, rather than V90 isolated shock or regression slope.
R96 ordinal range: over60 daily closes classify58 consecutive triples by one
of6 STRICT ascending-rank permutations; ties skip, >=50 valid triples.
H=-sum(p log p)/log6; A=fraction with middle close above/below both neighbors.
First day H<=.90 AND A>=.75 (full condition), current close falling ->long,
rising ->short;61 consecutive complete days, coin R1 sideways. Nonlinear,
scale-free motif distribution rather than autocorrelation/variance ratio.

Both next-day00 signal/modeled04 fill;2.5ATR/2R/30bars, research-only OFF.
All constants fixed before any market outcome, no parameter search.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H

set_btc=L1.set_btc


def cusum(D):
    r=np.log(D.c/D.c.shift())
    mean=r.shift().rolling(60,min_periods=60).mean()
    sd=r.shift().rolling(60,min_periods=60).std(ddof=0)
    z=(r-mean)/sd.where(sd>0)
    score=(z-.5).rolling(20,min_periods=20).apply(lambda x:max(0.,float(np.cumsum(x[::-1]).max())),raw=True)
    return ((score>=5)&(score.shift()<5)&L27._consec(D,81)).fillna(False)


def ordinal_statistics(D):
    a,b,c=D.c.shift(2),D.c.shift(),D.c
    patterns=[(a<b)&(b<c),(a<c)&(c<b),(b<a)&(a<c),(b<c)&(c<a),(c<a)&(a<b),(c<b)&(b<a)]
    counts=pd.concat([p.astype(float).rolling(58,min_periods=58).sum() for p in patterns],axis=1)
    valid=counts.sum(axis=1,min_count=6)
    probabilities=counts.div(valid.replace(0,np.nan),axis=0)
    entropy=-(probabilities*np.log(probabilities.where(probabilities>0))).sum(axis=1,min_count=1)/np.log(6.)
    alternate=counts.iloc[:,[1,2,3,4]].sum(axis=1,min_count=4)/valid.replace(0,np.nan)
    return pd.DataFrame(dict(entropy=entropy,alternate=alternate,valid=valid),index=D.index)


def ordinal(D):
    st=ordinal_statistics(D)
    hit=(st.entropy<=.90)&(st.alternate>=.75)&(st.valid>=50)&L27._consec(D,60)
    event=hit&~hit.shift(fill_value=False)
    return dict(long=(event&(D.c<D.c.shift())).fillna(False),short=(event&(D.c>D.c.shift())).fillna(False))


def entries(d,range_rule):
    D=H.daily(d);cr=L1.coin_regime(d)
    flags=ordinal(D) if range_rule else dict(long=cusum(D))
    gate=cr==0 if range_rule else np.isin(cr,(0.,1.))
    out={k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}
    for side,key in [('le','long'),('se','short')]:
        if key in flags:out[side]=S.X(pd.Series(L27._next_day(flags[key],D,d)&gate,index=d.index))
    return out


def sig_cusum(d,p,ctx):return entries(d,False)
def sig_ordinal(d,p,ctx):return entries(d,True)


CANDIDATES={
 'l96_sequential_cusum_4h':dict(name='[RESEARCH] L96 truncated sequential CUSUM long',style='momentum',fn=sig_cusum,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
 'r96_ordinal_oscillation_4h':dict(name='[RESEARCH] R96 ordinal motif oscillation range',style='range',fn=sig_ordinal,sides='both',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
}


def register():
    for k,v in CANDIDATES.items():S.STRATEGIES[k]=dict(v,desc=v['name'])
