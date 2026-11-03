import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_state_persistence_c44 as C
def tape(n=320):
 c=100+np.arange(n,dtype=float)*.5
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[c[0],c[:-1]],h=c+1,l=c-1,c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=2.;d.loc[:,'e20']=d.c-2.;d.loc[:,'e50']=d.c-4.;d.loc[:,'qv']=100000.;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_persistent_bull_state_enters():
 d=tape();assert bool(S.sig_state_persistence_long_1h(d,S.STPER1H,{})['le'].iloc[250])
def test_current_transition_is_excluded_from_estimate():
 d=tape();a=S.sig_state_persistence_long_1h(d,S.STPER1H,{})['le'].copy();d.loc[250,'c']+=1000;b=S.sig_state_persistence_long_1h(d,S.STPER1H,{})['le'];assert bool(a.iloc[249])==bool(b.iloc[249])
def test_choppy_state_rejected():
 d=tape();i=250;d.loc[i-6:i,'c']=float(d.loc[i-6,'c']);assert not bool(S.sig_state_persistence_long_1h(d,S.STPER1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
