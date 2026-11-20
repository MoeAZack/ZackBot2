"""Synthetic-only independent checks of V94's frozen peer formulas."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'research')]
import longscout_v94 as M


def make_panel(n=51, days=67):
    ts=pd.date_range('2023-01-01', periods=days*6, freq='4h')
    panel={}
    for i in range(n):
        daily=np.arange(days)
        returns=np.full(days, (i-25)*.0001)
        returns[64]=(i-25)*.004
        close=100*np.exp(np.cumsum(returns))
        volume=np.ones(days)*100
        if i>=41: volume[63:65]=300
        c=np.repeat(close,6)
        panel[f'COIN{i:03}']=pd.DataFrame(dict(t=ts,o=c,h=c*1.01,l=c*.99,c=c,
                                                v=np.repeat(volume/6,6),qv=np.full(days*6,2e6)))
    panel['BTCUSDT']=panel['COIN000'].copy()
    return panel


def ref(panel):
    groups={s:{day:(list(g.t.dt.hour),g.qv.sum(min_count=6),g.c.iloc[-1],g.v.sum())
               for day,g in d.groupby(d.t.dt.normalize())}
            for s,d in panel.items() if s!='BTCUSDT'}
    days=sorted(set().union(*(g.keys() for g in groups.values())))
    rows=[]; disp={}
    for day in days:
        vals=[]
        for g in groups.values():
            ds=[g.get(day-pd.Timedelta(days=k)) for k in range(30,-1,-1)]
            if any(x is None or x[0]!=[0,4,8,12,16,20] for x in ds): continue
            q=[x[1] for x in ds[:-1]]
            if not np.isfinite(np.median(q)) or np.median(q)<1e7: continue
            close=[x[2] for x in ds]
            vol=[x[3] for x in ds]
            vals.append((np.log(close[-1]/close[-2]),vol[-1]/np.median(vol[-21:-1]),close[-1]/close[-11]-1))
        n=len(vals)
        if n<50:
            rows.append((day,n,np.nan,np.nan,np.nan,np.nan,False)); disp[day]=np.nan; continue
        r,v,m=np.asarray(vals).T
        sd=np.std(r,ddof=0)
        previous=[disp.get(day-pd.Timedelta(days=k),np.nan) for k in range(30,0,-1)]
        shock=all(np.isfinite(previous)) and sd>2*np.median(previous)
        disp[day]=sd
        rows.append((day,n,np.quantile(v,.8),np.quantile(m,.5),np.quantile(r,.1),np.quantile(r,.9),shock))
    return pd.DataFrame(rows,columns=['day','n','vol80','mom50','r10','r90','shock']).set_index('day')


def test_reference_features_quantiles_dispersion_and_no_btc():
    P=make_panel(); got=M.features(P); want=ref(P)
    for name in ['vol80','mom50','r10','r90']:
        np.testing.assert_allclose(got[name],want[name],equal_nan=True)
    np.testing.assert_array_equal(got.shock.fillna(False),want.shock)
    assert got.shock.loc[pd.Timestamp('2023-03-06')]
    pd.testing.assert_frame_equal(got,M.features({s:d for s,d in P.items() if s!='BTCUSDT'}))


def test_peer_eligibility_liquidity_and_missing_bar():
    P=make_panel(n=51)
    P['COIN000'].loc[:, 'qv']=0
    got=M.features(P)
    assert got.n.dropna().eq(50).all()
    P['COIN001']=P['COIN001'].loc[~P['COIN001'].t.eq(pd.Timestamp('2023-02-01 12:00'))].reset_index(drop=True)
    got=M.features(P)
    assert pd.isna(got.loc[pd.Timestamp('2023-03-01'),'n'])
    assert not bool(got.shock.fillna(False).loc[pd.Timestamp('2023-03-06')])


def test_strict_ties_and_first_condition():
    D=M.H.daily(make_panel()['COIN050'])
    F=pd.DataFrame(index=D.index, data={'n':50,'vol80':1.,'mom50':0.,'r10':-.1,'r90':.1,'shock':False})
    D.loc[:,'v']=100.
    assert not M.daily_flags(D,F,True)['long'].any()  # exact 80th percentile
    D.loc[D.index[40:42],'v']=300.
    out=M.daily_flags(D,F,True)['long']
    assert out.iloc[40] and not out.iloc[41]
    F.loc[D.index[45:47],['shock','r10']]=[True,0.]
    D.loc[D.index[45:47],'c']=D.c.iloc[44]  # r == r10, strict tie
    assert not M.daily_flags(D,F,False)['long'].iloc[45:47].any()


def test_newly_eligible_coin_enters_full_condition():
    D=M.H.daily(make_panel()['COIN050'])
    D['v']=1.07**np.arange(len(D))
    D['qv']=np.where(np.arange(len(D))<15,0.,12e6)
    F=pd.DataFrame(index=D.index, data={'n':50,'vol80':1.,'mom50':-1.,'r10':-.1,'r90':.1,'shock':False})
    out=M.daily_flags(D,F,True)['long']
    assert out.iloc[31]  # eligibility first holds today; full condition was false yesterday


@pytest.mark.parametrize('rotation',[True,False])
def test_signal_prefix_and_regime(rotation,monkeypatch):
    P=make_panel(); d=P['COIN050'] if rotation else P['COIN000']
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.zeros(len(x)))
    M.set_panel(P); fn=M.sig_rotation if rotation else M.sig_dispersion
    full=fn(d,{},{}); assert full['le'].any()
    assert (d.t[full['le']].dt.hour==0).all()
    for end in [380,390,400]:
        M.set_panel({s:x.iloc[:end] for s,x in P.items()})
        got=fn(d.iloc[:end],{},{})
        for k in full: np.testing.assert_array_equal(got[k],full[k].iloc[:end])
    M.set_panel(P)
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.full(len(x),-1.))
    assert not fn(d,{}, {})['le'].any()
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.ones(len(x)))
    assert fn(d,{}, {})['le'].any() == rotation


def test_default_off_and_management():
    assert 'longscout_v94' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    a=M.CANDIDATES['l94_peer_attention_rotation_4h']; b=M.CANDIDATES['r94_peer_dispersion_shock_fade_4h']
    assert a['research_only'] and b['research_only'] and a['tf']==b['tf']=='4h'
    assert a['mgmt']==dict(stop_atr=3.,tp_r=3.,max_bars=60)
    assert b['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=30)
