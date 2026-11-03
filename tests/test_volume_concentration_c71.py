import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_volume_concentration_c71 as C
def tape(kind='concentrated',n=320):
 ret=np.full(n,.001)
 if kind=='negative':ret[-1]=-.03
 close=100*np.cumprod(1+ret);qv=np.full(n,100000.)
 if kind!='diffuse':qv[-1]=5000000.
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=qv)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_concentrated_positive_participation_fires():
 d=tape();assert bool(S.sig_volume_concentration_continuation_long_1h(d,S.VHC1H,{})['le'].iloc[-1])
def test_uniform_participation_does_not_fire():
 d=tape('diffuse');assert not bool(S.sig_volume_concentration_continuation_long_1h(d,S.VHC1H,{})['le'].iloc[-1])
def test_negative_event_hour_does_not_fire():
 d=tape('negative');assert not bool(S.sig_volume_concentration_continuation_long_1h(d,S.VHC1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_volume_concentration_continuation_long_1h(d,S.VHC1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_volume_concentration_continuation_long_1h(d,S.VHC1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
