import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_round_number_breakout_c68 as C
def tape(kind='cross',n=300):
 close=np.linspace(900.,990.,n)
 if kind=='cross':close[-2:]=995.,1005.
 elif kind=='below':close[-2:]=995.,997.
 else:close[-2:]=1005.,995.
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_upside_round_number_cross_fires():
 d=tape();assert bool(S.sig_round_number_breakout_long_1h(d,S.RNB1H,{})['le'].iloc[-1])
def test_move_remaining_below_barrier_does_not_fire():
 d=tape('below');assert not bool(S.sig_round_number_breakout_long_1h(d,S.RNB1H,{})['le'].iloc[-1])
def test_downward_cross_does_not_fire():
 d=tape('down');assert not bool(S.sig_round_number_breakout_long_1h(d,S.RNB1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_round_number_breakout_long_1h(d,S.RNB1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_round_number_breakout_long_1h(d,S.RNB1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
