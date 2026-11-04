import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_gmma_c172 as C
def tape(active=True):
 x=np.arange(500.);c=100+np.where(x>100,.08*(x-100),0)+2*np.sin(x/15) if active else 200-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_gmma_ribbon_long_1h(d,S.GMMA1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_formula():
 d=tape();s,l=S._gmma_ribbons(d.c);expected_s=pd.concat([d.c.ewm(span=n,adjust=False,min_periods=n).mean() for n in (3,5,8,10,12,15)],axis=1).min(axis=1,skipna=False);expected_l=pd.concat([d.c.ewm(span=n,adjust=False,min_periods=n).mean() for n in (30,35,40,45,50,60)],axis=1).max(axis=1,skipna=False);assert np.allclose(s.dropna(),expected_s.dropna()) and np.allclose(l.dropna(),expected_l.dropna())
def test_scale():
 d=tape();a=S._gmma_ribbons(d.c);b=S._gmma_ribbons(d.c*2);assert all(np.allclose(x.dropna()*2,y.dropna()) for x,y in zip(a,b))
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
