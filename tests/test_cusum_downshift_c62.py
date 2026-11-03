import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_cusum_downshift_c62 as C
def tape(mode='down',n=260):
 i=np.arange(n)
 if mode=='down':ret=np.where(i%2==0,-.001,-.003)
 elif mode=='up':ret=np.where(i%2==0,.001,.003)
 else:ret=np.where(i%2==0,-.002,.002)
 close=100*np.cumprod(1.0+ret);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=close/(1+ret),h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_persistent_negative_shift_emits_alarm():
 d=tape();assert S.sig_cusum_downshift_short_1h(d,S.CSD1H,{})['se'].iloc[-100:].any()
def test_positive_shift_does_not_emit_alarm():
 d=tape('up');assert not S.sig_cusum_downshift_short_1h(d,S.CSD1H,{})['se'].any()
def test_balanced_returns_do_not_emit_alarm():
 d=tape('flat');assert not S.sig_cusum_downshift_short_1h(d,S.CSD1H,{})['se'].iloc[-100:].any()
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_cusum_downshift_short_1h(d,S.CSD1H,{})['se'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_cusum_downshift_short_1h(d,S.CSD1H,{})['se'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
