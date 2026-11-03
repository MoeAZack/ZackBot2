import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_volatility_clustering_c60 as C
def tape(clustered=True,down=True,n=260):
 i=np.arange(n);mag=np.where((i//12)%2==0,.001,.006) if clustered else np.where(i%2==0,.001,.006)
 sign=-1.0 if down else 1.0;close=100*np.cumprod(1.0+sign*mag)
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=close,h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_clustered_downside_fires_short():
 d=tape();assert bool(S.sig_volatility_clustering_downside_short_1h(d,S.VCD1H,{})['se'].iloc[-1])
def test_negative_squared_return_serial_correlation_does_not_fire():
 d=tape(False);assert not bool(S.sig_volatility_clustering_downside_short_1h(d,S.VCD1H,{})['se'].iloc[-1])
def test_positive_path_does_not_fire_short():
 d=tape(down=False);assert not bool(S.sig_volatility_clustering_downside_short_1h(d,S.VCD1H,{})['se'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d=tape();i=len(d)-2;before=bool(S.sig_volatility_clustering_downside_short_1h(d,S.VCD1H,{})['se'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_volatility_clustering_downside_short_1h(d,S.VCD1H,{})['se'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
