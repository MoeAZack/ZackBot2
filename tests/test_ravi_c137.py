import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_ravi_c137 as C
def tape(down=False,n=420):
 x=np.arange(n,dtype=float);c=(180-.14*x)if down else 100+.08*x+4*np.sin(x/12);o=np.r_[c[0],c[:-1]];h=np.maximum(o,c)*1.003;l=np.minimum(o,c)*.997;d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=100000.)));d['atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fires():assert S.sig_ravi_trend_long_1h(tape(),S.RAVI1H,{})['le'].sum()>0
def test_down_no_fire():assert not S.sig_ravi_trend_long_1h(tape(True),S.RAVI1H,{})['le'].any()
def test_scale_invariant():
 d=tape();a,_,_=S._ravi(d.c);b,_,_=S._ravi(d.c*5);assert np.allclose(a.dropna(),b.dropna())
def test_cost_gate():
 d=tape();s=S.sig_ravi_trend_long_1h(d,S.RAVI1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_ravi_trend_long_1h(d,S.RAVI1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_ravi_trend_long_1h(d,S.RAVI1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i+1,'c']*=3;assert bool(s.iloc[i])==bool(S.sig_ravi_trend_long_1h(d,S.RAVI1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
