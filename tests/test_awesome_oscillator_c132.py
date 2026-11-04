import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_awesome_oscillator_c132 as C
def tape(kind='rising_wave',n=360):
 x=np.arange(n,dtype=float);close=100.+.12*x+2.8*np.sin(x/8.) if kind=='rising_wave' else 170.-.14*x
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.003;low=np.minimum(open_,close)*.997
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_ao_transition_fires():
 d=tape();sig=S.sig_awesome_oscillator_long_1h(d,S.AO1H,{})['le'];assert sig.iloc[-220:].sum()>0
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_awesome_oscillator_long_1h(d,S.AO1H,{})['le'].iloc[-180:].any()
def test_ao_is_price_translation_invariant():
 d=tape();a=S._awesome_oscillator(d.h,d.l);b=S._awesome_oscillator(d.h+50.,d.l+50.);z=a.notna()&b.notna();assert np.allclose(a[z],b[z])
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_awesome_oscillator_long_1h(d,S.AO1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_awesome_oscillator_long_1h(d,S.AO1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_awesome_oscillator_long_1h(d,S.AO1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,'h']=d.h.iloc[i]*3;d.loc[i+1,'l']=d.l.iloc[i]*.3;after=bool(S.sig_awesome_oscillator_long_1h(d,S.AO1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
