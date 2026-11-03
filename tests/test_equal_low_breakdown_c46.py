import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_equal_low_breakdown_c46 as C
def tape(n=300):
 c=np.full(n,100.);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=np.full(n,101.),l=np.full(n,99.),c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=2.;d.loc[:,'e20']=101.;d.loc[:,'e50']=101.;d.loc[:,'qv']=100000.;return d
def test_default_off_and_family_count():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_repeated_low_breakdown():
 d=tape();i=250;d.loc[i,['o','h','l','c','qv']]=[100.,100.,98.,98.5,130000.];assert bool(S.sig_equal_low_breakdown_short_1h(d,S.ELBD1H,{})['se'].iloc[i])
def test_single_low_is_not_a_cluster():
 d=tape();i=250;d.loc[i-24:i-1,'l']=100.;d.loc[i-8,'l']=95.;d.loc[i,['o','h','l','c','qv']]=[95.,96.,93.,94.,130000.];assert not bool(S.sig_equal_low_breakdown_short_1h(d,S.ELBD1H,{})['se'].iloc[i])
def test_breakdown_buffer_is_required():
 d=tape();i=250;d.loc[i,['o','h','l','c','qv']]=[100.,100.,98.,98.9,130000.];assert not bool(S.sig_equal_low_breakdown_short_1h(d,S.ELBD1H,{})['se'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
