import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_force_index_c127 as C
def tape(kind='rising_wave',n=320):
 x=np.arange(n,dtype=float);close=100.+.12*x+2.*np.sin(x/6.) if kind=='rising_wave' else 160.-.15*x
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.002;low=np.minimum(open_,close)*.998
 qv=100000.*(1.+.1*np.sin(x/11.));d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=qv)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_force_transition_fires():
 d=tape();sig=S.sig_force_index_long_1h(d,S.FORCE1H,{})['le'];assert sig.iloc[-180:].sum()>0
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_force_index_long_1h(d,S.FORCE1H,{})['le'].iloc[-140:].any()
def test_liquidity_scaling_scales_force():
 d=tape();a=S._force_index(d.c,d.qv,13);b=S._force_index(d.c,d.qv*10,13);m=a.notna();assert np.allclose(b[m],a[m]*10)
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_force_index_long_1h(d,S.FORCE1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_force_index_long_1h(d,S.FORCE1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_force_index_long_1h(d,S.FORCE1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,['c','qv']]=[d.c.iloc[i]*3,d.qv.iloc[i]*10];after=bool(S.sig_force_index_long_1h(d,S.FORCE1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
