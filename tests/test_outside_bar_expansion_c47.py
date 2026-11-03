import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_outside_bar_expansion_c47 as C
def tape(n=300):
 c=np.full(n,100.);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=np.full(n,101.),l=np.full(n,99.),c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=2.;d.loc[:,'e20']=99.;d.loc[:,'e50']=99.;d.loc[:,'e200']=98.;d.loc[:,'qv']=100000.;return d
def test_default_off_and_family_count():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_bullish_outside_bar_accepts():
 d=tape();i=250;d.loc[i,['o','h','l','c','qv']]=[100.,102.,98.5,101.8,130000.];assert bool(S.sig_outside_bar_expansion_long_1h(d,S.OBE1H,{})['le'].iloc[i])
def test_engulfment_requires_both_extremes():
 d=tape();i=250;d.loc[i,['o','h','l','c','qv']]=[100.,102.,99.2,101.8,130000.];assert not bool(S.sig_outside_bar_expansion_long_1h(d,S.OBE1H,{})['le'].iloc[i])
def test_close_must_accept_above_prior_high():
 d=tape();i=250;d.loc[i,['o','h','l','c','qv']]=[100.,102.,98.5,100.8,130000.];assert not bool(S.sig_outside_bar_expansion_long_1h(d,S.OBE1H,{})['le'].iloc[i])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
