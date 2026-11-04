import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_concealing_baby_swallow_c229 as C
def tape(active=True):
 x=np.arange(600.);c=100+.08*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(43,len(x),50):
  b=c[i-3];o[i-3:i+1]=[b+2.,b+1.,b-.1,b+.6];c[i-3:i+1]=[b+1.,b,b-.5,b-.7];h[i-3:i+1]=[b+2.1,b+1.1,b+.5,b+.7];l[i-3:i+1]=[b+.9,b-.1,b-.6,b-.8]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_concealing_baby_swallow_long_1h(d,S.CONCEALBABY1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._concealing_baby_swallow(pd.Series([12.,11.,9.9,10.6]),pd.Series([12.1,11.1,10.5,10.7]),pd.Series([10.9,9.9,9.4,9.2]),pd.Series([11.,10.,9.5,9.3])).iloc[-1]
def test_shadow_probe_required():assert not S._concealing_baby_swallow(pd.Series([12.,11.,9.9,10.6]),pd.Series([12.1,11.1,9.95,10.7]),pd.Series([10.9,9.9,9.4,9.2]),pd.Series([11.,10.,9.5,9.3])).iloc[-1]
def test_outside_engulf_required():assert not S._concealing_baby_swallow(pd.Series([12.,11.,9.9,10.4]),pd.Series([12.1,11.1,10.5,10.7]),pd.Series([10.9,9.9,9.4,9.2]),pd.Series([11.,10.,9.5,9.3])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
