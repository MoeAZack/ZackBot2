import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_chande_kroll_stop_c148 as C
def tape(active=True):
 x=np.arange(500.);c=100+.06*x+3*np.sin(x/11) if active else 180-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e20']=d.c-1 if active else d.e20;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_chande_kroll_breakout_long_1h(tape(),S.CKS1H,{})['le'].sum()>0
def test_down():assert not S.sig_chande_kroll_breakout_long_1h(tape(False),S.CKS1H,{})['le'].any()
def test_scale():
 d=tape();a,b=S._chande_kroll_stops(d.h,d.l,d.c);x,y=S._chande_kroll_stops(d.h*2,d.l*2,d.c*2);assert np.allclose(a.dropna()*2,x.dropna()) and np.allclose(b.dropna()*2,y.dropna())
def test_cost():
 d=tape();s=S.sig_chande_kroll_breakout_long_1h(d,S.CKS1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_chande_kroll_breakout_long_1h(d,S.CKS1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_chande_kroll_breakout_long_1h(d,S.CKS1H,{})['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==S.sig_chande_kroll_breakout_long_1h(d,S.CKS1H,{})['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
