import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_multiscale_roughness_c63 as C
def tape(kind='anti',n=261):
 i=np.arange(n)
 if kind=='anti':ret=np.where(i%2==0,-.003,.002)
 else:ret=np.where((i//24)%2==0,-.003,.003)
 close=100*np.cumprod(1.0+ret);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=close/(1+ret),h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_antipersistent_negative_hour_fires():
 d=tape();assert bool(S.sig_multiscale_roughness_reversion_long_1h(d,S.MSR1H,{})['le'].iloc[-1])
def test_antipersistent_path_has_low_scaling_exponent():
 d=tape();assert S._rolling_variance_scaling_exponent(d.c,168,24).iloc[-1] <= .40
def test_persistent_blocks_have_high_scaling_exponent():
 d=tape('persistent');assert S._rolling_variance_scaling_exponent(d.c,168,24).iloc[-1] > .40
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_multiscale_roughness_reversion_long_1h(d,S.MSR1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_multiscale_roughness_reversion_long_1h(d,S.MSR1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
