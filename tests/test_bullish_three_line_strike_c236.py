import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bullish_three_line_strike_c236 as C
def tape(active=True):
 x=np.arange(650.);c=100+.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(43,len(x),53):
  b=c[i-3];o[i-3:i+1]=[b,b+.5,b+1.,b+2.];c[i-3:i+1]=[b+1.,b+1.5,b+2.,b-.2];h[i-3:i+1]=[b+1.1,b+1.6,b+2.1,b+2.1];l[i-3:i+1]=[b-.1,b+.4,b+.9,b-.3]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_bullish_three_line_strike_long_1h(d,S.BULL3STRIKE1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_three_line_strike(pd.Series([10.,10.5,11.,12.]),pd.Series([11.,11.5,12.,9.8])).iloc[-1]
def test_three_rising_lines_required():assert not S._bullish_three_line_strike(pd.Series([10.,10.5,11.,12.]),pd.Series([11.,10.4,12.,9.8])).iloc[-1]
def test_nested_opens_required():assert not S._bullish_three_line_strike(pd.Series([10.,11.2,11.4,12.]),pd.Series([11.,11.5,12.,9.8])).iloc[-1]
def test_strike_open_required():assert not S._bullish_three_line_strike(pd.Series([10.,10.5,11.,11.9]),pd.Series([11.,11.5,12.,9.8])).iloc[-1]
def test_outside_close_required():assert not S._bullish_three_line_strike(pd.Series([10.,10.5,11.,12.]),pd.Series([11.,11.5,12.,10.1])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
