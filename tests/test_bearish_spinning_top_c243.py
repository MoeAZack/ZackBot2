import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bearish_spinning_top_c243 as C
def tape(active=True):
 x=np.arange(650.);c=150-.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i]=b+.2;c[i]=b;h[i]=b+.5;l[i]=b-.3
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_bearish_spinning_top_short_1h(d,S.BEARSPINTOP1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():assert S._bearish_spinning_top(pd.Series([10.2]),pd.Series([10.5]),pd.Series([9.7]),pd.Series([10.])).iloc[-1]
def test_bearish_body_required():assert not S._bearish_spinning_top(pd.Series([10.]),pd.Series([10.5]),pd.Series([9.7]),pd.Series([10.2])).iloc[-1]
def test_small_body_required():assert not S._bearish_spinning_top(pd.Series([10.5]),pd.Series([10.7]),pd.Series([9.7]),pd.Series([10.])).iloc[-1]
def test_two_sided_shadows_required():assert not S._bearish_spinning_top(pd.Series([10.2]),pd.Series([10.3]),pd.Series([9.7]),pd.Series([10.])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
