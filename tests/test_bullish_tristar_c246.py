import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bullish_tristar_c246 as C
def tape(active=True):
 x=np.arange(650.);c=100+.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-2:i+1]=[b+.05,b-.45,b+.05];c[i-2:i+1]=[b,b-.5,b];h[i-2:i+1]=[b+.4,b-.1,b+.4];l[i-2:i+1]=[b-.4,b-.9,b-.4]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_bullish_tristar_long_1h(d,S.BULLTRISTAR1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_tristar(pd.Series([10.05,9.55,10.05]),pd.Series([10.4,9.9,10.4]),pd.Series([9.6,9.1,9.6]),pd.Series([10.,9.5,10.])).iloc[-1]
def test_three_doji_required():assert not S._bullish_tristar(pd.Series([10.05,9.8,10.05]),pd.Series([10.4,9.9,10.4]),pd.Series([9.6,9.1,9.6]),pd.Series([10.,9.5,10.])).iloc[-1]
def test_middle_below_both_neighbors():
 assert not S._bullish_tristar(pd.Series([10.05,9.55,10.05]),pd.Series([10.4,9.9,10.4]),pd.Series([9.6,9.6,9.6]),pd.Series([10.,9.5,10.])).iloc[-1]
def test_zero_range_rejected():assert not S._bullish_tristar(pd.Series([10.,9.5,10.]),pd.Series([10.,9.5,10.]),pd.Series([10.,9.5,10.]),pd.Series([10.,9.5,10.])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
