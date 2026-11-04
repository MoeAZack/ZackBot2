import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_mat_hold_c230 as C
def tape(active=True):
 x=np.arange(650.);c=100+.08*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(44,len(x),55):
  b=c[i-4];o[i-4:i+1]=[b,b+2.,b+1.7,b+1.4,b+1.2];c[i-4:i+1]=[b+2.,b+1.7,b+1.4,b+1.2,b+2.3];h[i-4:i+1]=[b+2.1,b+2.05,b+1.8,b+1.5,b+2.4];l[i-4:i+1]=[b-.1,b+1.6,b+1.3,b+1.1,b+1.1]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_mat_hold_long_1h(d,S.MATHOLD1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._mat_hold(pd.Series([10.,12.,11.7,11.4,11.2]),pd.Series([12.1,12.05,11.8,11.5,12.4]),pd.Series([9.9,11.6,11.3,11.1,11.1]),pd.Series([12.,11.7,11.4,11.2,12.3])).iloc[-1]
def test_upper_half_hold_required():assert not S._mat_hold(pd.Series([10.,12.,11.7,11.4,11.2]),pd.Series([12.1,12.05,11.8,11.5,12.4]),pd.Series([9.9,11.6,10.9,11.1,11.1]),pd.Series([12.,11.7,11.4,11.2,12.3])).iloc[-1]
def test_breakout_required():assert not S._mat_hold(pd.Series([10.,12.,11.7,11.4,11.2]),pd.Series([12.1,12.05,11.8,11.5,12.4]),pd.Series([9.9,11.6,11.3,11.1,11.1]),pd.Series([12.,11.7,11.4,11.2,12.0])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
