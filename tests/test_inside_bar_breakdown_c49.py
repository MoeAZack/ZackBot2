import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_inside_bar_breakdown_c49 as C
def tape(n=300):
 c=np.full(n,100.);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=np.full(n,101.),l=np.full(n,99.),c=c,v=1000.,qv=100000.)))
 d.loc[:,'atr']=2.;d.loc[:,'e20']=101.;d.loc[:,'e50']=101.;d.loc[:,'qv']=100000.;return d
def pattern(d,i):
 d.loc[i-2,['o','h','l','c']]=[100.,102.,98.,100.];d.loc[i-1,['o','h','l','c']]=[100.,101.,99.,100.];return d
def test_default_off_and_family_count():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_inside_bar_breakdown():
 d=pattern(tape(),250);d.loc[250,['o','h','l','c','qv']]=[99.,99.,97.,97.5,100000.];assert bool(S.sig_inside_bar_breakdown_short_1h(d,S.IBB1H,{})['se'].iloc[250])
def test_inside_bar_must_remain_contained():
 d=pattern(tape(),250);d.loc[249,'h']=102.5;d.loc[250,['o','h','l','c','qv']]=[99.,99.,97.,97.5,100000.];assert not bool(S.sig_inside_bar_breakdown_short_1h(d,S.IBB1H,{})['se'].iloc[250])
def test_close_must_break_mother_low():
 d=pattern(tape(),250);d.loc[250,['o','h','l','c','qv']]=[99.,100.,97.5,98.2,100000.];assert not bool(S.sig_inside_bar_breakdown_short_1h(d,S.IBB1H,{})['se'].iloc[250])
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
