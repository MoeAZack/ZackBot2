import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bullish_kicker_c199 as C
def tape(active=True):
 x=np.arange(600.);c=100+.1*x+3*np.sin(x/12) if active else 200-.04*x+3*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 if active:
  for i in range(41,len(x),50):
   b=c[i-1];o[i-1:i+1]=[b+1,b+1.2];c[i-1:i+1]=[b,b+2.2];h[i-1:i+1]=[b+1.1,b+2.3];l[i-1:i+1]=[b-.1,b+1.1]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_bullish_kicker_long_1h(d,S.BULLKICK1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_kicker(pd.Series([11.,11.2]),pd.Series([11.1,12.3]),pd.Series([9.9,11.1]),pd.Series([10.,12.2])).iloc[-1]
def test_no_gap_rejected():assert not S._bullish_kicker(pd.Series([11.,10.5]),pd.Series([11.1,11.6]),pd.Series([9.9,10.4]),pd.Series([10.,11.5])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
