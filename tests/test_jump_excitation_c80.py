import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_jump_excitation_c80 as C
def tape(kind='clustered',n=240):
 ret=np.full(n,.001)
 if kind in ('clustered','negative'):
  ret[-18::3]=.02;ret[-1]=.02
 elif kind=='isolated':ret[-20]=.02
 if kind=='negative':ret[-1]=-.005
 close=100*np.exp(np.cumsum(ret))
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.001,l=close*.999,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_clustered_positive_jump_events_fire():
 d=tape();assert bool(S.sig_positive_jump_excitation_long_1h(d,S.JEX1H,{})['le'].iloc[-1])
def test_isolated_jump_does_not_fire():
 d=tape('isolated');assert not bool(S.sig_positive_jump_excitation_long_1h(d,S.JEX1H,{})['le'].iloc[-1])
def test_negative_decision_hour_does_not_fire():
 d=tape('negative');assert not bool(S.sig_positive_jump_excitation_long_1h(d,S.JEX1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_positive_jump_excitation_long_1h(d,S.JEX1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_positive_jump_excitation_long_1h(d,S.JEX1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
