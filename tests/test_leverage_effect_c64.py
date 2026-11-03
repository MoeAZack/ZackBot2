import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_leverage_effect_c64 as C
def tape(kind='leverage',n=261):
 i=np.arange(n)
 if kind=='leverage':ret=np.where(i%2==0,-.010,.001)
 elif kind=='equal':ret=np.resize(np.array([-.003,-.001,.003,.001]),n)
 else:ret=np.where(i%2==0,.010,-.001)
 close=100*np.cumprod(1.0+ret);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=close/(1+ret),h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_negative_return_variance_cross_moment_fires():
 d=tape();assert bool(S.sig_leverage_effect_downside_short_1h(d,S.LEV1H,{})['se'].iloc[-1])
def test_equal_magnitude_signs_do_not_fire():
 d=tape('equal');assert not bool(S.sig_leverage_effect_downside_short_1h(d,S.LEV1H,{})['se'].iloc[-1])
def test_positive_path_does_not_fire():
 d=tape('positive');assert not bool(S.sig_leverage_effect_downside_short_1h(d,S.LEV1H,{})['se'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_leverage_effect_downside_short_1h(d,S.LEV1H,{})['se'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_leverage_effect_downside_short_1h(d,S.LEV1H,{})['se'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
