import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_lower_tail_dependence_c67 as C
def tape(kind='dependent',n=800):
 target=np.full(n,.001);btc=np.full(n,.001)
 btc[::5]=-.04
 if kind=='dependent':target[::5]=-.04
 elif kind=='independent':target[2::5]=-.04
 else:target[::5]=.01
 if kind!='positive':target[-1]=-.05
 btc[-1]=-.05
 close=100*np.cumprod(1+target);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=np.r_[close[0],close[:-1]],h=close*1.002,l=close*.998,c=close,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d,btc
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_dependent_lower_tail_fires():
 d,btc=tape();assert bool(S.sig_lower_tail_dependence_short_1h(d,S.LTD1H,{'btc_return':btc})['se'].iloc[-1])
def test_independent_tail_does_not_fire():
 d,btc=tape('independent');assert not bool(S.sig_lower_tail_dependence_short_1h(d,S.LTD1H,{'btc_return':btc})['se'].iloc[-1])
def test_positive_target_return_does_not_fire():
 d,btc=tape('positive');assert not bool(S.sig_lower_tail_dependence_short_1h(d,S.LTD1H,{'btc_return':btc})['se'].iloc[-1])
def test_future_close_cannot_change_current_signal():
 d,btc=tape();i=len(d)-2;before=bool(S.sig_lower_tail_dependence_short_1h(d,S.LTD1H,{'btc_return':btc})['se'].iloc[i]);d.loc[i+1,'c']*=2;after=bool(S.sig_lower_tail_dependence_short_1h(d,S.LTD1H,{'btc_return':btc})['se'].iloc[i]);assert before==after
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
