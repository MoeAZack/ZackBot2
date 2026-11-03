import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_ichimoku_cloud_c97 as C
def tape(kind='breakout',n=180):
 if kind=='breakout':close=np.r_[np.linspace(110,90,100),np.full(55,90.),np.linspace(90,125,25)]
 elif kind=='flat':close=np.full(n,100.)
 else:close=np.linspace(130,80,n)
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_bullish_cloud_transition_fires_once():
 d=tape();sig=S.sig_ichimoku_cloud_cross_long_1h(d,S.ICHI1H,{})['le'];assert sig.iloc[-30:].sum()==1
def test_flat_market_does_not_fire():
 d=tape('flat');assert not S.sig_ichimoku_cloud_cross_long_1h(d,S.ICHI1H,{})['le'].any()
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_ichimoku_cloud_cross_long_1h(d,S.ICHI1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_ichimoku_cloud_cross_long_1h(d,S.ICHI1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_ichimoku_cloud_cross_long_1h(d,S.ICHI1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,['h','l','c']]=[d.h.iloc[i]*3,d.l.iloc[i]*.3,d.c.iloc[i]*2];after=bool(S.sig_ichimoku_cloud_cross_long_1h(d,S.ICHI1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
