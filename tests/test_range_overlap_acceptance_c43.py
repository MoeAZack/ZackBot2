import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_range_overlap_acceptance_c43 as C
def tape(n=300):
 c=np.full(n,100.0);o=c.copy();h=np.full(n,101.0);l=np.full(n,99.0)
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=2.;d.loc[:,'e20']=99.;d.loc[:,'e50']=99.;d.loc[:,'qv']=100000.;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_two_close_acceptance_after_overlap():
 d=tape();i=250;d.loc[i-1,['o','h','l','c']]=[100.5,102.,100.,101.5];d.loc[i,['o','h','l','c']]=[101.5,103.,101.,102.5]
 assert bool(S.sig_range_overlap_acceptance_long_1h(d,S.ROAB1H,{})['le'].iloc[i])
def test_one_close_is_not_acceptance():
 d=tape();i=250;d.loc[i,['o','h','l','c']]=[100.5,103.,100.,102.5]
 assert not bool(S.sig_range_overlap_acceptance_long_1h(d,S.ROAB1H,{})['le'].iloc[i])
def test_nonoverlapping_balance_is_rejected():
 d=tape();i=250
 for j in range(i-13,i-1):d.loc[j,['h','l','c']]=[80+2*j,79+2*j,79.5+2*j]
 d.loc[i-1,['o','h','l','c']]=[100.5,102.,100.,101.5];d.loc[i,['o','h','l','c']]=[101.5,103.,101.,102.5]
 assert not bool(S.sig_range_overlap_acceptance_long_1h(d,S.ROAB1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
