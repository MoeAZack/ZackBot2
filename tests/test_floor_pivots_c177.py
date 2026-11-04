import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_floor_pivots_c177 as C
def tape(active=True):
 x=np.arange(720.);c=100+.04*x+4*np.sin(x/5) if active else 200-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_floor_pivot_breakout_long_1h(d,S.PIVOT1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_prior_day_formula():
 d=tape();p,r1,s1=S._classic_floor_pivots(d.t,d.h,d.l,d.c);h=d.h.iloc[:24].max();l=d.l.iloc[:24].min();c=d.c.iloc[23];expected=(h+l+c)/3;assert p.iloc[24]==expected and r1.iloc[24]==2*expected-l and s1.iloc[24]==2*expected-h
def test_scale():
 d=tape();a=S._classic_floor_pivots(d.t,d.h,d.l,d.c);b=S._classic_floor_pivots(d.t,d.h*2,d.l*2,d.c*2);assert all(np.allclose(x.dropna()*2,y.dropna()) for x,y in zip(a,b))
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
