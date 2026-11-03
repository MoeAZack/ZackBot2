import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_ar2_forecast_c93 as C
def tape(kind='accelerating',n=360):
 ret=np.full(n,.001)
 if kind=='accelerating':ret[-168:]=np.linspace(.0005,.004,168)
 elif kind=='declining':ret[-168:]=np.linspace(-.0005,-.004,168)
 elif kind=='negative':ret[-168:]=np.linspace(.0005,.004,168);ret[-1]=-.003
 close=100*np.exp(np.cumsum(ret))
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.001,l=close*.999,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_ar2_forecast_path_fires():
 d=tape();assert bool(S.sig_ar2_forecast_long_1h(d,S.AR2F1H,{})['le'].iloc[-1])
def test_negative_forecast_path_does_not_fire():
 d=tape('declining');assert not bool(S.sig_ar2_forecast_long_1h(d,S.AR2F1H,{})['le'].iloc[-1])
def test_negative_decision_hour_does_not_fire():
 d=tape('negative');assert not bool(S.sig_ar2_forecast_long_1h(d,S.AR2F1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_ar2_forecast_long_1h(d,S.AR2F1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_ar2_forecast_long_1h(d,S.AR2F1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
