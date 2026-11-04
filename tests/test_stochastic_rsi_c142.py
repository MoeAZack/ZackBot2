import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_stochastic_rsi_c142 as C
def tape(down=False):
 x=np.arange(500.);c=180-.1*x if down else 100+.04*x+4*np.sin(x/9);o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.e200 if down else d.c-1.;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_stochastic_rsi_recovery_long_1h(tape(),S.STOCHRSI1H,{})['le'].sum()>0
def test_down():assert not S.sig_stochastic_rsi_recovery_long_1h(tape(True),S.STOCHRSI1H,{})['le'].any()
def test_scale():
 d=tape();assert np.allclose(S._stochastic_rsi(d.c).dropna(),S._stochastic_rsi(d.c*2).dropna())
def test_cost():
 d=tape();s=S.sig_stochastic_rsi_recovery_long_1h(d,S.STOCHRSI1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_stochastic_rsi_recovery_long_1h(d,S.STOCHRSI1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_stochastic_rsi_recovery_long_1h(d,S.STOCHRSI1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==S.sig_stochastic_rsi_recovery_long_1h(d,S.STOCHRSI1H,{})['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
