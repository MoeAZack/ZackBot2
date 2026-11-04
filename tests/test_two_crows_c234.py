import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_two_crows_c234 as C
def tape(active=True):
 x=np.arange(650.);c=150-.06*x+2*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997
 for i in range(42,len(x),47):
  b=c[i-2];o[i-2:i+1]=[b,b+3.,b+2.7];c[i-2:i+1]=[b+2.,b+2.5,b+1.];h[i-2:i+1]=[b+2.1,b+3.1,b+2.8];l[i-2:i+1]=[b-.1,b+2.4,b+.9]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c+1 if active else d.c-1;return d
def signal(d):return S.sig_two_crows_short_1h(d,S.TWOCROWS1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['se'].sum()>0
def test_trend_filter():assert not signal(tape(False))['se'].any()
def test_geometry():assert S._two_crows(pd.Series([10.,13.,12.7]),pd.Series([12.,12.5,11.])).iloc[-1]
def test_gap_body_required():assert not S._two_crows(pd.Series([10.,13.,12.7]),pd.Series([12.,11.9,11.])).iloc[-1]
def test_final_open_inside_crow_required():assert not S._two_crows(pd.Series([10.,13.,13.1]),pd.Series([12.,12.5,11.])).iloc[-1]
def test_reversal_into_first_body_required():assert not S._two_crows(pd.Series([10.,13.,12.7]),pd.Series([12.,12.5,12.1])).iloc[-1]
def test_initial_open_must_hold():assert not S._two_crows(pd.Series([10.,13.,12.7]),pd.Series([12.,12.5,9.9])).iloc[-1]
def test_cost():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['se'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['se'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['se'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
