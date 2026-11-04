import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_schaff_cycle_c108 as C
def tape(kind='cycle',n=400):
 x=np.arange(n);close=100+0.20*x+8*np.sin(x/14.0) if kind=='cycle' else np.linspace(140,80,n)
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.002;low=np.minimum(open_,close)*.998
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_cyclical_transition_fires():
 d=tape();sig=S.sig_schaff_cycle_long_1h(d,S.STC1H,{})['le'];assert sig.iloc[-200:].sum()>0
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_schaff_cycle_long_1h(d,S.STC1H,{})['le'].iloc[-120:].any()
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_schaff_cycle_long_1h(d,S.STC1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_schaff_cycle_long_1h(d,S.STC1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_schaff_cycle_long_1h(d,S.STC1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,'c']=d.c.iloc[i]*3;after=bool(S.sig_schaff_cycle_long_1h(d,S.STC1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
