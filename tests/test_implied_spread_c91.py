import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_implied_spread_c91 as C
def tape(kind='tight',n=240):
 ret=np.full(n,.002);close=100*np.exp(np.cumsum(ret));width=.002 if kind!='wide' else .02
 if kind=='negative':ret[-1]=-.003;close=100*np.exp(np.cumsum(ret))
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*np.exp(width/2),l=close*np.exp(-width/2),c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_tight_spread_positive_path_fires():
 d=tape();assert bool(S.sig_corwin_schultz_spread_long_1h(d,S.CS_SPREAD1H,{})['le'].iloc[-1])
def test_wide_spread_path_does_not_fire():
 d=tape('wide');assert not bool(S.sig_corwin_schultz_spread_long_1h(d,S.CS_SPREAD1H,{})['le'].iloc[-1])
def test_negative_decision_hour_does_not_fire():
 d=tape('negative');assert not bool(S.sig_corwin_schultz_spread_long_1h(d,S.CS_SPREAD1H,{})['le'].iloc[-1])
def test_future_ohlc_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_corwin_schultz_spread_long_1h(d,S.CS_SPREAD1H,{})['le'].iloc[i]);d.loc[i+1,['h','l','c']]=[d.h.iloc[i+1]*2,d.l.iloc[i+1]/2,d.c.iloc[i+1]*2];after=bool(S.sig_corwin_schultz_spread_long_1h(d,S.CS_SPREAD1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
