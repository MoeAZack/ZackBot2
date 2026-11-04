import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_pvt_c136 as C
def tape(kind='waves',n=420):
 x=np.arange(n,dtype=float);close=100.+.11*x+2.8*np.sin(x/8.) if kind=='waves' else 180.-.14*x
 open_=np.r_[close[0],close[:-1]];high=np.maximum(open_,close)*1.003;low=np.minimum(open_,close)*.997
 vol=900.+250.*(1.+np.sin(x/5.))+5.*x
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=vol,qv=vol*close)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_bullish_pvt_crossover_fires():
 d=tape();sig=S.sig_pvt_cross_long_1h(d,S.PVT1H,{})['le'];assert sig.iloc[-260:].sum()>0
def test_persistent_downtrend_does_not_fire():
 d=tape('down');assert not S.sig_pvt_cross_long_1h(d,S.PVT1H,{})['le'].iloc[-200:].any()
def test_pvt_scales_with_volume():
 d=tape();a=S._price_volume_trend(d.c,d.v);b=S._price_volume_trend(d.c,d.v*3.);assert np.allclose(b,3.*a)
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_pvt_cross_long_1h(d,S.PVT1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_pvt_cross_long_1h(d,S.PVT1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_pvt_cross_long_1h(d,S.PVT1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,'c']=d.c.iloc[i]*3.;after=bool(S.sig_pvt_cross_long_1h(d,S.PVT1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
