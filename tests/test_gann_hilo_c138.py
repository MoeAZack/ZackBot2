import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_gann_hilo_c138 as C
def tape(down=False,n=360):
 x=np.arange(n,dtype=float);c=180-.14*x if down else 100+.1*x+3*np.sin(x/8);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997;d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=100000.)));d['atr']=d.c*.02;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_gann_hilo_long_1h(tape(),S.GANN_HILO1H,{})['le'].sum()>0
def test_down():assert not S.sig_gann_hilo_long_1h(tape(True),S.GANN_HILO1H,{})['le'].any()
def test_state_values():assert set(S._gann_hilo_state(tape().h,tape().l,tape().c)[0].unique())<={-1.,0.,1.}
def test_cost():
 d=tape();s=S.sig_gann_hilo_long_1h(d,S.GANN_HILO1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_gann_hilo_long_1h(d,S.GANN_HILO1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_gann_hilo_long_1h(d,S.GANN_HILO1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i+1,'c']*=3;assert bool(s.iloc[i])==bool(S.sig_gann_hilo_long_1h(d,S.GANN_HILO1H,{})['le'].iloc[i])
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
