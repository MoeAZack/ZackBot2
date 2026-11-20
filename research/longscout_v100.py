"""LONGSCOUT V100 prospective OHLCV families from the pre-V99 scout shortlist.

L100 downside-to-next-range leverage asymmetry: daily r=log(C/Cprev),w=log(H/L).
rho=Corr(max(-r[t-1],0),w[t]) over t=j-59..j (60pairs). First cross from<.30
to>=.30, current r<0;63 consecutive complete days, own R1 rising/sideways.
This tests volatility RESPONSE to prior downside, not same-day semivariance.

R100 distribution migration range: on complete day-end20-open4h bar i, use
24 recent logreturns r[i-23..i] and preceding120 r[i-143..i-24],145 consecutive
4h closes. Quantiles at(k-.5)/24,k1..24, linear interpolation. W=mean absolute
quantile difference divided by populationSD of prior120,SD>0. W>=.75 and median
recent<=-.5SD ->long; >=+.5SD ->short. First FULL condition per direction on
successive daily decisions, coin R1 sideways. This compares distributions,
not rank motifs, one moment, or an exact optimal-transport estimator claim.

Both next-day00 signal/modeled04 fill,2.5ATR/2R/30bars, research-only/defaultOFF.
Constants from a prospective untested proposal, no grid/holdout/near-miss tuning.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H

set_btc=L1.set_btc


def leverage_flags(D):
    r=np.log(D.c/D.c.shift());w=np.log(D.h/D.l)
    x=(-r.shift()).clip(lower=0)
    rho=x.rolling(60,min_periods=60).corr(w)
    # pandas rolling correlation can emit inf for a nonzero constant series.
    # A true correlation requires variation in BOTH finite input samples.
    variable=(x.rolling(60,min_periods=60).max()>x.rolling(60,min_periods=60).min())&(w.rolling(60,min_periods=60).max()>w.rolling(60,min_periods=60).min())
    rho=rho.where(variable&np.isfinite(rho))
    return ((rho>=.30)&(rho.shift()<.30)&(r<0)&L27._consec(D,62)).fillna(False)


def distribution_flags(d):
    D=H.daily(d);t=pd.to_datetime(d.t);r=np.log(d.c/d.c.shift()).to_numpy()
    continuous=t.diff().eq(pd.Timedelta(hours=4)).rolling(144,min_periods=144).sum().eq(144).to_numpy()
    lower=pd.Series(False,index=D.index);upper=lower.copy();score=pd.Series(np.nan,index=D.index)
    probabilities=(np.arange(1,25)-.5)/24
    for i in np.flatnonzero((t.dt.hour.eq(20).to_numpy())&continuous):
        day=t.iloc[i].normalize()
        if day not in D.index:continue
        recent=r[i-23:i+1];base=r[i-143:i-23]
        if len(recent)!=24 or len(base)!=120 or not np.isfinite(np.r_[recent,base]).all():continue
        sd=float(np.std(base,ddof=0))
        if not sd>0:continue
        value=float(np.mean(np.abs(np.quantile(recent,probabilities,method='linear')-np.quantile(base,probabilities,method='linear')))/sd)
        med=float(np.median(recent));score.loc[day]=value
        lower.loc[day]=value>=.75 and med<=-.5*sd
        upper.loc[day]=value>=.75 and med>=.5*sd
    ready=L27._consec(D,1)
    return D,dict(long=(lower&~lower.shift(fill_value=False)&ready).fillna(False),
                  short=(upper&~upper.shift(fill_value=False)&ready).fillna(False)),score


def empty(d):return {k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}


def sig_leverage(d,p,ctx):
    D=H.daily(d);out=empty(d)
    out['le']=S.X(pd.Series(L27._next_day(leverage_flags(D),D,d)&np.isin(L1.coin_regime(d),(0.,1.)),index=d.index))
    return out


def sig_distribution(d,p,ctx):
    D,flags,_=distribution_flags(d);out=empty(d);cr=L1.coin_regime(d)
    for side,key in [('le','long'),('se','short')]:out[side]=S.X(pd.Series(L27._next_day(flags[key],D,d)&(cr==0),index=d.index))
    return out


CANDIDATES={
 'l100_downside_range_response_4h':dict(name='[RESEARCH] L100 lagged downside-range leverage response',style='reversal',fn=sig_leverage,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
 'r100_distribution_migration_4h':dict(name='[RESEARCH] R100 quantile distribution migration range',style='range',fn=sig_distribution,sides='both',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
}


def register():
    for k,v in CANDIDATES.items():S.STRATEGIES[k]=dict(v,desc=v['name'])
