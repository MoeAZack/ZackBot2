import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_psychological_line_c140 as C
def tape(down=False):
 x=np.arange(360.);c=180-.1*x if down else 100+.06*x+2*np.sin(x/3);o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=360,freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e20']=d.e20 if down else d.c-1.;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_psychological_line_long_1h(tape(),S.PSY1H,{})['le'].sum()>0
def test_down():assert not S.sig_psychological_line_long_1h(tape(True),S.PSY1H,{})['le'].any()
def test_scale():
 d=tape();assert np.allclose(S._psychological_line(d.c).dropna(),S._psychological_line(d.c*2).dropna())
def test_cost():
 d=tape();s=S.sig_psychological_line_long_1h(d,S.PSY1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_psychological_line_long_1h(d,S.PSY1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_psychological_line_long_1h(d,S.PSY1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==S.sig_psychological_line_long_1h(d,S.PSY1H,{})['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
