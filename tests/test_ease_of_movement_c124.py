import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_ease_of_movement_c124 as C
def tape(kind='cycle',n=300):
 close=np.r_[np.linspace(140,90,n-100),np.linspace(100,170,100)] if kind=='cycle' else np.linspace(140,80,n)
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.004;low=np.minimum(open_,close)*.996
 qv=np.full(n,100000.);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=qv)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_ease_transition_fires():
 d=tape();sig=S.sig_ease_of_movement_long_1h(d,S.EOM1H,{})['le'];assert sig.iloc[-140:].sum()>0
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_ease_of_movement_long_1h(d,S.EOM1H,{})['le'].iloc[-120:].any()
def test_liquidity_normalization_reduces_raw_ease():
 d=tape();a=S._ease_of_movement(d.h,d.l,d.qv,14);b=S._ease_of_movement(d.h,d.l,d.qv*10,14);m=a.notna();assert np.allclose(b[m],a[m]/10)
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_ease_of_movement_long_1h(d,S.EOM1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_ease_of_movement_long_1h(d,S.EOM1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_ease_of_movement_long_1h(d,S.EOM1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,['h','l','c']]=[d.h.iloc[i]*3,d.l.iloc[i]*.3,d.c.iloc[i]*2];after=bool(S.sig_ease_of_movement_long_1h(d,S.EOM1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
