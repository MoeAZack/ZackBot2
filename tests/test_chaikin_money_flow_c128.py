import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_chaikin_money_flow_c128 as C
def tape(kind='accumulation',n=320):
 x=np.arange(n,dtype=float);mid=100.+.10*x+2.*np.sin(x/8.) if kind=='accumulation' else 160.-.12*x
 spread=np.full(n,2.);bias=.75*np.sin(x/7.) if kind=='accumulation' else np.full(n,-.7)
 close=mid+bias;high=mid+spread/2;low=mid-spread/2;open_=np.r_[close[0],close[:-1]]
 d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=open_,h=high,l=low,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_positive_flow_transition_fires():
 d=tape();sig=S.sig_chaikin_money_flow_long_1h(d,S.CMF1H,{})['le'];assert sig.iloc[-180:].sum()>0
def test_persistent_distribution_does_not_fire():
 d=tape('distribution');assert not S.sig_chaikin_money_flow_long_1h(d,S.CMF1H,{})['le'].iloc[-140:].any()
def test_midpoint_closes_have_zero_flow():
 d=tape();mid=(d.h+d.l)/2;cmf=S._chaikin_money_flow(d.h,d.l,mid,d.qv,20);assert np.allclose(cmf.dropna(),0.)
def test_cost_gate_blocks_too_tight_stop():
 d=tape();sig=S.sig_chaikin_money_flow_long_1h(d,S.CMF1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);d.loc[i,'atr']=d.loc[i,'c']*.001;assert not bool(S.sig_chaikin_money_flow_long_1h(d,S.CMF1H,{})['le'].iloc[i])
def test_future_bar_cannot_change_prior_signal():
 d=tape();sig=S.sig_chaikin_money_flow_long_1h(d,S.CMF1H,{})['le'];i=int(np.flatnonzero(sig.values)[-1]);before=bool(sig.iloc[i]);d.loc[i+1,['h','l','c','qv']]=[d.h.iloc[i]*3,d.l.iloc[i]*.3,d.c.iloc[i]*2,d.qv.iloc[i]*10];after=bool(S.sig_chaikin_money_flow_long_1h(d,S.CMF1H,{})['le'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
