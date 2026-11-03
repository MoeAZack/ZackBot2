import os,sys,numpy as np,pandas as pd
import strategies as S
ROOT=os.path.dirname(os.path.dirname(__file__));sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_cross_sectional_low_vol_c59 as C
def tape(n=240):
 c=100*np.exp(np.arange(n)*0.0002);d=S.indicators(pd.DataFrame(dict(t=pd.date_range('2024',periods=n,freq='1h',tz='UTC'),o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=100000.)));d.loc[:,'atr']=d.c*.02;return d
def context(d,rank=3,n=18):return {'vol_rank7':pd.Series(rank,index=d.index),'vol_rank7_n':pd.Series(n,index=d.index)}
def test_default_off():assert S.STRATEGIES[C.KEY]['research_only'] and S.STRATEGIES[C.KEY]['reentry_lock']
def test_low_vol_rank_fires():
 d=tape();assert bool(S.sig_cross_sectional_low_vol_long_1h(d,S.LVF1H,context(d))['le'].iloc[-1])
def test_rank_outside_selection_does_not_fire():
 d=tape();assert not bool(S.sig_cross_sectional_low_vol_long_1h(d,S.LVF1H,context(d,6))['le'].iloc[-1])
def test_insufficient_pack_breadth_does_not_fire():
 d=tape();assert not bool(S.sig_cross_sectional_low_vol_long_1h(d,S.LVF1H,context(d,n=7))['le'].iloc[-1])
def test_context_ranks_quiet_symbol_first():
 n=260;t=pd.date_range('2024',periods=n,freq='1h',tz='UTC');base=np.arange(n)
 def frame(c):return S.indicators(pd.DataFrame(dict(t=t,o=c,h=c*1.002,l=c*.998,c=c,v=1000.,qv=100000.)))
 quiet=100*np.exp(base*.0002);noisy=100*np.exp(base*.0002+.02*np.sin(base))
 ctx=S.build_context({'BTCUSDT':frame(quiet),'QUIET':frame(quiet),'NOISY':frame(noisy)})
 assert ctx['QUIET']['vol_rank7'].iloc[-1] < ctx['NOISY']['vol_rank7'].iloc[-1]
def test_import_safe():assert C.V.KEY=='volume_pressure_inventory_1h'
