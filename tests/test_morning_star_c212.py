import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_morning_star_c212 as C
def tape(active=True):
 x=np.arange(600.);c=100+.1*x+3*np.sin(x/12) if active else 200-.04*x+3*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 if active:
  for i in range(42,len(x),50):
   b=c[i-2];o[i-2:i+1]=[b+1,b-.05,b-.1];c[i-2:i+1]=[b,b-.1,b+.7];h[i-2:i+1]=[b+1.1,b,b+.8];l[i-2:i+1]=[b-.1,b-.2,b-.2]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_morning_star_long_1h(d,S.MORNINGSTAR1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._morning_star(pd.Series([11.,9.95,9.9]),pd.Series([10.,9.9,10.7])).iloc[-1]
def test_large_middle_body_rejected():assert not S._morning_star(pd.Series([11.,9.95,9.9]),pd.Series([10.,9.5,10.7])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
