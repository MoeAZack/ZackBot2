import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_ou_half_life_c65 as C
def tape(kind='ou',n=320):
 rng=np.random.default_rng(65)
 if kind=='ou':
  x=np.zeros(n)
  for i in range(1,n):x[i]=.80*x[i-1]+rng.normal(0,.005)
  x[-2],x[-1]=-.080,-.060
 else:x=np.arange(n)*.001+rng.normal(0,.001,n)
 close=100*np.exp(x);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_tradeable_half_life_negative_displacement_fires():
 d=tape();assert bool(S.sig_ou_half_life_reversion_long_1h(d,S.OUH1H,{})['le'].iloc[-1])
def test_ou_fixture_has_frozen_half_life_band():
 d=tape();_,half,z=S._rolling_ou_state(d.c,168);assert 2<=half.iloc[-1]<=24 and z.iloc[-1]<=-2
def test_trending_price_does_not_fire():
 d=tape('trend');assert not bool(S.sig_ou_half_life_reversion_long_1h(d,S.OUH1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_ou_half_life_reversion_long_1h(d,S.OUH1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_ou_half_life_reversion_long_1h(d,S.OUH1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
