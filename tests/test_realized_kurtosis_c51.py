import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_realized_kurtosis_c51 as C
def tape(fat=True,n=500):
 r=np.resize(np.array([.001,-.001]),n)
 if fat:r[390]=.10
 c=100.*np.exp(np.cumsum(r));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01',periods=n,freq='1h',tz='UTC'),o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=d.c*.02;return d
def test_default_off_and_family_count():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_fat_tail_daily_short():
 d=tape();assert bool(S.sig_realized_kurtosis_risk_short_1h(d,S.RKR1H,{})['se'].iloc[408])
def test_thin_tail_does_not_qualify():
 d=tape(False);assert not bool(S.sig_realized_kurtosis_risk_short_1h(d,S.RKR1H,{})['se'].iloc[408])
def test_only_frozen_decision_hour_qualifies():
 d=tape();assert not bool(S.sig_realized_kurtosis_risk_short_1h(d,S.RKR1H,{})['se'].iloc[409])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
