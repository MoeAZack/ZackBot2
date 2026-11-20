"""Independent synthetic V99 causal, peer and session checks."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v99 as M


def bars(returns):
    prices=100*np.exp(np.cumsum(returns)); rows=[]
    for i,c in enumerate(prices):
        for h in range(0,24,4): rows.append((pd.Timestamp('2023-01-01')+pd.Timedelta(days=i,hours=h),c,c+1,c-1,c,1.,2e6))
    return pd.DataFrame(rows,columns=['t','o','h','l','c','v','qv'])


def peer_panel():
    n=78; rng=np.random.default_rng(99); x=rng.normal(0,.004,n); x[69]=.001
    own=np.r_[0.,x[:-1]]; own[70]=0.
    panel={'TARGET':bars(own),'BTCUSDT':bars(np.full(n,.04))}
    for i in range(51):
        r=x.copy() if i<6 else rng.normal(0,.005,n)
        if i<5: r[70]=.03
        elif i==5: r[70]=-.03
        panel[f'P{i:02}']=bars(r)
    return panel


@pytest.fixture(scope='module')
def peer_sample():
    return peer_panel()


def reference_at(panel,target,j):
    day=pd.Timestamp('2023-01-01')+pd.Timedelta(days=j)
    vals={}
    for sym,d in panel.items():
        if sym=='BTCUSDT':continue
        by={}
        for row in d.itertuples(index=False):
            by.setdefault(row.t.normalize(),[]).append(row)
        valid={dt:g for dt,g in by.items() if [r.t.hour for r in g]==[0,4,8,12,16,20]}
        rets={dt:np.log(g[-1].c/valid[dt-pd.Timedelta(days=1)][-1].c) for dt,g in valid.items() if dt-pd.Timedelta(days=1) in valid}
        elig=all(day-pd.Timedelta(days=k) in valid for k in range(31)) and np.median([sum(r.qv for r in valid[day-pd.Timedelta(days=k)]) for k in range(1,31)])>=1e7
        vals[sym]=(valid,rets,elig)
    own=vals[target][1]
    if not all(day-pd.Timedelta(days=k) in vals[target][0] for k in range(64)) or not vals[target][2]:return False,[]
    eligible=[s for s in vals if s!=target and vals[s][2]]
    if len(eligible)<50 or day not in own or abs(own[day])>.01:return False,[]
    ranked=[]
    for s in eligible:
        peer=vals[s][1]; xs=[]; ys=[]
        for k in range(60,0,-1):
            t=day-pd.Timedelta(days=k)
            if t not in own or t-pd.Timedelta(days=1) not in peer:break
            ys.append(own[t]); xs.append(peer[t-pd.Timedelta(days=1)])
        if len(xs)==60 and np.std(xs)>0 and np.std(ys)>0 and day in peer:
            rho=np.corrcoef(xs,ys)[0,1]
            if rho>.30:ranked.append((s,rho,peer[day]))
    ranked.sort(key=lambda x:(-x[1],x[0])); chosen=ranked[:5]
    return len(chosen)==5 and np.mean([x[2] for x in chosen])>=.02,chosen


def early_frame():
    d=bars(np.zeros(38));d['qv']=np.tile([2.,2.,8.,8.,8.,8.],38)
    for day,c in [(25,96.),(26,92.)]:
        ix=d.index[d.t.dt.normalize().eq(pd.Timestamp('2023-01-01')+pd.Timedelta(days=day))]
        d.loc[ix,'qv']=[9.,9.,4.,4.,4.,4.];d.loc[ix,'c']=c
    return d


def early_reference(d):
    values={}; closes={}; dates=sorted(d.t.dt.normalize().unique())
    for day in dates:
        g=d[d.t.dt.normalize().eq(day)]; q=list(g.qv)
        if list(g.t.dt.hour)!=[0,4,8,12,16,20]:continue
        closes[day]=g.c.iloc[-1]
        values[day]=(sum(q[:2])/sum(q) if np.isfinite(q).all() and min(q)>=0 and sum(q)>0 else np.nan)
    result={}; previous=False
    for day in closes:
        window=[day-pd.Timedelta(days=k) for k in range(21,-1,-1)]
        ready=all(x in values and np.isfinite(values[x]) for x in window)
        hit=bool(ready and values[day]>=.5 and values[day]>=1.5*np.median([values[day-pd.Timedelta(days=k)] for k in range(1,21)]) and np.log(closes[day]/closes[day-pd.Timedelta(days=1)])<=-.03)
        result[day]=hit and not previous;previous=hit
    return pd.Series(result),pd.Series(values)


def test_peer_reference_top5_lexical_exclusions_and_min50(peer_sample):
    panel=peer_sample; raw,chosen=reference_at(panel,'TARGET',70)
    assert raw and [a[0] for a in chosen]==[f'P{i:02}' for i in range(5)]
    M.set_panel(panel); day=pd.Timestamp('2023-03-12')
    assert M.peer_event('TARGET').loc[day]
    assert not M.peer_event('TARGET').loc[day-pd.Timedelta(days=1)]
    less={k:v for k,v in panel.items() if k not in ('P49','P50')}
    M.set_panel(less); assert not M.peer_event('TARGET').loc[day]
    with pytest.raises(ValueError):M.peer_event('BTCUSDT')
    with pytest.raises(ValueError):M.peer_event('UNKNOWN')


def test_peer_prior_liquidity_and_gap(peer_sample):
    P=peer_sample.copy(); P['P00']=P['P00'].copy(); day=pd.Timestamp('2023-03-12')
    P['P00'].loc[P['P00'].t.dt.normalize().lt(day),'qv']=0.
    M.set_panel(P); assert not M.peer_event('TARGET').loc[day]
    P=peer_sample.copy(); P['TARGET']=P['TARGET'].loc[~P['TARGET'].t.eq(pd.Timestamp('2023-02-01 12:00'))].reset_index(drop=True)
    M.set_panel(P); assert not M.peer_event('TARGET').loc[day]


def test_early_independent_reference_and_invalid():
    d=early_frame(); D,flags,E=M.early_volume(d); want,q=early_reference(d)
    np.testing.assert_allclose(E,q,equal_nan=True);np.testing.assert_array_equal(flags,want)
    assert flags.iloc[25] and not flags.iloc[26]
    for val in (np.nan,-1.):
        x=d.copy();x.loc[x.t.eq(pd.Timestamp('2023-01-16 00:00')),'qv']=val
        assert not M.early_volume(x)[1].iloc[25]
    x=d.loc[~d.t.eq(pd.Timestamp('2023-01-16 12:00'))].reset_index(drop=True)
    assert not M.early_volume(x)[1].loc[pd.Timestamp('2023-01-26')]
    x=d.copy(); x.loc[x.t.dt.normalize().eq(pd.Timestamp('2023-01-26')),'qv']=0.
    assert not M.early_volume(x)[1].iloc[25]


def test_signals_regime_and_prefix(monkeypatch,peer_sample):
    P=peer_sample; d=P['TARGET']; M.set_panel(P)
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.zeros(len(d)))
    full=M.sig_peer(d,{},dict(research_symbol='TARGET'))
    assert list(d.t[full['le']])==[pd.Timestamp('2023-03-13')]
    with pytest.raises(ValueError):M.sig_peer(d,{}, {})
    for n in (423,426):
        M.set_panel({k:v.iloc[:n] for k,v in P.items()})
        part=M.sig_peer(d.iloc[:n],{},dict(research_symbol='TARGET'))
        for k in full: np.testing.assert_array_equal(part[k],full[k].iloc[:n])
    M.set_panel(P); E=early_frame(); early=M.sig_early(E,{},{}); assert list(E.t[early['le']])==[pd.Timestamp('2023-01-27')]
    for n in (150,153,156,159,180):
        part=M.sig_early(E.iloc[:n],{}, {})
        for k in early: np.testing.assert_array_equal(part[k],early[k].iloc[:n])
    for regime in (np.nan,-1.,1.):
        monkeypatch.setattr(M.L1,'coin_regime',lambda d,r=regime:np.full(len(d),r))
        assert bool(M.sig_peer(d,{},dict(research_symbol='TARGET'))['le'].any())==(regime==1.)
        assert not M.sig_early(E,{}, {})['le'].any()


def test_default_off(monkeypatch):
    assert 'longscout_v99' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for k in M.CANDIDATES: monkeypatch.delitem(M.S.STRATEGIES,k,raising=False)
    M.register()
    for k,v in M.CANDIDATES.items():
        assert M.S.STRATEGIES[k]['research_only'] and v['tf']=='4h'
        assert v['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=30)
        monkeypatch.delitem(M.S.STRATEGIES,k)


@pytest.mark.parametrize('case',['valid','missing','duplicate','out_of_order','missing_qv'])
def test_peer_daily_independent_complete_bins(case):
    d=bars(np.zeros(4))
    if case=='missing': d=d.drop(index=8).reset_index(drop=True)
    elif case=='duplicate': d.loc[8,'t']=d.loc[7,'t']
    elif case=='out_of_order':
        d.loc[[7,8],:]=d.loc[[8,7],:].to_numpy()
    elif case=='missing_qv': d.loc[8,'qv']=np.nan
    expected={}
    for day,g in d.groupby(d.t.dt.normalize()):
        if list(g.t.dt.hour)!=[0,4,8,12,16,20]: continue
        q=list(g.qv)
        expected[day]=(g.c.dropna().iloc[-1],sum(q) if all(pd.notna(q)) else np.nan)
    got=M.peer_daily(d)
    assert list(got.index)==list(expected)
    for day,(c,q) in expected.items():
        assert got.loc[day,'c']==c
        assert got.loc[day,'qv']==q or (pd.isna(got.loc[day,'qv']) and pd.isna(q))
