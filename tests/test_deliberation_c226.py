import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_deliberation_c226 as C
def tape(active=True):
 x=np.arange(600.);c=200-.08*x+3*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),50):
  b=c[i-2];o[i-2:i+1]=[b,b+.6,b+1.2];c[i-2:i+1]=[b+.6,b+1.2,b+1.45];h[i-2:i+1]=c[i-2:i+1]+.1;l[i-2:i+1]=o[i-2:i+1]-.1
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_deliberation_short_1h(d,S.DELIBERATION1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():assert S._deliberation(pd.Series([10.,10.6,11.2]),pd.Series([10.6,11.2,11.45])).iloc[-1]
def test_large_third_body_rejected():assert not S._deliberation(pd.Series([10.,10.6,11.2]),pd.Series([10.6,11.2,11.6])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
