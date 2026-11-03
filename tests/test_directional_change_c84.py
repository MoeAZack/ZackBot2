import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_directional_change_c84 as C
def tape(kind='up_event',n=240):
 ret=np.full(n,.003);ret[-5:]=[-.01,-.01,-.01,.005,.025]
 if kind=='no_up_event':ret[-1]=.005
 elif kind=='negative_drift':ret[-24:]=-.003;ret[-5:]=[-.01,-.01,-.01,.005,.025]
 close=100*np.exp(np.cumsum(ret))
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.001,l=close*.999,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_confirmed_upward_change_fires():
 d=tape();assert bool(S.sig_directional_change_overshoot_long_1h(d,S.DCOS1H,{})['le'].iloc[-1])
def test_subthreshold_recovery_does_not_fire():
 d=tape('no_up_event');assert not bool(S.sig_directional_change_overshoot_long_1h(d,S.DCOS1H,{})['le'].iloc[-1])
def test_negative_24h_drift_does_not_fire():
 d=tape('negative_drift');assert not bool(S.sig_directional_change_overshoot_long_1h(d,S.DCOS1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_directional_change_overshoot_long_1h(d,S.DCOS1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_directional_change_overshoot_long_1h(d,S.DCOS1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
