import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_chaikin_oscillator_c143 as C
def tape(down=False):
 x=np.arange(420.);c=180-.1*x if down else 100+.05*x+3*np.sin(x/7);o=np.r_[c[0],c[:-1]];v=1000+200*np.sin(x/11);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.004,l=np.minimum(o,c)*.996,c=c,v=v,qv=v*c)));d['atr']=d.c*.02;d['e20']=d.e20 if down else d.c-1.;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_chaikin_oscillator_long_1h(tape(),S.CHAIKINOSC1H,{})['le'].sum()>0
def test_down():assert not S.sig_chaikin_oscillator_long_1h(tape(True),S.CHAIKINOSC1H,{})['le'].any()
def test_scale():
 d=tape();a=S._chaikin_oscillator(d.h,d.l,d.c,d.v);b=S._chaikin_oscillator(d.h*2,d.l*2,d.c*2,d.v);assert np.allclose(a.dropna(),b.dropna())
def test_cost():
 d=tape();s=S.sig_chaikin_oscillator_long_1h(d,S.CHAIKINOSC1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_chaikin_oscillator_long_1h(d,S.CHAIKINOSC1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_chaikin_oscillator_long_1h(d,S.CHAIKINOSC1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==S.sig_chaikin_oscillator_long_1h(d,S.CHAIKINOSC1H,{})['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
