import os,sys
import numpy as np
import pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_volatility_acceleration_c41 as C  # noqa: E402
def tape(n=400):
    c=np.linspace(100,110,n); d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01',periods=n,freq='1h',tz='UTC'),o=c,h=c+.5,l=c-.5,c=c,v=1000.,qv=100000.)))
    d.loc[:,'atr']=2.; d.loc[:,'qv']=100000.; d.loc[:,'e20']=d.c*.99; d.loc[:,'e50']=np.linspace(90,100,n); return d
def setup(d,i=300):
    d.loc[d.index[i-5:i+1],'h']=d.c.iloc[i-5:i+1]+1.5; d.loc[d.index[i-5:i+1],'l']=d.c.iloc[i-5:i+1]-1.5
    d.loc[i,'o']=d.c.iloc[i]-.8; return d,i
def test_candidate_default_off():
    s=S.STRATEGIES[C.KEY]; assert s['research_only'] and s['reentry_lock'] and s['params']==S.VACL1H
def test_literal_signal():
    d,i=setup(tape()); assert bool(S.sig_volatility_acceleration_long_1h(d,S.VACL1H,{})['le'].iloc[i])
def test_no_acceleration_rejected():
    d=tape(); i=300; d.loc[i,'o']=d.c.iloc[i]-.8; assert not bool(S.sig_volatility_acceleration_long_1h(d,S.VACL1H,{})['le'].iloc[i])
def test_current_volume_excluded_from_baseline():
    d,i=setup(tape()); d.loc[i,'qv']=110000.; a=S.sig_volatility_acceleration_long_1h(d,S.VACL1H,{})['le'].iloc[i]; d.loc[i,'qv']=100000.; b=S.sig_volatility_acceleration_long_1h(d,S.VACL1H,{})['le'].iloc[i]; assert bool(a) and bool(b)
def test_wrapper_import_safe(): assert C.V.KEY=='volume_pressure_inventory_1h' and C.KEY!=C.V.KEY
