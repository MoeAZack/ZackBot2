import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_falling_three_methods_c196 as C
def tape(active=True):
 x=np.arange(700.);c=200-.08*x+2*np.sin(x/13) if active else 100+.04*x+2*np.sin(x/13);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 if active:
  for i in range(44,len(x),60):
   b=c[i-4];o[i-4:i+1]=[b+2,b+.2,b+.4,b+.6,b+.8];c[i-4:i+1]=[b,b+.5,b+.7,b+.9,b-.8];h[i-4:i+1]=[b+2.2,b+1.2,b+1.3,b+1.4,b+1];l[i-4:i+1]=[b-.2,b+.1,b+.2,b+.3,b-1]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_falling_three_methods_short_1h(d,S.FALLING3M1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():
 o=pd.Series([12.,10.2,10.4,10.6,10.8]);h=pd.Series([12.2,11.2,11.3,11.4,11.]);l=pd.Series([9.8,10.1,10.2,10.3,9.]);c=pd.Series([10.,10.5,10.7,10.9,9.2]);assert S._falling_three_methods(o,h,l,c).iloc[-1]
def test_escape_rejected():
 d=tape();i=np.flatnonzero(signal(d)['se'])[-1];d.loc[i-2,'l']=d.loc[i-4,'l']-1;assert not signal(d)['se'].iloc[i]
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=.3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
