import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_ladder_bottom_c224 as C
def tape(active=True):
 x=np.arange(600.);c=100+.08*x+3*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(44,len(x),50):
  b=c[i-4];o[i-4:i+1]=[b+1,b+.7,b+.4,b+.1,b-.4];c[i-4:i+1]=[b+.6,b+.3,b,b-.3,b+.7];h[i-4:i+1]=[b+1.1,b+.8,b+.5,b+.5,b+.8];l[i-4:i+1]=[b+.5,b+.2,b-.1,b-.4,b-.5]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_ladder_bottom_long_1h(d,S.LADDERBOTTOM1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._ladder_bottom(pd.Series([11.,10.7,10.4,10.1,9.6]),pd.Series([11.1,10.8,10.5,10.5,10.8]),pd.Series([10.6,10.3,10.,9.7,10.7])).iloc[-1]
def test_no_high_reclaim_rejected():assert not S._ladder_bottom(pd.Series([11.,10.7,10.4,10.1,9.6]),pd.Series([11.1,10.8,10.5,10.5,10.8]),pd.Series([10.6,10.3,10.,9.7,10.4])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
