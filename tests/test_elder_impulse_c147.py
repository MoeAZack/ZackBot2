import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_elder_impulse_c147 as C
def tape(active=True):
 x=np.arange(500.);c=100+.05*x+3*np.sin(x/10) if active else 180-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_elder_impulse_long_1h(tape(),S.EIMP1H,{})['le'].sum()>0
def test_down():assert not S.sig_elder_impulse_long_1h(tape(False),S.EIMP1H,{})['le'].any()
def test_scale():
 d=tape();a=S.sig_elder_impulse_long_1h(d,S.EIMP1H,{})['le'];z=d.copy();z[['o','h','l','c','atr','e200']]*=2;b=S.sig_elder_impulse_long_1h(z,S.EIMP1H,{})['le'];assert a.equals(b)
def test_cost():
 d=tape();s=S.sig_elder_impulse_long_1h(d,S.EIMP1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_elder_impulse_long_1h(d,S.EIMP1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_elder_impulse_long_1h(d,S.EIMP1H,{})['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==S.sig_elder_impulse_long_1h(d,S.EIMP1H,{})['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
