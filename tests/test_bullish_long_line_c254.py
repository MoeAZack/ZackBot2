import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_bullish_long_line_c254 as C
def tape(active=True):
 x=np.arange(650.);c=100+.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i];o[i]=b;c[i]=b+1.;h[i]=b+1.1;l[i]=b-.1
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.c+1;return d
def signal(d):return S.sig_bullish_long_line_long_1h(d,S.BULLLONGLINE1H,{})
def pattern(o,h,l,c):return S._bullish_long_line(pd.Series(o),pd.Series(h),pd.Series(l),pd.Series(c)).iloc[-1]
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_trend_filter():assert not signal(tape(False))['le'].any()
def test_geometry():assert pattern([10.],[11.1],[9.9],[11.])
def test_bullish_body_required():assert not pattern([11.],[11.1],[9.9],[10.])
def test_dominant_body_required():assert not pattern([10.],[11.],[9.],[10.6])
def test_nonzero_shadows_allowed():assert pattern([10.],[11.1],[9.9],[11.])
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
