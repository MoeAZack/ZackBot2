"""LONGSCOUT V97 draft: idiosyncratic flush and intraday participation proxy.

L97A market-model residual flush: OLS coin DAILY simple return on intercept
and BTC, using exactly prior60 paired returns; prior residual populationSD>0.
Cancellation residue<=64machine-epsilon times coin variance counts as zero.
Current residual<=-2SD with abs(BTC current return)<=1%; coin R1 rising/sideways,
first day full statistical condition.62 complete consecutive days. Different
from V88 residual-volatility regime and V93 systemic/downside-beta stress.
L97B intraday quote-volume/settlement disagreement: complete6 chronological4h
bars, Q=sum(qv_i*sign(C_i-O_i))/sum(qv_i), valid nonnegative qv,total>0.
Q>=.35 despite daily close/previous close-1<=-2%, first day full condition,
three consecutive completed days, coin R1 rising/sideways. Coarse BAR-DIRECTION
participation proxy, NOT taker flow or proven buyer-initiated trading. Distinct
from up/down DAY volume or contemporaneous daily return-volume correlation.

Both next-day00 signal/modeled04 fill,2.5ATR/2R/30bars, research-only OFF.
Thresholds fixed before any market outcome; no holdout tuning or parameter search.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H
import longscout_v93 as J93

set_btc=L1.set_btc


def residual_statistics(J):
    x,y=J.rb.shift(),J.rc.shift()
    mx=x.rolling(60,min_periods=60).mean();my=y.rolling(60,min_periods=60).mean()
    vx=x.rolling(60,min_periods=60).var(ddof=0)
    vy=y.rolling(60,min_periods=60).var(ddof=0)
    cov=x.rolling(60,min_periods=60).cov(y,ddof=0)
    beta=cov/vx.where(vx>0)
    alpha=my-beta*mx
    variance=vy-cov*cov/vx.where(vx>0)
    tolerance=64*np.finfo(float).eps*vy.abs()
    sd=np.sqrt(variance.mask(variance<=tolerance,0.).clip(lower=0))
    return pd.DataFrame(dict(beta=beta,alpha=alpha,sd=sd,residual=J.rc-alpha-beta*J.rb),index=J.index)


def residual_flags(D,J):
    st=residual_statistics(J)
    ready=J.rc.notna().rolling(61,min_periods=61).min().eq(1)&J.rb.notna().rolling(61,min_periods=61).min().eq(1)&L27._consec(D,61)
    hit=ready&(st.sd>0)&(st.residual<=-2*st.sd)&(J.rb.abs()<=.01)
    return (hit&~hit.shift(fill_value=False)).fillna(False)


def participation(d):
    D=H.daily(d)
    t=pd.to_datetime(d.t)
    q=d.qv.where(d.qv>=0) if 'qv' in d else pd.Series(np.nan,index=d.index)
    direction=np.sign(d.c-d.o)
    numerator=(q*direction).groupby(t.dt.normalize()).sum(min_count=6)
    denominator=q.groupby(t.dt.normalize()).sum(min_count=6)
    Q=(numerator/denominator.where(denominator>0)).reindex(D.index)
    hit=(Q>=.35)&(D.c/D.c.shift()-1<=-.02)&L27._consec(D,1)
    return D,(hit&~hit.shift(fill_value=False)&L27._consec(D,2)).fillna(False),Q


def empty(d):return {k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}


def sig_residual(d,p,ctx):
    D,J=J93.joint(d);out=empty(d)
    out['le']=S.X(pd.Series(L27._next_day(residual_flags(D,J),D,d)&np.isin(L1.coin_regime(d),(0.,1.)),index=d.index))
    return out


def sig_participation(d,p,ctx):
    D,flags,_=participation(d);out=empty(d)
    out['le']=S.X(pd.Series(L27._next_day(flags,D,d)&np.isin(L1.coin_regime(d),(0.,1.)),index=d.index))
    return out


CANDIDATES={
 'l97_quiet_btc_residual_flush_4h':dict(name='[RESEARCH] L97 market-model residual flush long',style='reversal',fn=sig_residual,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
 'l97_intraday_participation_disagreement_4h':dict(name='[RESEARCH] L97 quote-volume bar participation disagreement long',style='reversal',fn=sig_participation,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
}


def register():
    for k,v in CANDIDATES.items():S.STRATEGIES[k]=dict(v,desc=v['name'])
