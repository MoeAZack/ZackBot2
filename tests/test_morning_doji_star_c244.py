import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_morning_doji_star_c244 as C
def tape(active=True):
 x=np.arange(650.);c=100+.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-2:i+1]=[b+1,b-.05,b-.1];c[i-2:i+1]=[b,b,b+.7];h[i-2:i+1]=[b+1.1,b+.4,b+.8];l[i-2:i+1]=[b-.1,b-.6,b-.2]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_morning_doji_star_long_1h(d,S.MORNINGDOJISTAR1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._morning_doji_star(pd.Series([11.,9.95,9.9]),pd.Series([11.2,10.4,10.8]),pd.Series([9.8,9.4,9.8]),pd.Series([10.,10.,10.7])).iloc[-1]
def test_first_bearish_required():assert not S._morning_doji_star(pd.Series([10.,9.95,9.9]),pd.Series([11.2,10.4,10.8]),pd.Series([9.8,9.4,9.8]),pd.Series([11.,10.,10.7])).iloc[-1]
def test_middle_doji_required():assert not S._morning_doji_star(pd.Series([11.,9.7,9.9]),pd.Series([11.2,10.4,10.8]),pd.Series([9.8,9.4,9.8]),pd.Series([10.,10.,10.7])).iloc[-1]
def test_midpoint_recovery_required():assert not S._morning_doji_star(pd.Series([11.,9.95,9.9]),pd.Series([11.2,10.4,10.5]),pd.Series([9.8,9.4,9.8]),pd.Series([10.,10.,10.4])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
