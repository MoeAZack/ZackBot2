import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_hill_tail_c72 as C
def tape(kind='heavy',n=400):
 ret=np.full(n,.0005)
 if kind in ('heavy','negative'):
  mags=np.array([.20,.15,.10,.08,.06,.05,.04,.03,.025,.02,.018,.016,.005])
  ret[np.arange(n-30,n-17)]=-mags
 elif kind=='thin':ret[::3]=-.002
 ret[-1]=-.003 if kind=='negative' else .003
 close=100*np.exp(np.cumsum(ret));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_heavy_downside_tail_positive_recovery_fires():
 d=tape();assert bool(S.sig_hill_tail_recovery_long_1h(d,S.HTR1H,{})['le'].iloc[-1])
def test_thin_downside_tail_does_not_fire():
 d=tape('thin');assert not bool(S.sig_hill_tail_recovery_long_1h(d,S.HTR1H,{})['le'].iloc[-1])
def test_negative_decision_hour_does_not_fire():
 d=tape('negative');assert not bool(S.sig_hill_tail_recovery_long_1h(d,S.HTR1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_hill_tail_recovery_long_1h(d,S.HTR1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_hill_tail_recovery_long_1h(d,S.HTR1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
