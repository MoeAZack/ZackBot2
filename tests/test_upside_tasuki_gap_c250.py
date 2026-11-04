import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_upside_tasuki_gap_c250 as C
def tape(active=True):
 x=np.arange(650.);c=100+.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i-2:i+1]=[b,b+1.4,b+1.8];c[i-2:i+1]=[b+1,b+2,b+1.2];h[i-2:i+1]=[b+1.1,b+2.1,b+1.9];l[i-2:i+1]=[b-.1,b+1.3,b+1.15]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_upside_tasuki_gap_long_1h(d,S.UPTASUKI1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._upside_tasuki_gap(pd.Series([10.,11.4,11.8]),pd.Series([11.1,12.1,11.9]),pd.Series([9.9,11.3,11.15]),pd.Series([11.,12.,11.2])).iloc[-1]
def test_two_bullish_lead_candles_required():assert not S._upside_tasuki_gap(pd.Series([11.,11.4,11.8]),pd.Series([11.1,12.1,11.9]),pd.Series([9.9,11.3,11.15]),pd.Series([10.,12.,11.2])).iloc[-1]
def test_true_range_gap_required():assert not S._upside_tasuki_gap(pd.Series([10.,11.1,11.8]),pd.Series([11.1,12.1,11.9]),pd.Series([9.9,11.,11.15]),pd.Series([11.,12.,11.05])).iloc[-1]
def test_bearish_pullback_required():assert not S._upside_tasuki_gap(pd.Series([10.,11.4,11.8]),pd.Series([11.1,12.1,12.]),pd.Series([9.9,11.3,11.15]),pd.Series([11.,12.,11.9])).iloc[-1]
def test_pullback_open_inside_second_body():assert not S._upside_tasuki_gap(pd.Series([10.,11.4,12.1]),pd.Series([11.1,12.1,12.2]),pd.Series([9.9,11.3,11.15]),pd.Series([11.,12.,11.2])).iloc[-1]
def test_partial_gap_fill_must_hold_prior_high():assert not S._upside_tasuki_gap(pd.Series([10.,11.4,11.8]),pd.Series([11.1,12.1,11.9]),pd.Series([9.9,11.3,11.]),pd.Series([11.,12.,11.])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
