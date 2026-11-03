import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_price_recurrence_c69 as C
def tape(kind='sparse',n=320):
 if kind=='sparse':close=np.linspace(90.,100.,n);close[-1]=110.
 elif kind=='dense':close=100.+.2*np.sin(np.arange(n));close[-1]=100.1
 else:close=np.linspace(110.,100.,n);close[-1]=90.
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_sparse_positive_discovery_fires():
 d=tape();assert bool(S.sig_price_recurrence_discovery_long_1h(d,S.PRD1H,{})['le'].iloc[-1])
def test_dense_price_zone_does_not_fire():
 d=tape('dense');assert not bool(S.sig_price_recurrence_discovery_long_1h(d,S.PRD1H,{})['le'].iloc[-1])
def test_sparse_downside_move_does_not_fire():
 d=tape('down');assert not bool(S.sig_price_recurrence_discovery_long_1h(d,S.PRD1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_price_recurrence_discovery_long_1h(d,S.PRD1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_price_recurrence_discovery_long_1h(d,S.PRD1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
