"""Independent synthetic V105 formula and signal timing tests, no market data."""
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v105 as M


PLANTED_MUTANTS = [
    ('bipower_as_realized_variation', '(rv-bv).clip(lower=0)', '(rv-rv).clip(lower=0)',
     'test_discontinuity_math_and_causality'),
    ('same_day_in_conditioned_model', 'up.shift(2,fill_value=False)', 'up.shift(0,fill_value=False)',
     'test_conditional_prior_window_reference_and_future_invariance'),
    ('allow_falling_long', 'np.isin(L1.coin_regime(d),(0.,1.))', 'np.isfinite(L1.coin_regime(d))',
     'test_real_entries_regime_prefix_and_next_day'),
]


def from_daily_returns(returns,volume):
    close=100.;rows=[];base=pd.Timestamp('2022-01-01')
    for day,(ret,q) in enumerate(zip(returns,volume)):
        for k in range(6):
            prev=close;close*=math.exp(ret if k==5 else 0.)
            rows.append((base+pd.Timedelta(days=day,hours=4*k),prev,max(prev,close)*1.001,min(prev,close)*.999,close,1.,q/6))
    return pd.DataFrame(rows,columns=['t','o','h','l','c','v','qv'])


def conditional_fixture():
    r=np.zeros(96);q=np.full(96,6.)
    for t in range(21,96,3):
        sign=1 if ((t-21)//3)%2==0 else -1
        r[t]=.02*sign;q[t]=18.
        if t+1<len(r):r[t+1]=-.01*sign
    return from_daily_returns(r,q)


def conditional_reference(D,j):
    close=list(D.c);q=list(D.qv);r=[float('nan')]+[math.log(close[i]/close[i-1]) for i in range(1,len(close))]
    up=[];down=[]
    for t in range(j-61,j-1):
        prev=sorted(q[t-20:t]);med=(prev[9]+prev[10])/2
        if med>0 and q[t]>=1.2*med:
            if r[t]>0:up.append(r[t+1])
            if r[t]<0:down.append(r[t+1])
    return len(up),len(down),sum(up)/len(up),sum(down)/len(down)


def test_discontinuity_math_and_causality():
    r=np.zeros(10);r[4]=-.06;d=from_daily_returns(r,np.full(10,6.))
    D,stat=M.discontinuity_statistics(d);i=4
    rr=[math.log(d.c.iloc[k]/d.c.iloc[k-1]) for k in range(i*6,i*6+6)]
    rv=sum(x*x for x in rr);bv=(math.pi/2)*(6/5)*sum(abs(rr[k])*abs(rr[k-1]) for k in range(1,6))
    assert stat.rv.iloc[i]==pytest.approx(rv);assert stat.bv.iloc[i]==pytest.approx(bv,abs=1e-15)
    assert stat.share.iloc[i]==pytest.approx(max(rv-bv,0)/rv)
    assert M.discontinuity_flags(d)[1].iloc[i]
    assert np.isnan(stat.share.iloc[2])
    gap=d.drop(index=18).reset_index(drop=True);assert not M.discontinuity_flags(gap)[1].any()


def test_conditional_prior_window_reference_and_future_invariance():
    d=conditional_fixture();D=M._days(d);st=M.conditional_statistics(D);j=84
    nu,nd,mu,md=conditional_reference(D,j)
    assert nu>=8 and nd>=8 and mu<=-.002 and md>=.002
    assert st.n_up.iloc[j]==nu and st.n_down.iloc[j]==nd
    assert st.mean_up.iloc[j]==pytest.approx(mu) and st.mean_down.iloc[j]==pytest.approx(md)
    before=st.iloc[j].copy();modified=d.copy();modified.loc[(j+1)*6:,'c']*=1.4
    after=M.conditional_statistics(M._days(modified)).iloc[j]
    pd.testing.assert_series_equal(before,after)
    gap=d.drop(index=50*6).reset_index(drop=True)
    _,flags=M.conditional_flags(gap)
    assert not flags['long'].any() and not flags['short'].any()


def test_real_entries_regime_prefix_and_next_day(monkeypatch):
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.zeros(len(d)))
    r=np.zeros(10);r[4]=-.06;d=from_daily_returns(r,np.full(10,6.))
    sig=M.sig_discontinuity(d,{},{});assert list(np.flatnonzero(sig['le']))==[30]
    cond=conditional_fixture();sig=M.sig_conditional(cond,{}, {})
    long_idx=list(np.flatnonzero(sig['le']));short_idx=list(np.flatnonzero(sig['se']))
    assert long_idx and short_idx
    assert all(cond.t.iloc[i].hour==0 for i in long_idx+short_idx)
    for cut in (30,31,84*6,85*6+1,90*6+1):
        for fn,src in ((M.sig_discontinuity,d),(M.sig_conditional,cond)):
            cut2=min(cut,len(src));a=fn(src,{},{});b=fn(src.iloc[:cut2],{}, {})
            for key in a:np.testing.assert_array_equal(a[key].iloc[:cut2],b[key])
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.full(len(d),-1.))
    assert not M.sig_discontinuity(d,{}, {})['le'].any()
    assert not M.sig_conditional(cond,{}, {})['le'].any()
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.ones(len(d)))
    assert M.sig_discontinuity(d,{}, {})['le'].any()
    assert not M.sig_conditional(cond,{}, {})['se'].any()
    assert 'longscout_v105' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for key,v in M.CANDIDATES.items():assert key not in M.S.STRATEGIES and v['research_only'] and v['tf']=='4h'


@pytest.mark.parametrize('unit',['s','ms','us','ns'])
def test_timestamp_units(unit):
    r=np.zeros(7);r[4]=-.06;d=from_daily_returns(r,np.full(7,6.));d['t']=d.t.dt.as_unit(unit)
    assert M.discontinuity_flags(d)[1].iloc[4]
