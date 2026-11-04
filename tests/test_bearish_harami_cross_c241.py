import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bearish_harami_cross_c241 as C
def tape(active=True):
 x=np.arange(650.);c=150-.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-1]=b;c[i-1]=b+2;o[i]=b+1.05;c[i]=b+1.;h[i]=b+1.6;l[i]=b+.6
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_bearish_harami_cross_short_1h(d,S.BEARHARAMICROSS1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():assert S._bearish_harami_cross(pd.Series([9.,10.]),pd.Series([11.5,10.4]),pd.Series([8.5,9.4]),pd.Series([11.,9.95])).iloc[-1]
def test_prior_bullish_required():assert not S._bearish_harami_cross(pd.Series([11.,10.]),pd.Series([11.5,10.4]),pd.Series([8.5,9.4]),pd.Series([9.,9.95])).iloc[-1]
def test_doji_required():assert not S._bearish_harami_cross(pd.Series([9.,10.5]),pd.Series([11.5,10.6]),pd.Series([8.5,9.4]),pd.Series([11.,9.6])).iloc[-1]
def test_inside_body_required():assert not S._bearish_harami_cross(pd.Series([9.,11.05]),pd.Series([11.5,11.1]),pd.Series([8.5,10.1]),pd.Series([11.,11.])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
