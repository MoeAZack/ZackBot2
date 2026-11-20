"""Synthetic independent checks for native-daily V101; no acquired data."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v101 as M


def panel(n=145):
    rng=np.random.default_rng(121)
    common=rng.normal(.0003,.015,n)
    residual=np.zeros(n)
    for i in range(1,n): residual[i]=.85*residual[i-1]+rng.normal(0,.002)
    own=np.cumsum(common)+residual
    dates=pd.date_range('2023-01-01',periods=n,freq='D')
    def make(logs):
        c=100*np.exp(logs)
        return pd.DataFrame({'t':dates,'c':c,'qv':2e7})
    out={'TARGET':make(own),'BTCUSDT':make(np.zeros(n))}
    for i in range(50): out[f'P{i:02}']=make(np.cumsum(common)+i*.001)
    return out


def ols_reference(own,peer):
    x=list(peer);y=list(own);xm=sum(x)/120;ym=sum(y)/120
    beta=sum((a-xm)*(b-ym) for a,b in zip(x,y))/sum((a-xm)**2 for a in x)
    alpha=ym-beta*xm; res=[b-alpha-beta*a for a,b in zip(x,y)]
    sd=(sum(a*a for a in res)/120)**.5
    lag=res[:-1];nxt=res[1:];lm=sum(lag)/119;nm=sum(nxt)/119
    phi=sum((a-lm)*(b-nm) for a,b in zip(lag,nxt))/sum((a-lm)**2 for a in lag)
    return alpha,beta,sd,phi


def test_ou_independent_reference_positive_and_undefined():
    p=panel(); y=np.log(p['TARGET'].c.to_numpy())[1:121];x=np.log(p['P00'].c.to_numpy())[1:121]
    a,b,sd,phi=ols_reference(y,x); assert 1 <= -np.log(2)/np.log(phi) <= 10
    assert M.model_ou(y,x)==pytest.approx((a,b,sd),rel=1e-10,abs=1e-10)
    assert M.model_ou(y,np.ones(120)) is None
    assert M.model_ou(-x,x) is None
    assert M.model_ou(x,x) is None


def test_reference_tail_and_ou_prior_only_prefix_and_lexical(monkeypatch):
    p=panel(); n=len(p['TARGET']); logs={s:np.log(d.c.to_numpy()) for s,d in p.items() if s!='BTCUSDT'}
    # A market drop at j, with the target flat or rising, makes a positive tail fixture.
    j=130
    for s,d in p.items():
        if s not in ('TARGET','BTCUSDT'): d.loc[j:,'c']*=np.exp(-.04)
    p['TARGET'].loc[j:,'c']*=np.exp(.03)
    M.set_panel(p); got=M.peer_statistics('TARGET')
    logs={s:np.log(d.c.to_numpy()) for s,d in p.items() if s!='BTCUSDT'}
    ret={s:np.diff(v,prepend=np.nan) for s,v in logs.items()}
    # Scalar, sorted-order implementation of the prior-window tail fraction.
    y=ret['TARGET'][j-120:j]; threshold=sorted(y)[11]*.1+sorted(y)[12]*.9
    tail=[k for k,v in enumerate(y) if v<threshold]
    peers=sorted(s for s in logs if s!='TARGET')
    flags=[]
    for k in tail:
        hits=[]
        for s in peers:
            vals=sorted(ret[s][j-120:j]); q=vals[11]*.1+vals[12]*.9
            hits.append(ret[s][j-120+k]<q)
        flags.extend(hits)
    expected=(len(tail)>=8 and sum(flags)/len(flags)<=.20 and np.median([ret[s][j] for s in peers])<=-.015 and ret['TARGET'][j]>=0)
    assert bool(got['tail'].iloc[j]) == bool(expected)
    # At a separate time, identical peer returns resolve to the lexical first peer.
    k=125; a,b,sd,phi=ols_reference(logs['TARGET'][k-120:k],logs['P00'][k-120:k])
    assert 1<=-np.log(2)/np.log(phi)<=10
    assert got.z.iloc[k]==pytest.approx((logs['TARGET'][k]-a-b*logs['P00'][k])/sd,abs=1e-9)
    # Future mutations do not change earlier outputs, including model exits.
    before=got.iloc[:j+1].copy()
    for d in p.values(): d.loc[j+1:,'c']*=1.7
    M.set_panel(p);pd.testing.assert_frame_equal(before,M.peer_statistics('TARGET').iloc[:j+1])
    short={s:d.iloc[:j+1].copy() for s,d in p.items()};M.set_panel(short)
    pd.testing.assert_frame_equal(before,M.peer_statistics('TARGET'))


def test_point_in_time_eligibility_readiness_gaps_and_btc():
    p=panel();M.set_panel(p)
    assert 'BTCUSDT' not in M._SYMBOLS
    assert not M._HISTORY.loc[M._DATES[120],'TARGET']
    assert M._HISTORY.loc[M._DATES[121],'TARGET']
    assert not M._ELIGIBLE.loc[M._DATES[29],'TARGET']
    assert M._ELIGIBLE.loc[M._DATES[30],'TARGET']
    # A current quote-volume shock does not affect eligibility until next day.
    p['TARGET'].loc[121:,'qv']=0
    M.set_panel(p); assert M._ELIGIBLE.loc[M._DATES[121],'TARGET']
    assert not M._ELIGIBLE.loc[M._DATES[137],'TARGET']
    p=panel();p['TARGET'].loc[110,'c']=np.nan;M.set_panel(p)
    assert not M._HISTORY.loc[M._DATES[130],'TARGET']
    assert not M.peer_statistics('TARGET').iloc[130][['tail','lower','upper']].any()
    # 49 other coins, even with BTC present, cannot satisfy 50-peer gate.
    p=panel();del p['P49'];M.set_panel(p)
    assert not M.peer_statistics('TARGET')[['tail','lower','upper']].to_numpy().any()
    assert M.peer_statistics('TARGET').z.isna().all()


def test_native_context_regime_edges_exits_and_default_off(monkeypatch):
    p=panel();M.set_panel(p);d=p['TARGET']; dates=M._DATES
    stat=pd.DataFrame({'tail':False,'lower':False,'upper':False,'z':np.nan},index=dates)
    stat.loc[dates[125:127],'tail']=True;stat.loc[dates[125:127],'lower']=True
    stat.loc[dates[128],'upper']=True;stat.loc[dates[129],'z']=.1;stat.loc[dates[130],'z']=-.1
    monkeypatch.setattr(M,'peer_statistics',lambda symbol: stat if symbol=='TARGET' else (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(np.zeros(len(c)), index=c.index))
    a=M.sig_tail(d,{}, {'research_symbol':'TARGET'}); b=M.sig_ou(d,{}, {'research_symbol':'TARGET'})
    assert list(np.flatnonzero(a['le']))==[125]
    assert list(np.flatnonzero(b['le']))==[125] and list(np.flatnonzero(b['se']))==[128]
    assert b['lx'].iloc[129] and b['sx'].iloc[130]
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(np.ones(len(c)), index=c.index))
    assert M.sig_tail(d,{}, {'research_symbol':'TARGET'})['le'].iloc[125]
    assert not M.sig_ou(d,{}, {'research_symbol':'TARGET'})['le'].any()
    with pytest.raises(ValueError):M.sig_tail(d,{}, {})
    with pytest.raises(ValueError):M.sig_tail(d,{}, {'research_symbol':'BTCUSDT'})
    bad=d.copy();bad.loc[1,'t']+=pd.Timedelta(hours=4)
    with pytest.raises(ValueError):M._native_daily(bad)
    assert 'longscout_v101' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for key,v in M.CANDIDATES.items():
        assert v['tf']=='1d' and v['research_only'] and key not in M.S.STRATEGIES



def test_tail_positive_disjoint_peer_crashes_and_prior_liquidity():
    n=123; dates=pd.date_range('2023-01-01',periods=n)
    def make(r):
        return pd.DataFrame({'t':dates,'c':100*np.exp(np.cumsum(r)), 'qv':2e7})
    own=np.full(n,.001);peer=np.full(n,.001)
    own[10:22]=-.04;peer[40:52]=-.04
    own[121]=.002;peer[121]=-.04
    p={'TARGET':make(own),'BTCUSDT':make(peer)}
    p.update({f'P{i:02}':make(peer) for i in range(50)})
    M.set_panel(p);stat=M.peer_statistics('TARGET')
    assert stat['tail'].iloc[121]
    # One qualifying peer removed at the decision time breaks the 50-other gate.
    p['P00'].loc[91:120,'qv']=0
    M.set_panel(p);assert not M.peer_statistics('TARGET')['tail'].iloc[121]


def test_exact_correlation_tie_chooses_lexical_peer(monkeypatch):
    p=panel(122);M.set_panel(p)
    # Make return vectors bit-identical while retaining distinguishable log levels.
    for s in (f'P{i:02}' for i in range(50)):
        M._R[s]=M._R['P00'].copy()
    chosen=[]
    def observe(own,peer):
        chosen.append(peer[0]);return None
    monkeypatch.setattr(M,'model_ou',observe)
    M.peer_statistics('TARGET')
    assert len(chosen)==1
    assert chosen[0]==M._LOG['P00'].iloc[1]


def test_real_ou_crossing_and_exit(monkeypatch):
    p=panel(125);p['TARGET'].loc[121:121,'c']*=np.exp(-.1)
    M.set_panel(p);stat=M.peer_statistics('TARGET')
    assert stat['z'].iloc[121] <= -2 and stat['lower'].iloc[121]
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(0.,index=c.index))
    sig=M.sig_ou(p['TARGET'],{}, {'research_symbol':'TARGET'})
    assert sig['le'].iloc[121]
    assert sig['lx'].iloc[122] == (np.isfinite(stat['z'].iloc[122]) and stat['z'].iloc[122]>=0)
