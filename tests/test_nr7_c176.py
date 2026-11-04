import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_nr7_c176 as C
def tape(active=True):
 x=np.arange(500.);c=100+.03*x+2*np.sin(x/10)+(np.where((x%50)==21,5,0) if active else 0) if active else 200-.1*x;o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997;mask=(x%50)==20;h[mask]=c[mask]*1.0001;l[mask]=c[mask]*.9999;d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_nr7_breakout_long_1h(d,S.NR71H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_formula():
 d=tape();r=d.h-d.l;assert S._nr7_setup(d.h,d.l).equals(r.eq(r.rolling(7,min_periods=7).min()))
def test_scale():
 d=tape();assert S._nr7_setup(d.h,d.l).equals(S._nr7_setup(d.h*2,d.l*2))
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
