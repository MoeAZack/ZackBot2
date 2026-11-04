import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bullish_breakaway_c231 as C
def tape(active=True):
 x=np.arange(650.);c=100+.08*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(44,len(x),55):
  b=c[i-4];o[i-4:i+1]=[b+2.,b+1.,b+.5,b,b-.3];c[i-4:i+1]=[b+1.,b+.5,b,b-.3,b+1.3];h[i-4:i+1]=[b+2.1,b+1.1,b+.6,b+.1,b+1.4];l[i-4:i+1]=[b+.9,b+.4,b-.1,b-.4,b-.4]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_bullish_breakaway_long_1h(d,S.BREAKAWAY1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_breakaway(pd.Series([12.,11.,10.5,10.,9.7]),pd.Series([12.1,11.1,10.6,10.1,11.4]),pd.Series([10.9,10.4,9.9,9.6,9.6]),pd.Series([11.,10.5,10.,9.7,11.3])).iloc[-1]
def test_staircase_required():assert not S._bullish_breakaway(pd.Series([12.,11.,10.5,10.,9.7]),pd.Series([12.1,11.1,10.6,10.1,11.4]),pd.Series([10.9,10.4,10.5,9.6,9.6]),pd.Series([11.,10.5,10.,9.7,11.3])).iloc[-1]
def test_reclaim_required():assert not S._bullish_breakaway(pd.Series([12.,11.,10.5,10.,9.7]),pd.Series([12.1,11.1,10.6,10.1,10.9]),pd.Series([10.9,10.4,9.9,9.6,9.6]),pd.Series([11.,10.5,10.,9.7,10.9])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
