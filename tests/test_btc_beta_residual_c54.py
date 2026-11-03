import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_btc_beta_residual_c54 as C
def tape(shock=-.03,n=750,i=700):
 rng=np.random.default_rng(54);br=rng.normal(0,.005,n);eps=rng.normal(0,.001,n);tr=1.5*br+eps;tr[i]=1.5*br[i]+shock
 bc=100.*np.exp(np.cumsum(br));tc=50.*np.exp(np.cumsum(tr));t=pd.date_range('2024',periods=n,freq='1h',tz='UTC')
 raw={'BTCUSDT':pd.DataFrame(dict(t=t,o=bc,h=bc*1.002,l=bc*.998,c=bc,v=1000.,qv=100000.)),'ETHUSDT':pd.DataFrame(dict(t=t,o=tc,h=tc*1.002,l=tc*.998,c=tc,v=1000.,qv=100000.))}
 b=S.indicators(raw['BTCUSDT']);d=S.indicators(raw['ETHUSDT']);d.loc[:,'atr']=d.c*.02;ctx=S.build_context({'BTCUSDT':b,'ETHUSDT':d})['ETHUSDT'];return d,ctx,i
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_negative_beta_residual_fires():
 d,ctx,i=tape();assert bool(S.sig_btc_beta_residual_reversion_long_1h(d,S.BBR1H,ctx)['le'].iloc[i])
def test_common_btc_move_is_removed():
 d,ctx,i=tape(0.);assert not bool(S.sig_btc_beta_residual_reversion_long_1h(d,S.BBR1H,ctx)['le'].iloc[i])
def test_current_shock_does_not_contaminate_prior_beta():
 d,ctx,i=tape();br=pd.Series(ctx['btc_return']);prior=d.c.pct_change().rolling(336,min_periods=336).cov(br).shift().iloc[i]/br.rolling(336,min_periods=336).var().shift().iloc[i];assert 1.2<prior<1.8
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
