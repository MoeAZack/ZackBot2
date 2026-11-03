import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_mass_index_c74 as C
def tape(kind='bulge',n=260):
 ret=np.full(n,-.0005);ret[-1]=.003
 close=100*np.exp(np.cumsum(ret))
 frac=np.geomspace(.0005,.30,n) if kind=='bulge' else np.full(n,.01)
 h=close*(1+frac/2);l=close*(1-frac/2)
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=h,l=l,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_range_bulge_positive_recovery_fires():
 d=tape();assert bool(S.sig_mass_index_recovery_long_1h(d,S.MIR1H,{})['le'].iloc[-1])
def test_flat_range_cycle_does_not_fire():
 d=tape('flat');assert not bool(S.sig_mass_index_recovery_long_1h(d,S.MIR1H,{})['le'].iloc[-1])
def test_negative_decision_hour_does_not_fire():
 d=tape();d.loc[len(d)-1,'c']=d.c.iloc[-2]*.99;assert not bool(S.sig_mass_index_recovery_long_1h(d,S.MIR1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_mass_index_recovery_long_1h(d,S.MIR1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_mass_index_recovery_long_1h(d,S.MIR1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
