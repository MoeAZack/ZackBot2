import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_regression_trend_quality_c56 as C
def tape(kind='trend',n=100,i=80):
 x=np.arange(n,dtype=float)
 if kind=='trend':logc=.002*x+.0005*np.sin(x)
 elif kind=='chop':logc=.015*np.sin(x*1.7)
 else:logc=np.zeros(n)
 c=100*np.exp(logc);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=d.c*.02;return d,i
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_coherent_positive_slope_fires():
 d,i=tape();assert bool(S.sig_regression_trend_quality_long_1h(d,S.RTT1H,{})['le'].iloc[i])
def test_noisy_path_does_not_fire():
 d,i=tape('chop');assert not bool(S.sig_regression_trend_quality_long_1h(d,S.RTT1H,{})['le'].iloc[i])
def test_future_close_cannot_change_current_signal():
 d,i=tape();before=bool(S.sig_regression_trend_quality_long_1h(d,S.RTT1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=.5;after=bool(S.sig_regression_trend_quality_long_1h(d,S.RTT1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
