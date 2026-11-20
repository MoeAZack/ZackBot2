"""V97 synthetic, independent formula and causal checks."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v97 as M


def bars(closes):
    return pd.DataFrame([(pd.Timestamp('2023-01-01')+pd.Timedelta(days=i,hours=h),float(c),float(c)+1,float(c)-1,float(c),1.,1.)
                         for i,c in enumerate(closes) for h in range(0,24,4)],columns=['t','o','h','l','c','v','qv'])


def residual_data():
    rng=np.random.default_rng(97)
    rb=rng.normal(0,.008,110); rc=.0004+1.3*rb+rng.normal(0,.003,110)
    rb[80]=0.; rc[80]=-.025
    return bars(100*np.cumprod(1+rc)),bars(100*np.cumprod(1+rb))


def ols_reference(J):
    values=[]
    for i in range(len(J)):
        if i<60 or not np.isfinite(J.rb.iloc[i-60:i]).all() or not np.isfinite(J.rc.iloc[i-60:i]).all():
            values.append((np.nan,)*4); continue
        x=list(J.rb.iloc[i-60:i]); y=list(J.rc.iloc[i-60:i])
        mx=sum(x)/60; my=sum(y)/60; ss=sum((v-mx)**2 for v in x)
        if ss<1e-24:
            values.append((np.nan,)*4); continue
        beta=sum((a-mx)*(b-my) for a,b in zip(x,y))/ss
        alpha=my-beta*mx
        sd=(sum((b-alpha-beta*a)**2 for a,b in zip(x,y))/60)**.5
        residual=J.rc.iloc[i]-alpha-beta*J.rb.iloc[i]
        values.append((beta,alpha,sd,residual))
    return np.array(values)


def test_ols_reference_gap_variance_and_quiet_limit():
    coin,btc=residual_data(); M.set_btc(btc); D,J=M.J93.joint(coin)
    ref=ols_reference(J); got=M.residual_statistics(J)
    for i,key in enumerate(('beta','alpha','sd','residual')):
        np.testing.assert_allclose(got[key],ref[:,i],atol=1e-12,equal_nan=True)
    flags=M.residual_flags(D,J); assert flags.iloc[80] and flags.sum()==1
    x=J.copy(); x.loc[x.index[80],'rb']=.010001
    assert not M.residual_flags(D,x).iloc[80]
    x.loc[x.index[80],'rb']=.01
    assert M.residual_flags(D,x).iloc[80]
    x=J.copy(); x.loc[x.index[40],'rc']=np.nan
    assert not M.residual_flags(D,x).iloc[80]
    flat=J.copy(); flat['rb']=.001
    assert not M.residual_flags(D,flat).any()
    for which in ('coin','btc'):
        c,b=coin.copy(),btc.copy()
        if which=='coin': c=c.loc[~c.t.eq(pd.Timestamp('2023-02-10 12:00'))].reset_index(drop=True)
        else: b=b.loc[~b.t.eq(pd.Timestamp('2023-02-10 12:00'))].reset_index(drop=True)
        M.set_btc(b); d,j=M.J93.joint(c)
        assert not M.residual_flags(d,j).get(pd.Timestamp('2023-03-22'),False)


def participation_data():
    d=bars([100.,100.,98.,96.04,96.04,96.04])
    for day in (2,3):
        idx=d.index[d.t.dt.normalize().eq(pd.Timestamp('2023-01-01')+pd.Timedelta(days=day))]
        d.loc[idx,'qv']=0.
        d.loc[idx[0],['o','c','qv']]=[100.,101.,1.35]
        d.loc[idx[-1],['o','qv']]=[100.,.65]
    return d


def participation_reference(d):
    result={}; qvalues={}; last_hit=False; previous=None; previous2=None
    for day,g in d.groupby(d.t.dt.normalize(),sort=True):
        complete=list(g.t.dt.hour)==[0,4,8,12,16,20] and len(g)==6
        if not complete: continue
        q=list(g.qv); valid=all(np.isfinite(q)) and all(x>=0 for x in q) and sum(q)>0
        Q=sum(v*np.sign(c-o) for v,c,o in zip(q,g.c,g.o))/sum(q) if valid else np.nan
        hit=previous is not None and day-previous[0]==pd.Timedelta(days=1) and Q>=.35 and g.c.iloc[-1]/previous[1]-1<=-.02
        ready=previous2 is not None and day-previous2==pd.Timedelta(days=2)
        result[day]=bool(hit and not last_hit and ready)
        qvalues[day]=Q
        previous2=previous[0] if previous is not None else None
        previous=(day,g.c.iloc[-1]);last_hit=hit
    return pd.Series(result),pd.Series(qvalues)


def test_participation_reference_boundaries_repeats_and_invalid():
    d=participation_data(); D,flags,Q=M.participation(d); want,q=participation_reference(d)
    np.testing.assert_allclose(Q,q,equal_nan=True)
    np.testing.assert_array_equal(flags,want)
    assert Q.iloc[2]==pytest.approx(.35)
    assert flags.iloc[2] and not flags.iloc[3]
    for invalid in (-1.,np.nan):
        x=d.copy(); x.loc[x.t.eq(pd.Timestamp('2023-01-03 04:00')),'qv']=invalid
        assert not M.participation(x)[1].iloc[2]
    x=d.copy(); x.loc[x.t.dt.normalize().eq(pd.Timestamp('2023-01-03')),'qv']=0.
    assert not M.participation(x)[1].iloc[2]
    x=d.drop(columns='qv'); assert not M.participation(x)[1].any()
    x=d.copy(); x.loc[x.t.eq(pd.Timestamp('2023-01-03 00:00')),'qv']=1.349
    assert not M.participation(x)[1].iloc[2]
    x=d.copy(); x.loc[x.t.eq(pd.Timestamp('2023-01-03 20:00')),'c']=98.001
    assert not M.participation(x)[1].iloc[2]
    x=d.loc[~d.t.eq(pd.Timestamp('2023-01-02 12:00'))].reset_index(drop=True)
    assert not M.participation(x)[1].loc[pd.Timestamp('2023-01-03')]


@pytest.mark.parametrize('kind',['residual','participation'])
def test_actual_prefix_time_regime(kind,monkeypatch):
    coin,btc=residual_data() if kind=='residual' else (participation_data(),bars([100.]*6))
    fn=M.sig_residual if kind=='residual' else M.sig_participation
    monkeypatch.setattr(M.L1,'coin_regime',lambda d:np.zeros(len(d)))
    M.set_btc(btc); full=fn(coin,{},{}); assert full['le'].any()
    assert coin.t[full['le']].dt.hour.eq(0).all()
    for n in ([478,480,483,486,500,600] if kind=='residual' else [12,16,18,19,23,29]):
        M.set_btc(btc.iloc[:n]); part=fn(coin.iloc[:n],{},{})
        for k in full: np.testing.assert_array_equal(part[k],full[k].iloc[:n])
    M.set_btc(btc)
    for regime in (np.nan,-1.,1.):
        monkeypatch.setattr(M.L1,'coin_regime',lambda d,r=regime:np.full(len(d),r))
        assert bool(fn(coin,{}, {})['le'].any()) == (regime==1.)


def test_default_off(monkeypatch):
    assert 'longscout_v97' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for k in M.CANDIDATES: monkeypatch.delitem(M.S.STRATEGIES,k,raising=False)
    M.register()
    for k,v in M.CANDIDATES.items():
        assert M.S.STRATEGIES[k]['research_only'] and v['tf']=='4h'
        assert v['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=30)
        monkeypatch.delitem(M.S.STRATEGIES,k)


def test_zero_residual_variance_must_skip():
    rng=np.random.default_rng(971)
    x=rng.integers(-8,9,90)/1024.; y=2*x
    y[80]-=.01
    index=pd.date_range('2023-01-01',periods=len(x))
    J=pd.DataFrame(dict(rb=x,rc=y),index=index)
    D=pd.DataFrame({'c':np.ones(len(x))},index=index)
    assert not M.residual_flags(D,J).iloc[80]
