import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_trimmed_return_c73 as C
def tape(kind='tail',n=240):
 ret=np.full(n,.001)
 if kind=='tail':ret[-12]=-.08
 elif kind=='broad':ret[-24:]=-.002;ret[-1]=.001
 elif kind=='negative':ret[-12]=-.08;ret[-1]=-.001
 ret[-1]=.002 if kind in ('tail','plain') else ret[-1]
 close=100*np.exp(np.cumsum(ret));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_tail_driven_selloff_recovery_fires():
 d=tape();assert bool(S.sig_trimmed_return_recovery_long_1h(d,S.TMR1H,{})['le'].iloc[-1])
def test_broad_decline_does_not_fire():
 d=tape('broad');assert not bool(S.sig_trimmed_return_recovery_long_1h(d,S.TMR1H,{})['le'].iloc[-1])
def test_no_dislocation_does_not_fire():
 d=tape('plain');assert not bool(S.sig_trimmed_return_recovery_long_1h(d,S.TMR1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_trimmed_return_recovery_long_1h(d,S.TMR1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_trimmed_return_recovery_long_1h(d,S.TMR1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
