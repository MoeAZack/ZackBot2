import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bearish_abandoned_baby_c249 as C
def tape(active=True):
 x=np.arange(650.);c=150-.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-2:i+1]=[b,b+1.6,b+1.1];c[i-2:i+1]=[b+1,b+1.62,b+.3];h[i-2:i+1]=[b+1.2,b+1.8,b+1.3];l[i-2:i+1]=[b-.2,b+1.4,b+.2]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_bearish_abandoned_baby_short_1h(d,S.BEARABANDONED1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():assert S._bearish_abandoned_baby(pd.Series([10.,11.6,11.1]),pd.Series([11.2,11.8,11.3]),pd.Series([9.8,11.4,10.2]),pd.Series([11.,11.62,10.3])).iloc[-1]
def test_middle_doji_required():assert not S._bearish_abandoned_baby(pd.Series([10.,11.4,11.1]),pd.Series([11.2,11.8,11.3]),pd.Series([9.8,11.3,10.2]),pd.Series([11.,11.7,10.3])).iloc[-1]
def test_true_gap_up_required():assert not S._bearish_abandoned_baby(pd.Series([10.,11.1,11.1]),pd.Series([11.2,11.3,11.3]),pd.Series([9.8,11.,10.2]),pd.Series([11.,11.12,10.3])).iloc[-1]
def test_true_gap_down_required():assert not S._bearish_abandoned_baby(pd.Series([10.,11.6,11.5]),pd.Series([11.2,11.8,11.5]),pd.Series([9.8,11.4,10.2]),pd.Series([11.,11.62,10.3])).iloc[-1]
def test_midpoint_reversal_required():assert not S._bearish_abandoned_baby(pd.Series([10.,11.6,11.1]),pd.Series([11.2,11.8,11.3]),pd.Series([9.8,11.4,10.6]),pd.Series([11.,11.62,10.6])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
