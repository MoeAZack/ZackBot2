"""Synthetic-only independent tests for V102's bar-based auction profile."""
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v102 as M


def frame(days=24):
    n=days*6;t=pd.date_range('2023-01-01',periods=n,freq='4h').as_unit('ns')
    return pd.DataFrame(dict(t=t,o=np.full(n,100.),h=np.full(n,101.),l=np.full(n,99.),c=np.full(n,100.),v=np.ones(n),qv=np.ones(n)))


def reference(bars):
    rows=list(bars.itertuples());lo=min(r.l for r in rows);hi=max(r.h for r in rows)
    left,right=math.log(lo),math.log(hi);edges=[left+(right-left)*i/20 for i in range(21)]
    w=[0.]*20
    for r in rows:
        price=math.log((r.h+r.l+r.c)/3)
        idx=next((i for i in range(20) if price<edges[i+1]),19)
        w[idx]+=r.qv
    peak=max(w);poc=next(i for i,x in enumerate(w) if x==peak)
    low=high=poc;total=sum(w);acc=w[poc]
    while acc<.7*total:
        a=w[low-1] if low else -1;b=w[high+1] if high<19 else -1
        if a>=b:low-=1;acc+=a
        else:high+=1;acc+=b
    return {'poc':math.exp((edges[poc]+edges[poc+1])/2), 'val':math.exp(edges[low]),'vah':math.exp(edges[high+1])}


def test_profile_independent_histogram_greedy_and_invalid():
    d=frame(20);rng=np.random.default_rng(33)
    center=rng.uniform(95,105,len(d));d['l']=center-1;d['h']=center+1;d['c']=center;d['qv']=rng.integers(0,10,len(d))
    assert M.profile_window(d)==pytest.approx(reference(d),rel=1e-12)
    for change in ('zero','negative','nan','gap','misordered','bad_price','flat'):
        x=d.copy()
        if change=='zero':x['qv']=0
        if change=='negative':x.loc[0,'qv']=-1
        if change=='nan':x.loc[0,'h']=np.nan
        if change=='gap':x.loc[10,'t']+=pd.Timedelta(hours=4)
        if change=='misordered':x=x.iloc[::-1]
        if change=='bad_price':x.loc[0,'c']=x.loc[0,'h']+1
        if change=='flat':x[['h','l','c']]=100.
        assert M.profile_window(x) is None


def test_equal_poc_and_greedy_tie_use_lower_side():
    d=frame(20);d['qv']=0.
    # Fixed extrema 1..exp(20); typical-price centers in bins 9,10,11.
    d['l']=1.;d['h']=math.exp(20);d['c']=1.
    # Instead use narrow bars, and zero-weight rows to anchor price extrema.
    for i,price in enumerate((math.exp(9.5),math.exp(10.5),math.exp(11.5))):
        d.loc[i,['l','h','c','qv']]=[price,price,price,1.]
    d.loc[3,['l','h','c','qv']]=[1.,math.exp(20),1.,0.]
    got=M.profile_window(d);assert got==pytest.approx(reference(d),rel=1e-12)
    assert got['poc']==pytest.approx(math.exp(9.5))
    # Equal adjacent weights reach 70% on the first expansion, exposing the tie.
    d.loc[1,'qv']=4.;d.loc[0,'qv']=3.;d.loc[2,'qv']=3.
    tied=M.profile_window(d)
    assert tied==pytest.approx(reference(d),rel=1e-12)
    assert tied['val']==pytest.approx(math.exp(9)) and tied['vah']==pytest.approx(math.exp(11))



def test_profiles_partial_day_prefix_and_prior_data_only():
    d=frame(23);full=M.daily_profiles(d);day=pd.Timestamp('2023-01-21')
    assert day in full.index and full.loc[day].to_dict()==pytest.approx(reference(d.iloc[:120]))
    for k in range(1,7):
        prefix=d.iloc[:120+k].copy();p=M.daily_profiles(prefix)
        pd.testing.assert_series_equal(p.loc[day],full.loc[day])
        for fn in (M.sig_acceptance,M.sig_rejection):
            a=fn(d,{},{});b=fn(prefix,{}, {})
            for key in a:np.testing.assert_array_equal(a[key].iloc[:len(prefix)],b[key])
    altered=d.copy();altered.loc[120:,'c']=100.5
    pd.testing.assert_series_equal(M.daily_profiles(altered).loc[day],full.loc[day])
    broken=d.drop(index=10).reset_index(drop=True)
    assert day not in M.daily_profiles(broken).index


def test_real_signals_complete_day_edges_and_intraday_poc_exit(monkeypatch):
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.zeros(len(d)))
    d=frame(24);profile=M.daily_profiles(d).iloc[0]
    # Two completed closes above their respective prior VAH; entry at following 00.
    d.loc[120:131,'c']=100.8
    a=M.sig_acceptance(d,{},{});assert a['le'].iloc[132] and not a['le'].iloc[126]
    assert not M.sig_acceptance(d.iloc[:132],{}, {})['le'].any()
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.full(len(d),-1.))
    assert not M.sig_acceptance(d,{}, {})['le'].any()
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.zeros(len(d)))
    r=frame(24);r.loc[120:125,'c']=(profile.val+profile.poc)/2
    r.loc[120,'l']=profile.val*.99
    r.loc[126,'c']=100.9
    out=M.sig_rejection(r,{},{});assert out['le'].iloc[126]
    assert out['lx'].iloc[126]
    prefix=M.sig_rejection(r.iloc[:127],{},{});assert prefix['le'].iloc[126] and prefix['lx'].iloc[126]
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.ones(len(d)))
    assert not M.sig_rejection(r,{}, {})['le'].any()
    assert 'longscout_v102' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for key,v in M.CANDIDATES.items():assert key not in M.S.STRATEGIES and v['research_only'] and v['tf']=='4h'



@pytest.mark.parametrize("unit",["s","ms","us","ns"])
def test_timestamp_resolution_independence(unit):
    d=frame(20);d['t']=d.t.dt.as_unit(unit)
    assert M.profile_window(d)==pytest.approx(reference(d),rel=1e-12)
