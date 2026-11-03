import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_directional_sign_entropy_c42 as C
def tape(n=300):
 r=np.tile([.004,.003,.002,-.001],n//4+1)[:n];c=100*np.exp(np.cumsum(r));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[c[0],c[:-1]],h=c+1,l=c-1,c=c,v=1000.,qv=100000.)));d.loc[:,'atr']=2.;d.loc[:,'e20']=d.c*.99;d.loc[:,'qv']=100000.;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_signal():
 d=tape();i=249;assert bool(S.sig_directional_sign_entropy_long_1h(d,S.DSEL1H,{})['le'].iloc[i])
def test_balanced_rejected():
 d=tape();i=250;c=d.c.iloc[i-30];r=np.tile([.003,-.003],15);d.loc[d.index[i-29:i+1],'c']=c*np.exp(np.cumsum(r));assert not bool(S.sig_directional_sign_entropy_long_1h(d,S.DSEL1H,{})['le'].iloc[i])
def test_volume_causal():
 d=tape();i=249;d.loc[i,'qv']=110000.;a=S.sig_directional_sign_entropy_long_1h(d,S.DSEL1H,{})['le'].iloc[i];d.loc[i,'qv']=100000.;b=S.sig_directional_sign_entropy_long_1h(d,S.DSEL1H,{})['le'].iloc[i];assert bool(a) and bool(b)
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
