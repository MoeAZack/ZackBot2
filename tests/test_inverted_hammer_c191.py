import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_inverted_hammer_c191 as C
def tape(active=True):
 x=np.arange(600.);c=100+.04*x+3*np.sin(x/12) if active else 200-.1*x;o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 if active:
  for i in range(40,len(x),50):o[i]=c[i]-.5;h[i]=c[i]+1.1;l[i]=o[i]-.2
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_inverted_hammer_long_1h(d,S.INVHAMMER1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_inverted_hammer(pd.Series([10.]),pd.Series([13.]),pd.Series([9.5]),pd.Series([11.])).iloc[-1]
def test_short_upper_rejected():assert not S._bullish_inverted_hammer(pd.Series([10.]),pd.Series([11.5]),pd.Series([9.5]),pd.Series([11.])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
