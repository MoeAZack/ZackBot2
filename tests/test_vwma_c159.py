import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_vwma_c159 as C
def tape(active=True):
 x=np.arange(500.);c=100+.05*x+3*np.sin(x/10) if active else 180-.1*x;o=np.r_[c[0],c[:-1]];v=500+(x%17)*50;d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=v,qv=v*c)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_vwma_crossover_long_1h(d,S.VWMA1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_formula():
 d=tape();i=123;expected=np.dot(d.c.iloc[i-19:i+1],d.v.iloc[i-19:i+1])/d.v.iloc[i-19:i+1].sum();assert np.isclose(S._vwma(d.c,d.v).iloc[i],expected)
def test_scale():
 d=tape();assert np.allclose(S._vwma(d.c,d.v).dropna()*2,S._vwma(d.c*2,d.v).dropna())
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
