import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_percentage_volume_oscillator_c146 as C
def tape(active=True):
 x=np.arange(420.);c=100+.05*x+2*np.sin(x/9) if active else 180-.1*x;o=np.r_[c[0],c[:-1]];v=1000+5*x+100*np.sin(x/20) if active else np.full(len(x),1000.);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=v,qv=v*c)));d['atr']=d.c*.02;d['e20']=d.c-1 if active else d.e20;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_percentage_volume_oscillator_long_1h(tape(),S.PVO1H,{})['le'].sum()>0
def test_down():assert not S.sig_percentage_volume_oscillator_long_1h(tape(False),S.PVO1H,{})['le'].any()
def test_scale():
 d=tape();a,b=S._percentage_volume_oscillator(d.v);c,e=S._percentage_volume_oscillator(d.v*3);assert np.allclose(a.dropna(),c.dropna()) and np.allclose(b.dropna(),e.dropna())
def test_cost():
 d=tape();s=S.sig_percentage_volume_oscillator_long_1h(d,S.PVO1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_percentage_volume_oscillator_long_1h(d,S.PVO1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_percentage_volume_oscillator_long_1h(d,S.PVO1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i+1,'v']*=20;assert s.iloc[i]==S.sig_percentage_volume_oscillator_long_1h(d,S.PVO1H,{})['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
