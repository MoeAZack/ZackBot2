import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_jump_variation_c66 as C
def tape(kind='jumps',n=320):
 i=np.arange(n)
 if kind=='jumps':
  ret=np.full(n,.0005);ret[::20]=-.05;ret[-1]=-.05
 elif kind=='smooth':ret=np.where(i%2==0,-.002,.002)
 else:ret=np.full(n,.001);ret[::20]=.03;ret[-1]=.03
 close=100*np.cumprod(1+ret);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_jump_dominated_downside_fires():
 d=tape();assert bool(S.sig_jump_variation_downside_short_1h(d,S.JVS1H,{})['se'].iloc[-1])
def test_smooth_equal_magnitude_path_does_not_fire():
 d=tape('smooth');assert not bool(S.sig_jump_variation_downside_short_1h(d,S.JVS1H,{})['se'].iloc[-1])
def test_positive_jump_path_does_not_fire():
 d=tape('positive');assert not bool(S.sig_jump_variation_downside_short_1h(d,S.JVS1H,{})['se'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_jump_variation_downside_short_1h(d,S.JVS1H,{})['se'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_jump_variation_downside_short_1h(d,S.JVS1H,{})['se'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
