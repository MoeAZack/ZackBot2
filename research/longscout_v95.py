"""LONGSCOUT V95 draft: lagged volume information and weekly peer reversal.

L95 daily r_t=log(C_t/C_t-1), u_t=log(V_t/median(V_t-20..t-1)). Correlation
over30pairs(u_t-1,r_t) crosses from<.3 to>=.3, current r>0;52 consecutive
completed days, coin R1 rising/sideways. Tests predictive volume timing rather
than V85 contemporaneous correlation.2.5ATR/2R/30bars.
R95 weekly cross-sectional rebalancing: on Sunday completed day, own7-day
return below eligible peers20th percentile ->long, above80th ->short; coin
R1 sideways, BTC30-day return positive. Eligible peers same31day/$10m prior30
quote-volume rule, at least50; linear percentiles and strict comparisons.
Monday00 signal/04 fill, Sunday20-openbar close exit,2.5ATR/2R/42bar cap.
This is contrarian peer rebalancing, not top-ranked momentum rotation. New
combined factor hypothesis; shared calendar is consumed, no parameter tuning.

Research-only/defaultOFF. No outcome inspected before this draft.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H

set_btc=L1.set_btc
_WEEKLY=None


def lead_lag(D):
    r=np.log(D.c/D.c.shift())
    u=np.log(D.v/D.v.shift().rolling(20,min_periods=20).median())
    rho=u.shift().rolling(30,min_periods=30).corr(r)
    return ((rho>=.3)&(rho.shift()<.3)&(r>0)&L27._consec(D,51)).fillna(False)


def peer_weekly(panel):
    returns={}
    for sym,d in sorted(panel.items()):
        if sym=='BTCUSDT':continue
        D=H.daily(d)
        ready=L27._consec(D,30)&(D.qv.shift().rolling(30,min_periods=30).median()>=1e7)
        returns[sym]=(D.c/D.c.shift(7)-1).where(ready)
    R=pd.DataFrame(returns)
    F=pd.DataFrame(dict(n=R.notna().sum(axis=1),lo=R.quantile(.2,axis=1),hi=R.quantile(.8,axis=1)))
    B=H.daily(panel['BTCUSDT'])
    F['btc_up']=(B.c/B.c.shift(30)>1).where(L27._consec(B,30)).reindex(F.index)
    return F


def set_panel(panel):
    global _WEEKLY
    _WEEKLY=peer_weekly(panel)


def weekly_flags(D,F):
    F=F.reindex(D.index)
    ready=L27._consec(D,30)&(D.qv.shift().rolling(30,min_periods=30).median()>=1e7)&(F.n>=50)&F.btc_up.eq(True)&(D.index.dayofweek==6)
    r=D.c/D.c.shift(7)-1
    return dict(long=(ready&(r<F.lo)).fillna(False),short=(ready&(r>F.hi)).fillna(False))


def empty(d):return {k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}


def sig_lead_lag(d,p,ctx):
    D=H.daily(d);out=empty(d)
    out['le']=S.X(pd.Series(L27._next_day(lead_lag(D),D,d)&np.isin(L1.coin_regime(d),(0.,1.)),index=d.index))
    return out


def sig_weekly(d,p,ctx):
    if _WEEKLY is None:raise RuntimeError('set_panel required')
    D=H.daily(d);flags=weekly_flags(D,_WEEKLY);out=empty(d);cr=L1.coin_regime(d)
    for side,key in [('le','long'),('se','short')]:out[side]=S.X(pd.Series(L27._next_day(flags[key],D,d)&(cr==0),index=d.index))
    t=pd.to_datetime(d.t)
    out['lx']=out['sx']=t.dt.dayofweek.eq(6)&t.dt.hour.eq(20)
    return out


CANDIDATES={
 'l95_volume_leads_return_4h':dict(name='[RESEARCH] L95 lagged-volume predictive correlation long',style='momentum',fn=sig_lead_lag,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
 'r95_weekly_peer_rebalancing_4h':dict(name='[RESEARCH] R95 weekly cross-sectional contrarian range',style='range',fn=sig_weekly,sides='both',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=42)),
}


def register():
    for k,v in CANDIDATES.items():S.STRATEGIES[k]=dict(v,desc=v['name'])
