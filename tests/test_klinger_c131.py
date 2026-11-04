import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_klinger_c131 as C
def tape(kind='rising_wave',n=420):
 x=np.arange(n,dtype=float);close=100.+.12*x+2.4*np.sin(x/7.) if kind=='rising_wave' else 180.-.14*x
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.003;low=np.minimum(open_,close)*.997
 vol=900.+250.*(1.+np.sin(x/5.))+8.*x
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=vol,qv=vol*close)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_klinger_transition_fires():
 d=tape();sig=S.sig_klinger_volume_long_1h(d,S.KVO1H,{})['le'];assert sig.iloc[-240:].sum()>0
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_klinger_volume_long_1h(d,S.KVO1H,{})['le'].iloc[-180:].any()
def test_kvo_scales_with_volume():
 d=tape();a,_=S._klinger_volume_oscillator(d.h,d.l,d.c,d.qv);b,_=S._klinger_volume_oscillator(d.h,d.l,d.c,d.qv*3.0);z=(a.notna()&b.notna());assert np.allclose(b[z],3.0*a[z])
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_klinger_volume_long_1h(d,S.KVO1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_klinger_volume_long_1h(d,S.KVO1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_klinger_volume_long_1h(d,S.KVO1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,'c']=d.c.iloc[i]*3;after=bool(S.sig_klinger_volume_long_1h(d,S.KVO1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
