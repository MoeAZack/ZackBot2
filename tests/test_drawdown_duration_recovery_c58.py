import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_drawdown_duration_recovery_c58 as C
def tape(long_spell=True,n=300,i=240):
 c=np.full(n,100.);start=170 if long_spell else 220;c[start:start+20]=np.linspace(100,85,20);c[start+20:i]=85.;c[i]=86.
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d,i
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_deep_long_drawdown_reclaim_fires():
 d,i=tape();assert bool(S.sig_drawdown_duration_recovery_long_1h(d,S.DDRH1H,{})['le'].iloc[i])
def test_short_underwater_spell_does_not_fire():
 d,i=tape(False);assert not bool(S.sig_drawdown_duration_recovery_long_1h(d,S.DDRH1H,{})['le'].iloc[i])
def test_future_close_cannot_change_current_signal():
 d,i=tape();before=bool(S.sig_drawdown_duration_recovery_long_1h(d,S.DDRH1H,{})['le'].iloc[i]);d.loc[i+1,'c']=200.;after=bool(S.sig_drawdown_duration_recovery_long_1h(d,S.DDRH1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
