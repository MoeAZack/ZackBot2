import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bearish_three_line_strike_c237 as C
def tape(active=True):
 x=np.arange(650.);c=150-.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(43,len(x),53):
  b=c[i-3];o[i-3:i+1]=[b+2.,b+1.5,b+1.,b];c[i-3:i+1]=[b+1.,b+.5,b,b+2.2];h[i-3:i+1]=[b+2.1,b+1.6,b+1.1,b+2.3];l[i-3:i+1]=[b+.9,b+.4,b-.1,b-.1]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_bearish_three_line_strike_short_1h(d,S.BEAR3STRIKE1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():assert S._bearish_three_line_strike(pd.Series([12.,11.5,11.,10.]),pd.Series([11.,10.5,10.,12.2])).iloc[-1]
def test_three_falling_lines_required():assert not S._bearish_three_line_strike(pd.Series([12.,11.5,11.,10.]),pd.Series([11.,11.6,10.,12.2])).iloc[-1]
def test_nested_opens_required():assert not S._bearish_three_line_strike(pd.Series([12.,10.8,10.6,10.]),pd.Series([11.,10.5,10.,12.2])).iloc[-1]
def test_strike_open_required():assert not S._bearish_three_line_strike(pd.Series([12.,11.5,11.,10.1]),pd.Series([11.,10.5,10.,12.2])).iloc[-1]
def test_outside_close_required():assert not S._bearish_three_line_strike(pd.Series([12.,11.5,11.,10.]),pd.Series([11.,10.5,10.,11.9])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
