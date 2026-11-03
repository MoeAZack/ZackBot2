import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_downside_semivariance_c57 as C
def tape(asymmetric=True,n=260,i=220):
 r=np.full(n,.001)
 if asymmetric:r[::3]=-.004
 else:r[::2]=-.001
 r[i]=.001;c=100.*np.exp(np.cumsum(r));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=d.c*.02;return d,i
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_downside_dominance_rebound_fires():
 d,i=tape();assert bool(S.sig_downside_semivariance_rebound_long_1h(d,S.DSR1H,{})['le'].iloc[i])
def test_balanced_semivariance_does_not_fire():
 d,i=tape(False);assert not bool(S.sig_downside_semivariance_rebound_long_1h(d,S.DSR1H,{})['le'].iloc[i])
def test_positive_rebound_is_required():
 d,i=tape();d.loc[i,'c']=d.loc[i-1,'c']*.999;assert not bool(S.sig_downside_semivariance_rebound_long_1h(d,S.DSR1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
