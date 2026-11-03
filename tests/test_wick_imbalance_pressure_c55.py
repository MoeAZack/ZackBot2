import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_wick_imbalance_pressure_c55 as C
def tape(lower=.8,upper=.1,n=80,i=60):
 o=np.full(n,100.);c=np.full(n,100.2);h=np.maximum(o,c)+upper;l=np.minimum(o,c)-lower
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=o,h=h,l=l,c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=d.c*.02;return d,i
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_persistent_lower_wicks_fire():
 d,i=tape();assert bool(S.sig_wick_imbalance_pressure_long_1h(d,S.WIP1H,{})['le'].iloc[i])
def test_upper_wicks_do_not_fire():
 d,i=tape(.1,.8);assert not bool(S.sig_wick_imbalance_pressure_long_1h(d,S.WIP1H,{})['le'].iloc[i])
def test_one_lower_wick_cannot_substitute_for_window():
 d,i=tape(.1,.8);d.loc[i,'l']=d.loc[i,'l']-20.;assert not bool(S.sig_wick_imbalance_pressure_long_1h(d,S.WIP1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
