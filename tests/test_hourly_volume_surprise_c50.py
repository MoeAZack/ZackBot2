import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_hourly_volume_surprise_c50 as C
def tape(n=500):
 c=np.full(n,100.);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=np.full(n,101.),l=np.full(n,99.),c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=2.;d.loc[:,'e20']=99.;d.loc[:,'e50']=99.;d.loc[:,'qv']=100000.;return d
def test_default_off_and_family_count():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_same_hour_volume_surprise_impulse():
 d=tape();i=400;d.loc[i,['o','h','l','c','qv']]=[100.,102.5,99.5,102.,210000.];assert bool(S.sig_hourly_volume_surprise_long_1h(d,S.HVS1H,{})['le'].iloc[i])
def test_same_hour_baseline_not_generic_rolling_volume():
 d=tape();i=400
 for day in range(1,15):d.loc[i-24*day,'qv']=150000.
 d.loc[i,['o','h','l','c','qv']]=[100.,102.5,99.5,102.,210000.];assert not bool(S.sig_hourly_volume_surprise_long_1h(d,S.HVS1H,{})['le'].iloc[i])
def test_price_impulse_is_required():
 d=tape();i=400;d.loc[i,['o','h','l','c','qv']]=[100.,101.5,99.5,101.,210000.];assert not bool(S.sig_hourly_volume_surprise_long_1h(d,S.HVS1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
