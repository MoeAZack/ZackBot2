import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_ssl_channel_c173 as C
def tape(active=True):
 x=np.arange(500.);c=100+.04*x+4*np.sin(x/12) if active else 200-.1*x;o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=len(x),freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=1e5)));d['atr']=d.c*.02;d['e200']=d.c-1 if active else d.e200;return d
def signal(d):return S.sig_ssl_channel_long_1h(d,S.SSL1H,{})
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert signal(tape())['le'].sum()>0
def test_down():assert not signal(tape(False))['le'].any()
def test_state_and_lines():
 d=tape();state,up,down=S._ssl_channel(d.h,d.l,d.c);mh=d.h.rolling(10,min_periods=10).mean();ml=d.l.rolling(10,min_periods=10).mean();assert ((state[state>0]==1)&(up[state>0]==mh[state>0])&(down[state>0]==ml[state>0])).all()
def test_scale():
 d=tape();a=S._ssl_channel(d.h,d.l,d.c);b=S._ssl_channel(d.h*2,d.l*2,d.c*2);assert a[0].equals(b[0]) and np.allclose(a[1].dropna()*2,b[1].dropna())
def test_cost():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not signal(d)['le'].iloc[i]
def test_causal():
 d=tape();s=signal(d)['le'];i=np.flatnonzero(s.iloc[:-1])[-1];d.loc[i+1,'c']*=3;assert s.iloc[i]==signal(d)['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
