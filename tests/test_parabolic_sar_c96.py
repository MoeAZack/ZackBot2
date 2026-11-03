import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_parabolic_sar_c96 as C
def tape(kind='reversal',n=180):
 if kind=='reversal':close=np.r_[np.linspace(130,90,n-35),90.,100.,np.linspace(101,130,33)]
 elif kind=='down':close=np.linspace(130,80,n)
 else:close=np.linspace(80,130,n)
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.003,l=close*.997,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_reversal_produces_one_causal_bullish_flip():
 d=tape();sig=S.sig_parabolic_sar_flip_long_1h(d,S.PSAR1H,{})['le'];assert sig.iloc[-35:].sum()==1
def test_persistent_downtrend_does_not_enter():
 d=tape('down');assert not S.sig_parabolic_sar_flip_long_1h(d,S.PSAR1H,{})['le'].iloc[-50:].any()
def test_flip_requires_close_above_ema20():
 d=tape();sig=S.sig_parabolic_sar_flip_long_1h(d,S.PSAR1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'e20']=d.loc[i,'c']*1.01;assert not bool(S.sig_parabolic_sar_flip_long_1h(d,S.PSAR1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_flip():
 d=tape();sig=S.sig_parabolic_sar_flip_long_1h(d,S.PSAR1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,['h','l','c']]=[d.h.iloc[i]*3,d.l.iloc[i]*.3,d.c.iloc[i]*2];after=bool(S.sig_parabolic_sar_flip_long_1h(d,S.PSAR1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
