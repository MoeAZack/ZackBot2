"""Independent synthetic V100 references and causal signal checks."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v100 as M


def frame(r):
    c=100*np.exp(np.cumsum(r)); t=pd.date_range('2023-01-01',periods=len(c),freq='4h')
    return pd.DataFrame(dict(t=t,o=c,h=c*1.01,l=c*.99,c=c,v=1.,qv=1.))


def leverage_frame():
    rng=np.random.default_rng(100)
    r=rng.normal(0,.015,130); r[70:]=-np.abs(r[70:]); c=100*np.exp(np.cumsum(r))
    w=rng.uniform(.01,.04,130)
    for i in range(70,130):w[i]=.02+3*max(-r[i-1],0)
    rows=[]
    for i in range(130):
        for h in range(0,24,4):
            rows.append((pd.Timestamp('2023-01-01')+pd.Timedelta(days=i,hours=h),c[i],c[i]*np.exp(w[i]/2),c[i]*np.exp(-w[i]/2),c[i],1.,1.))
    return pd.DataFrame(rows,columns=['t','o','h','l','c','v','qv'])


def leverage_reference(D):
    rows=list(D.itertuples());r=[np.nan]*len(D);w=[np.nan]*len(D);rho=[np.nan]*len(D);out=[False]*len(D)
    for i,row in enumerate(rows):
        w[i]=np.log(row.h/row.l)
        if i and row.Index-rows[i-1].Index==pd.Timedelta(days=1):r[i]=np.log(row.c/rows[i-1].c)
    for i in range(61,len(rows)):
        x=[max(-r[j-1],0) for j in range(i-59,i+1)];y=w[i-59:i+1]
        if np.isfinite(x+y).all() and np.std(x)>1e-14 and np.std(y)>1e-14:rho[i]=np.corrcoef(x,y)[0,1]
        if i>=62 and rows[i].Index-rows[i-62].Index==pd.Timedelta(days=62):
            out[i]=rho[i]>=.30 and rho[i-1]<.30 and r[i]<0
    return np.array(out)


def distribution_frame():
    rng=np.random.default_rng(101)
    r=rng.normal(0,.002,480)
    r[180:216]=-.009
    r[330:366]=.009
    return frame(r)


def quantile(sorted_values,p):
    at=(len(sorted_values)-1)*p;lo=int(np.floor(at));frac=at-lo
    return sorted_values[lo]*(1-frac)+sorted_values[min(lo+1,len(sorted_values)-1)]*frac


def distribution_reference(d):
    times=list(d.t);closes=list(d.c); values={}; raw={}
    valid_days={day for day,g in d.groupby(d.t.dt.normalize()) if list(g.t.dt.hour)==[0,4,8,12,16,20]}
    for i,t in enumerate(times):
        if t.hour!=20 or t.normalize() not in valid_days or i<144:continue
        if any(times[k]-times[k-1]!=pd.Timedelta(hours=4) for k in range(i-143,i+1)):continue
        rr=[np.log(closes[k]/closes[k-1]) for k in range(i-143,i+1)]
        base=rr[:120];recent=rr[120:];mean=sum(base)/120
        sd=(sum((x-mean)**2 for x in base)/120)**.5
        if not np.isfinite(sd) or sd<1e-14:continue
        a,b=sorted(base),sorted(recent)
        score=sum(abs(quantile(a,(k-.5)/24)-quantile(b,(k-.5)/24)) for k in range(1,25))/24/sd
        med=(b[11]+b[12])/2;day=t.normalize()
        values[day]=score;raw[day]=(score>=.75 and med<=-.5*sd,score>=.75 and med>=.5*sd)
    out={}
    for day in sorted(valid_days):
        prev=day-pd.Timedelta(days=1);now=raw.get(day,(False,False));last=raw.get(prev,(False,False))
        out[day]=tuple(now[k] and not last[k] and prev in valid_days for k in range(2))
    return out,values


def test_leverage_independent_lag_cross_gap_and_zero():
    d=leverage_frame();D=M.H.daily(d); want=leverage_reference(D)
    assert want.any();np.testing.assert_array_equal(M.leverage_flags(D),want)
    gap=D.drop(D.index[50]);np.testing.assert_array_equal(M.leverage_flags(gap),leverage_reference(gap))
    const=D.copy();const['h']=const['l']*2.
    assert not M.leverage_flags(const).any()


def test_distribution_independent_quantiles_both_sides_gap():
    d=distribution_frame();D,flags,score=M.distribution_flags(d);want,values=distribution_reference(d)
    assert any(v[0] for v in want.values()) and any(v[1] for v in want.values())
    for day,(lo,hi) in want.items():
        assert bool(flags['long'].loc[day])==lo and bool(flags['short'].loc[day])==hi
    for day,v in values.items(): assert score.loc[day]==pytest.approx(v,rel=1e-10,abs=1e-10)
    gap=d.loc[~d.t.eq(pd.Timestamp('2023-01-30 08:00'))].reset_index(drop=True)
    D,flags,score=M.distribution_flags(gap);want,values=distribution_reference(gap)
    for day,(lo,hi) in want.items():
        assert bool(flags['long'].loc[day])==lo and bool(flags['short'].loc[day])==hi
    constant=frame(np.zeros(180));assert not M.distribution_flags(constant)[1]['long'].any()
    assert not M.distribution_flags(constant)[1]['short'].any()


@pytest.mark.parametrize('kind',['leverage','distribution'])
def test_full_signal_prefix_timing_regime(kind,monkeypatch):
    d=leverage_frame() if kind=='leverage' else distribution_frame()
    fn=M.sig_leverage if kind=='leverage' else M.sig_distribution
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.zeros(len(d)))
    full=fn(d,{},{});assert (full['le'] | full['se']).any()
    assert d.t[full['le'] | full['se']].dt.hour.eq(0).all()
    for n in ([420,480,520,600,720] if kind=='leverage' else [180,210,216,230,330,366,390]):
        part=fn(d.iloc[:n],{}, {})
        for k in full:np.testing.assert_array_equal(part[k],full[k].iloc[:n])
    for regime in (np.nan,-1.,1.):
        monkeypatch.setattr(M.L1,'coin_regime',lambda d,r=regime:np.full(len(d),r))
        out=fn(d,{},{}); assert bool((out['le']|out['se']).any())==(kind=='leverage' and regime==1.)


def test_default_off(monkeypatch):
    assert 'longscout_v100' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for k in M.CANDIDATES:monkeypatch.delitem(M.S.STRATEGIES,k,raising=False)
    M.register()
    for k,v in M.CANDIDATES.items():
        assert M.S.STRATEGIES[k]['research_only'] and v['tf']=='4h'
        assert v['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=30)
        monkeypatch.delitem(M.S.STRATEGIES,k)

