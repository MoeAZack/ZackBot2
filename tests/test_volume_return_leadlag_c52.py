import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_volume_return_leadlag_c52 as C
def tape(sign=1.0,jump=.60,n=500):
 x=np.resize(np.array([.20,-.20,.10,-.10]),n);x[400]=jump
 qv=100000.*np.exp(np.cumsum(x));r=np.zeros(n);r[1:]=sign*.02*x[:-1]
 c=100.*np.exp(np.cumsum(r));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=qv)))
 d.loc[:,'atr']=d.c*.02;return d
def test_default_off_and_family_count():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_volume_return_leadlag():
 d=tape();assert bool(S.sig_volume_return_leadlag_long_1h(d,S.VRL1H,{})['le'].iloc[400])
def test_negative_predictive_relation_is_rejected():
 d=tape(-1.0);assert not bool(S.sig_volume_return_leadlag_long_1h(d,S.VRL1H,{})['le'].iloc[400])
def test_current_volume_jump_is_required():
 d=tape(jump=.10);assert not bool(S.sig_volume_return_leadlag_long_1h(d,S.VRL1H,{})['le'].iloc[400])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
