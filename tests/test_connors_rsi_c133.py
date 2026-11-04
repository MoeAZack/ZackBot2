import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_connors_rsi_c133 as C
def tape(kind='dips',n=520):
 x=np.arange(n,dtype=float)
 if kind=='dips': close=100.+.10*x+2.*np.sin(x/11.);close[260:266]-=np.arange(6)*2.2;close[266:272]-=np.arange(6,0,-1)*1.4
 else: close=180.-.12*x
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.003;low=np.minimum(open_,close)*.997
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_oversold_recovery_fires():
 d=tape();sig=S.sig_connors_rsi_recovery_long_1h(d,S.CRSI1H,{})['le'];assert sig.iloc[240:300].sum()>0
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_connors_rsi_recovery_long_1h(d,S.CRSI1H,{})['le'].iloc[-220:].any()
def test_connors_rsi_is_bounded():
 d=tape();v=S._connors_rsi(d.c);assert (v.dropna().between(0.,100.)).all()
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_connors_rsi_recovery_long_1h(d,S.CRSI1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_connors_rsi_recovery_long_1h(d,S.CRSI1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_connors_rsi_recovery_long_1h(d,S.CRSI1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,'c']=d.c.iloc[i]*3.;after=bool(S.sig_connors_rsi_recovery_long_1h(d,S.CRSI1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
