import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_aroon_recency_c76 as C
def tape(kind='rising',n=240):
 if kind=='rising':close=100*np.exp(np.arange(n)*.001)
 elif kind=='falling_recovery':close=100*np.exp(-np.arange(n)*.001);close[-1]=close[-2]*1.001
 else:close=100*np.exp(np.arange(n)*.001);close[-1]=close[-2]*.99
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_recent_high_stale_low_fires():
 d=tape();assert bool(S.sig_aroon_recency_long_1h(d,S.AROON1H,{})['le'].iloc[-1])
def test_recent_low_stale_high_does_not_fire():
 d=tape('falling_recovery');assert not bool(S.sig_aroon_recency_long_1h(d,S.AROON1H,{})['le'].iloc[-1])
def test_negative_decision_hour_does_not_fire():
 d=tape('negative');assert not bool(S.sig_aroon_recency_long_1h(d,S.AROON1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_aroon_recency_long_1h(d,S.AROON1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_aroon_recency_long_1h(d,S.AROON1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
