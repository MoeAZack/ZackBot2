import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bullish_harami_cross_c240 as C
def tape(active=True):
 x=np.arange(650.);c=100+.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-1]=b+2;c[i-1]=b;o[i]=b+.95;c[i]=b+1.;h[i]=b+1.4;l[i]=b+.4
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_bullish_harami_cross_long_1h(d,S.BULLHARAMICROSS1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_harami_cross(pd.Series([11.,10.]),pd.Series([11.5,10.6]),pd.Series([8.5,9.6]),pd.Series([9.,10.05])).iloc[-1]
def test_prior_bearish_required():assert not S._bullish_harami_cross(pd.Series([9.,10.]),pd.Series([11.5,10.6]),pd.Series([8.5,9.6]),pd.Series([11.,10.05])).iloc[-1]
def test_doji_required():assert not S._bullish_harami_cross(pd.Series([11.,9.5]),pd.Series([11.5,10.6]),pd.Series([8.5,9.4]),pd.Series([9.,10.4])).iloc[-1]
def test_inside_body_required():assert not S._bullish_harami_cross(pd.Series([11.,8.9]),pd.Series([11.5,10.1]),pd.Series([8.5,8.8]),pd.Series([9.,9.])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
