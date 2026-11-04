import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bullish_abandoned_baby_c248 as C
def tape(active=True):
 x=np.arange(650.);c=100+.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-2:i+1]=[b+2,b+.4,b+.9];c[i-2:i+1]=[b+1,b+.42,b+1.7];h[i-2:i+1]=[b+2.2,b+.6,b+1.8];l[i-2:i+1]=[b+.8,b+.2,b+.7]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_bullish_abandoned_baby_long_1h(d,S.BULLABANDONED1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_abandoned_baby(pd.Series([12.,10.4,10.9]),pd.Series([12.2,10.6,11.8]),pd.Series([10.8,10.2,10.7]),pd.Series([11.,10.42,11.7])).iloc[-1]
def test_middle_doji_required():assert not S._bullish_abandoned_baby(pd.Series([12.,10.2,10.9]),pd.Series([12.2,10.6,11.8]),pd.Series([10.8,10.1,10.7]),pd.Series([11.,10.5,11.7])).iloc[-1]
def test_true_gap_down_required():assert not S._bullish_abandoned_baby(pd.Series([12.,10.7,10.9]),pd.Series([12.2,10.9,11.8]),pd.Series([10.8,10.5,11.]),pd.Series([11.,10.72,11.7])).iloc[-1]
def test_true_gap_up_required():assert not S._bullish_abandoned_baby(pd.Series([12.,10.4,10.5]),pd.Series([12.2,10.6,11.8]),pd.Series([10.8,10.2,10.5]),pd.Series([11.,10.42,11.7])).iloc[-1]
def test_midpoint_recovery_required():assert not S._bullish_abandoned_baby(pd.Series([12.,10.4,10.9]),pd.Series([12.2,10.6,11.4]),pd.Series([10.8,10.2,10.7]),pd.Series([11.,10.42,11.4])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
