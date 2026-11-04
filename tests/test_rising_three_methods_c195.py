import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_rising_three_methods_c195 as C
def tape(active=True):
 x=np.arange(700.);c=100+.08*x+2*np.sin(x/13) if active else 200-.04*x+2*np.sin(x/13);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 if active:
  for i in range(44,len(x),60):
   b=c[i-4];o[i-4:i+1]=[b,b+1.8,b+1.6,b+1.4,b+1.2];c[i-4:i+1]=[b+2,b+1.5,b+1.3,b+1.1,b+2.8];h[i-4:i+1]=[b+2.2,b+2,b+1.9,b+1.8,b+3];l[i-4:i+1]=[b-.2,b+.8,b+.7,b+.6,b+1]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_rising_three_methods_long_1h(d,S.RISING3M1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():
 o=pd.Series([10.,11.8,11.6,11.4,11.2]);h=pd.Series([12.2,12.,11.9,11.8,13.]);l=pd.Series([9.8,10.8,10.7,10.6,11.]);c=pd.Series([12.,11.5,11.3,11.1,12.8]);assert S._rising_three_methods(o,h,l,c).iloc[-1]
def test_escape_rejected():
 d=tape();i=np.flatnonzero(signal(d)['le'])[-1];d.loc[i-2,'h']=d.loc[i-4,'h']+1;assert not signal(d)['le'].iloc[i]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
