import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_ravi_c170 as C
def tape(active=True):
 x=np.arange(500.);c=100+.001*x*x+2*np.sin(x/10) if active else 200-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_ravi_trend_long_1h(d,S.RAVI1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_formula():
 d=tape();f=d.c.rolling(7,min_periods=7).mean();s=d.c.rolling(65,min_periods=65).mean();expected=100*(f-s).abs()/s;got=S._ravi(d.c);assert np.allclose(got[0].dropna(),expected.dropna()) and np.allclose(got[1].dropna(),f.dropna())
def test_scale():
 d=tape();assert np.allclose(S._ravi(d.c)[0].dropna(),S._ravi(d.c*2)[0].dropna())
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
