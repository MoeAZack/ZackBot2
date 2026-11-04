import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_hammer_c181 as C
def tape(active=True):
 x=np.arange(600.);c=100+.04*x+3*np.sin(x/12) if active else 200-.1*x;o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997;mask=(x%40)==20;o[mask]=c[mask]-.2;h[mask]=c[mask]+.1;l[mask]=o[mask]-.8;d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_bullish_hammer_long_1h(d,S.HAMMER1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_geometry():assert S._bullish_hammer(pd.Series([10.]),pd.Series([10.3]),pd.Series([9.4]),pd.Series([10.2])).iloc[0]
def test_upper_wick_rejected():assert not S._bullish_hammer(pd.Series([10.]),pd.Series([10.6]),pd.Series([9.4]),pd.Series([10.2])).iloc[0]
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
