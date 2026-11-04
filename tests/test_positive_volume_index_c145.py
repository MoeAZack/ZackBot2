import os,sys,numpy as np,pandas as pd,strategies as S
sys.path.insert(0,os.path.join(os.path.dirname(os.path.dirname(__file__)),'tools','research'));import run_positive_volume_index_c145 as C
def tape(up=True,n=380):
 c=np.r_[np.linspace(120,90,n-70),np.linspace(92,145,70)] if up else np.linspace(120,80,n);q=np.full(n,100000.);q[-70:]=np.linspace(100001,250000,70) if up else q[-70:];o=np.r_[c[0],c[:-1]];d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=o,h=np.maximum(o,c)*1.003,l=np.minimum(o,c)*.997,c=c,v=1000.,qv=q)));d['atr']=d.c*.02;d['e20']=d.c-1 if up else d.e20;return d
def test_off():assert S.STRATEGIES[C.KEY]['research_only']
def test_fire():assert S.sig_positive_volume_index_long_1h(tape(),S.PVI1H,{})['le'].sum()>0
def test_down():assert not S.sig_positive_volume_index_long_1h(tape(False),S.PVI1H,{})['le'].any()
def test_scale():
 d=tape();assert np.allclose(S._positive_volume_index(d.c,d.qv),S._positive_volume_index(d.c*2,d.qv*3))
def test_cost():
 d=tape();s=S.sig_positive_volume_index_long_1h(d,S.PVI1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i,'atr']=d.loc[i,'c']*.001;assert not S.sig_positive_volume_index_long_1h(d,S.PVI1H,{})['le'].iloc[i]
def test_causal():
 d=tape();s=S.sig_positive_volume_index_long_1h(d,S.PVI1H,{})['le'];i=np.flatnonzero(s)[-1];d.loc[i+1,['c','qv']]=[d.c.iloc[i]*2,d.qv.iloc[i]*3];assert s.iloc[i]==S.sig_positive_volume_index_long_1h(d,S.PVI1H,{})['le'].iloc[i]
def test_import():assert C.V.KEY=='volume_pressure_inventory_1h'
