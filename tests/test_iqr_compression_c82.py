import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_iqr_compression_c82 as C
def tape(kind='compressed',n=300):
 pattern=np.array([-.004,.006,-.002,.005]);ret=np.resize(pattern,n)
 if kind in ('compressed','negative'):
  ret[-24:]=np.resize(np.array([.0008,.0012]),24);ret[-1]=.012
 elif kind=='uncompressed':
  ret[-24:]=np.resize(np.array([-.004,.008]),24);ret[-1]=.012
 if kind=='negative':ret[-1]=-.012
 close=100*np.exp(np.cumsum(ret))
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.001,l=close*.999,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_compressed_positive_release_fires():
 d=tape();assert bool(S.sig_iqr_compression_breakout_long_1h(d,S.IQRC1H,{})['le'].iloc[-1])
def test_uncompressed_path_does_not_fire():
 d=tape('uncompressed');assert not bool(S.sig_iqr_compression_breakout_long_1h(d,S.IQRC1H,{})['le'].iloc[-1])
def test_negative_release_does_not_fire():
 d=tape('negative');assert not bool(S.sig_iqr_compression_breakout_long_1h(d,S.IQRC1H,{})['le'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_iqr_compression_breakout_long_1h(d,S.IQRC1H,{})['le'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_iqr_compression_breakout_long_1h(d,S.IQRC1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
