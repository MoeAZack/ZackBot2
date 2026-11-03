import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_coppock_c107 as C
def tape(kind='reversal',n=300):
 close=np.r_[np.linspace(140,90,n-100),np.linspace(100,150,100)] if kind=='reversal' else np.linspace(140,80,n)
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.002;low=np.minimum(open_,close)*.998
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_dual_roc_reversal_fires():
 d=tape();sig=S.sig_coppock_zero_cross_long_1h(d,S.COPPOCK1H,{})['le'];assert sig.iloc[-120:].sum()==1
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_coppock_zero_cross_long_1h(d,S.COPPOCK1H,{})['le'].iloc[-120:].any()
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_coppock_zero_cross_long_1h(d,S.COPPOCK1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_coppock_zero_cross_long_1h(d,S.COPPOCK1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_coppock_zero_cross_long_1h(d,S.COPPOCK1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,'c']=d.c.iloc[i]*3;after=bool(S.sig_coppock_zero_cross_long_1h(d,S.COPPOCK1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
