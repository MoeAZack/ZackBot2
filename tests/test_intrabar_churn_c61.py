import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_intrabar_churn_c61 as C
def tape(churn=True,down=True,n=260):
 step=-.001 if down else .001;close=100*np.cumprod(np.full(n,1.0+step));spread=.03 if churn else .0008
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=close/(1+step),h=close*(1+spread),l=close*(1-spread),c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_high_intrabar_churn_negative_hour_fires():
 d=tape();assert bool(S.sig_intrabar_churn_reversion_long_1h(d,S.ICR1H,{})['le'].iloc[-1])
def test_low_range_variance_does_not_fire():
 d=tape(False);assert not bool(S.sig_intrabar_churn_reversion_long_1h(d,S.ICR1H,{})['le'].iloc[-1])
def test_positive_path_does_not_fire():
 d=tape(down=False);assert not bool(S.sig_intrabar_churn_reversion_long_1h(d,S.ICR1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_intrabar_churn_reversion_long_1h(d,S.ICR1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_intrabar_churn_reversion_long_1h(d,S.ICR1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
