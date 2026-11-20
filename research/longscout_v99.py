"""LONGSCOUT V99 prospective OHLCV-only queue while V98 flow data is gated.

L99 peer-leader catch-up: fixed eligible peers exclude BTC and the target;
31 consecutive days/prior30 median qv>=$10m, >=50 currently eligible peers.
Fit corr(peer return[t-1],own return[t]) over exactly t=j-60..j-1, require all60
finite pairs and positive variances. Select five largest correlations>.30,
ties by lexical symbol. Current mean leader logreturn>=.02, own abs(logreturn)
<=.01; first full statistical condition, own R1 rising/sideways. Own64 complete
days required for current/prior fits; peer history completeness is per fit.
New causal peer-lead graph, not BTC-only trigger or current return rank.

R99 early-volume stress recovery: among6 complete chronological4h bars in j,
E=(qv00+qv04)/sum(qv00..20); all finite nonnegative,total>0. E>=.50 and E>=1.5
times prior20 median E, daily logreturn<=-.03; first full condition, coin R1
sideways.22 consecutive valid complete days. Half-volume/1.5x constants chosen
prospectively for a broad liquidity-timing event before any feature screen;
not a tuned variant of R94 or any prior outcome. No taker-flow inference.

Both next-day00 signal/modeled04 fill,2.5ATR/2R/30bars, research-only/defaultOFF.
V98 is a separate not-yet-executable data-dependent plan; no skipped trial result.
"""
import numpy as np
import pandas as pd
import strategies as S
import longscout_v1 as L1
import longscout_v27 as L27
import longscout_v92 as H

set_btc=L1.set_btc
_R=_E=_READY=None
_CACHE={}


def peer_daily(d):
    """Same completed-day close/quote inputs, without unused path geometry."""
    t=pd.to_datetime(d.t);day=t.dt.normalize()
    f=pd.DataFrame(dict(c=d.c.to_numpy(),qv=d.qv.to_numpy(),hour=t.dt.hour.to_numpy(),day=day.to_numpy()))
    g=f.groupby('day',sort=True)
    order_ok=(f.hour==g.cumcount()*4)&t.dt.minute.eq(0).to_numpy()&t.dt.second.eq(0).to_numpy()&t.dt.microsecond.eq(0).to_numpy()
    good=order_ok.groupby(f.day).all()&g.size().eq(6)
    return pd.DataFrame(dict(c=g.c.last(),qv=g.qv.sum(min_count=6)))[good]


def set_panel(panel):
    global _R,_E,_READY,_CACHE
    returns,eligible,ready={},{},{}
    for sym,d in sorted(panel.items()):
        if sym=='BTCUSDT':continue
        D=peer_daily(d)
        returns[sym]=np.log(D.c/D.c.shift()).where(L27._consec(D,1))
        eligible[sym]=L27._consec(D,30)&(D.qv.shift().rolling(30,min_periods=30).median()>=1e7)
        ready[sym]=L27._consec(D,63)
    _R=pd.DataFrame(returns)
    _E=pd.DataFrame(eligible).reindex(_R.index).fillna(False).astype(bool)
    _READY=pd.DataFrame(ready).reindex(_R.index).fillna(False).astype(bool)
    _CACHE={}


def peer_event(symbol):
    if _R is None or symbol not in _R:raise ValueError('explicit known peer symbol required')
    if symbol in _CACHE:return _CACHE[symbol]
    peers=sorted(s for s in _R if s!=symbol)
    own=_R[symbol]
    rho=_R[peers].shift(2).rolling(60,min_periods=60).corr(own.shift())
    values=rho.to_numpy();current=_R[peers].to_numpy();elig=_E[peers].to_numpy()
    count=elig.sum(axis=1);hit=np.zeros(len(_R),bool)
    for i in range(len(hit)):
        if not _READY[symbol].iloc[i] or not _E[symbol].iloc[i] or count[i]<50 or not np.isfinite(own.iloc[i]) or abs(own.iloc[i])>.01:continue
        candidates=np.flatnonzero(elig[i]&np.isfinite(values[i])&(values[i]>.30)&np.isfinite(current[i]))
        if len(candidates)<5:continue
        chosen=sorted(candidates,key=lambda k:(-values[i,k],peers[k]))[:5]
        hit[i]=float(np.mean(current[i,chosen]))>=.02
    raw=pd.Series(hit,index=_R.index)
    result=raw&~raw.shift(fill_value=False)
    _CACHE[symbol]=result
    return result


def early_volume(d):
    D=H.daily(d);t=pd.to_datetime(d.t)
    q=d.qv.where(np.isfinite(d.qv)&(d.qv>=0))
    total=q.groupby(t.dt.normalize()).sum(min_count=6).reindex(D.index)
    early=q.where(t.dt.hour.isin((0,4))).groupby(t.dt.normalize()).sum(min_count=2).reindex(D.index)
    E=early/total.where(total>0)
    valid=E.notna()&np.isfinite(E)&(E>=0)&(E<=1)
    ready=valid.rolling(22,min_periods=22).sum().eq(22)&L27._consec(D,21)
    hit=ready&(E>=.50)&(E>=1.5*E.shift().rolling(20,min_periods=20).median())&(np.log(D.c/D.c.shift())<=-.03)
    return D,(hit&~hit.shift(fill_value=False)).fillna(False),E


def empty(d):return {k:pd.Series(False,index=d.index) for k in ('le','se','lx','sx')}


def sig_peer(d,p,ctx):
    if not isinstance(ctx,dict) or not isinstance(ctx.get('research_symbol'),str):raise ValueError('research_symbol required')
    D=H.daily(d);flag=peer_event(ctx['research_symbol']).reindex(D.index,fill_value=False);out=empty(d)
    out['le']=S.X(pd.Series(L27._next_day(flag,D,d)&np.isin(L1.coin_regime(d),(0.,1.)),index=d.index))
    return out


def sig_early(d,p,ctx):
    D,flag,_=early_volume(d);out=empty(d)
    out['le']=S.X(pd.Series(L27._next_day(flag,D,d)&(L1.coin_regime(d)==0),index=d.index))
    return out


CANDIDATES={
 'l99_peer_leader_catchup_4h':dict(name='[RESEARCH] L99 causal peer leader catch-up',style='momentum',fn=sig_peer,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
 'r99_early_volume_stress_recovery_4h':dict(name='[RESEARCH] R99 front-loaded quote-volume stress recovery range',style='range',fn=sig_early,sides='long',tf='4h',research_only=True,params={},mgmt=dict(stop_atr=2.5,tp_r=2.,max_bars=30)),
}


def register():
    for k,v in CANDIDATES.items():S.STRATEGIES[k]=dict(v,desc=v['name'])
