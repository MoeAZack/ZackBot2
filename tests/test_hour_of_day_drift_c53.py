import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_hour_of_day_drift_c53 as C
def tape(next_hour_return=.003,current_hour_return=0.,n=900,i=800):
 r=np.zeros(n)
 for day in range(1,29):r[i+1-24*day]=next_hour_return;r[i-24*day]=current_hour_return
 c=100.*np.exp(np.cumsum(r));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=d.c*.02;return d,i
def test_default_off_and_family_count():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_next_hour_seasonality():
 d,i=tape();assert bool(S.sig_hour_of_day_drift_long_1h(d,S.HODD1H,{})['le'].iloc[i])
def test_hurdle_is_required():
 d,i=tape(.001);assert not bool(S.sig_hour_of_day_drift_long_1h(d,S.HODD1H,{})['le'].iloc[i])
def test_current_hour_history_cannot_substitute():
 d,i=tape(0.,.01);assert not bool(S.sig_hour_of_day_drift_long_1h(d,S.HODD1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
