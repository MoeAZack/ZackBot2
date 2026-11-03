import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_kendall_monotonicity_c88 as C
def tape(kind='monotone',n=240):
 ret=np.full(n,.003)
 if kind=='noisy':ret[-24:]=np.resize(np.array([-.025,.030]),24)
 elif kind=='negative':ret[-1]=-.003
 close=100*np.exp(np.cumsum(ret))
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.001,l=close*.999,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_monotone_positive_path_fires():
 d=tape();assert bool(S.sig_kendall_monotonicity_long_1h(d,S.KMON1H,{})['le'].iloc[-1])
def test_low_concordance_path_does_not_fire():
 d=tape('noisy');assert not bool(S.sig_kendall_monotonicity_long_1h(d,S.KMON1H,{})['le'].iloc[-1])
def test_negative_decision_hour_does_not_fire():
 d=tape('negative');assert not bool(S.sig_kendall_monotonicity_long_1h(d,S.KMON1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_kendall_monotonicity_long_1h(d,S.KMON1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_kendall_monotonicity_long_1h(d,S.KMON1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
