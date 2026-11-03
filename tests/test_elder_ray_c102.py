import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_elder_ray_c102 as C
def tape(kind='pullback',n=220):
 close=np.linspace(100,140,n)
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.004;low=np.minimum(open_,close)*.996
 if kind=='pullback':
  low[-3:]=[close[-3]*.970,close[-2]*.980,close[-1]*.990]
 elif kind=='fall':
  close=np.linspace(140,100,n);open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.004;low=np.minimum(open_,close)*.996
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_improving_bear_power_in_uptrend_fires():
 d=tape();assert bool(S.sig_elder_ray_pullback_long_1h(d,S.ERAY1H,{})['le'].iloc[-1])
def test_falling_trend_does_not_fire():
 d=tape('fall');assert not S.sig_elder_ray_pullback_long_1h(d,S.ERAY1H,{})['le'].iloc[-20:].any()
def test_cost_gate_blocks_too_tight_stop():
 d=tape();d.loc[len(d)-1,'atr']=d.c.iloc[-1]*.001;assert not bool(S.sig_elder_ray_pullback_long_1h(d,S.ERAY1H,{})['le'].iloc[-1])
def test_future_bar_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_elder_ray_pullback_long_1h(d,S.ERAY1H,{})['le'].iloc[i]);d.loc[i+1,['h','l','c']]=[d.h.iloc[i]*3,d.l.iloc[i]*.3,d.c.iloc[i]*2];after=bool(S.sig_elder_ray_pullback_long_1h(d,S.ERAY1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
