"""Independent synthetic-only formula, causality and configuration checks."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v95 as M


def make_daily(n=100):
    rng=np.random.default_rng(92)
    u=rng.normal(0,.6,n)
    r=rng.normal(0,.006,n)
    r[55:]=.02*u[54:-1]+.03
    return pd.DataFrame({'c':100*np.exp(np.cumsum(r)), 'v':np.exp(u)*100,
                         'qv':np.full(n,12e6)},index=pd.date_range('2023-01-01',periods=n))


def lag_reference(D):
    idx=D.index; c=D.c.to_numpy(); v=D.v.to_numpy(); n=len(D)
    r=np.full(n,np.nan); u=np.full(n,np.nan); rho=np.full(n,np.nan)
    for i in range(1,n): r[i]=np.log(c[i]/c[i-1])
    for i in range(20,n): u[i]=np.log(v[i]/np.median(v[i-20:i]))
    for i in range(50,n): rho[i]=np.corrcoef(u[i-30:i],r[i-29:i+1])[0,1]
    return pd.Series([i>=51 and idx[i]-idx[i-51]==pd.Timedelta(days=51)
                      and rho[i]>=.3 and rho[i-1]<.3 and r[i]>0
                      for i in range(n)],index=idx)


@pytest.mark.parametrize('gap',[False,True])
def test_leadlag_independent_formula_and_gap(gap):
    D=make_daily()
    if gap: D=D.drop(D.index[65])
    expected=lag_reference(D)
    np.testing.assert_array_equal(M.lead_lag(D),expected)
    assert lag_reference(make_daily()).any()
    if gap: assert not M.lead_lag(D).loc['2023-03-07':].any()
    for stop in [52,60,80]:
        np.testing.assert_array_equal(M.lead_lag(D.iloc[:stop]),M.lead_lag(D).iloc[:stop])


def panel(n=51,days=57):
    t=pd.date_range('2023-01-01',periods=days*6,freq='4h')
    out={}
    for i in range(n):
        c=np.repeat(100*np.exp(np.arange(days)*(i-25)*.0005),6)
        out[f'COIN{i:03}']=pd.DataFrame(dict(t=t,o=c,h=c*1.01,l=c*.99,c=c,
                                              v=np.ones(days*6),qv=np.full(days*6,2e6)))
    c=np.repeat(100*np.exp(np.arange(days)*.001),6)
    out['BTCUSDT']=pd.DataFrame(dict(t=t,o=c,h=c*1.01,l=c*.99,c=c,
                                    v=np.ones(days*6),qv=np.full(days*6,2e6)))
    return out


def test_leadlag_actual_mapping_and_regime(monkeypatch):
    D=make_daily()
    t=pd.date_range(D.index[0],periods=len(D)*6,freq='4h')
    c=np.repeat(D.c.to_numpy(),6)
    d=pd.DataFrame(dict(t=t,o=c,h=c*1.01,l=c*.99,c=c,
                        v=np.repeat(D.v.to_numpy()/6,6),qv=np.full(len(t),2e6)))
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.zeros(len(x)))
    full=M.sig_lead_lag(d,{},{}); assert full['le'].any()
    for day in D.index[lag_reference(D)]:
        if day+pd.Timedelta(days=1) <= D.index[-1]:
            assert full['le'][d.t.eq(day+pd.Timedelta(days=1))].iloc[0]
    for end in [320,360,440]:
        got=M.sig_lead_lag(d.iloc[:end],{},{})
        for k in full: np.testing.assert_array_equal(got[k],full[k].iloc[:end])
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.full(len(x),-1.))
    assert not M.sig_lead_lag(d,{}, {})['le'].any()
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.ones(len(x)))
    assert M.sig_lead_lag(d,{}, {})['le'].any()


def independent_weekly(panel,day):
    rr=[]
    for sym,d in panel.items():
        if sym=='BTCUSDT':continue
        byday={date:g for date,g in d.groupby(d.t.dt.normalize())}
        days=[byday.get(day-pd.Timedelta(days=k)) for k in range(30,-1,-1)]
        if any(g is None or list(g.t.dt.hour)!=[0,4,8,12,16,20] for g in days): continue
        q=[g.qv.sum(min_count=6) for g in days[:-1]]
        if not np.isfinite(np.median(q)) or np.median(q)<1e7:continue
        rr.append(days[-1].c.iloc[-1]/days[-8].c.iloc[-1]-1)
    return len(rr),np.quantile(rr,.2),np.quantile(rr,.8)


def test_weekly_independent_quantiles_eligibility_and_btc():
    P=panel(); P['COIN050'].loc[:,'qv']=0
    day=pd.Timestamp('2023-02-12')
    F=M.peer_weekly(P); n,lo,hi=independent_weekly(P,day)
    assert n==50 and F.loc[day,'n']==n
    np.testing.assert_allclose([F.loc[day,'lo'],F.loc[day,'hi']],[lo,hi])
    assert F.loc[day,'btc_up']
    P['COIN049']=P['COIN049'].loc[~P['COIN049'].t.eq(pd.Timestamp('2023-02-01 12:00'))].reset_index(drop=True)
    F=M.peer_weekly(P)
    assert F.loc[day,'n']==49
    assert not M.weekly_flags(M.H.daily(P['COIN000']),F)['long'].loc[day]
    P['BTCUSDT'].loc[:,'c']=100.
    assert not M.peer_weekly(P).loc[day,'btc_up']


def test_weekly_strict_ties_sunday_and_btc_gate():
    P=panel(); D=M.H.daily(P['COIN000']); F=M.peer_weekly(P)
    day=pd.Timestamp('2023-02-12')
    flags=M.weekly_flags(D,F)
    assert flags['long'].loc[day]
    assert all(D.index[flags['long']].dayofweek==6)
    ret=D.c.loc[day]/D.c.shift(7).loc[day]-1
    F.loc[day,'lo']=ret
    assert not M.weekly_flags(D,F)['long'].loc[day]
    F.loc[day,'lo']=ret+1
    F.loc[day,'btc_up']=False
    assert not M.weekly_flags(D,F)['long'].loc[day]


def test_weekly_actual_prefix_regime_mapping_and_exit(monkeypatch):
    P=panel(); d=P['COIN000']; M.set_panel(P)
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.zeros(len(x)))
    full=M.sig_weekly(d,{},{}); assert full['le'].any()
    entry=d.t[full['le']]
    assert (entry.dt.dayofweek==0).all() and (entry.dt.hour==0).all()
    exit_times=d.t[full['lx']]
    assert (exit_times.dt.dayofweek==6).all() and (exit_times.dt.hour==20).all()
    np.testing.assert_array_equal(full['lx'],full['sx'])
    end=260; M.set_panel({s:x.iloc[:end] for s,x in P.items()})
    part=M.sig_weekly(d.iloc[:end],{},{})
    for k in full: np.testing.assert_array_equal(part[k],full[k].iloc[:end])
    M.set_panel(P)
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.ones(len(x)))
    assert not M.sig_weekly(d,{}, {})['le'].any()


def test_default_off_and_management():
    assert 'longscout_v95' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    a=M.CANDIDATES['l95_volume_leads_return_4h']; b=M.CANDIDATES['r95_weekly_peer_rebalancing_4h']
    assert a['research_only'] and b['research_only'] and a['tf']==b['tf']=='4h'
    assert a['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=30)
    assert b['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=42)
