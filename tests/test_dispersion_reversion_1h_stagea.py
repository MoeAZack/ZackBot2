"""DSR-1H Stage A: causal BTC-neutral cross-sectional residual recovery."""
import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,'tools','research'))
import run_breadth_candidate as B  # noqa: E402
import run_dispersion_reversion_candidate as D  # noqa: E402


KEY='dispersion_reversion_1h'


def tape(n=1000,seed=1):
    rng=np.random.default_rng(seed); c=100*np.exp(np.cumsum(rng.normal(0,.003,n)))
    o=np.r_[c[0],c[:-1]]; w=c*(.002+rng.random(n)*.002)
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01',periods=n,freq='1h',tz='UTC'),
                             o=o,h=np.maximum(o,c)+w,l=np.minimum(o,c)-w,c=c,v=1000*(1+rng.random(n))))


def test_frozen_research_only_registry_and_management():
    st=S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf']=='1h' and st['sides']=='both'
    assert st['params']==S.DSR1H and st['reentry_lock'] is True
    assert st['mgmt']==dict(stop_atr=1.5,tp1_r=.8,tp1_frac=.5,be_r=.8,tp_r=1.5,max_bars=12)


def test_cross_sectional_residual_context_is_prefix_causal():
    names=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','DOGEUSDT','UNIUSDT','SUIUSDT','NEARUSDT','ZECUSDT','BNBUSDT']
    raw={s:tape(seed=i+1) for i,s in enumerate(names)}
    full={s:S.indicators(x.copy()) for s,x in raw.items()}; fc=S.build_context(full)
    cut=900; part={s:S.indicators(x.iloc[:cut].copy()) for s,x in raw.items()}; pc=S.build_context(part)
    for s in names:
        for key in ('dsr_z','dsr_high','dsr_n'):
            assert np.array_equal(np.asarray(pc[s][key]),np.asarray(fc[s][key])[:cut],equal_nan=True),(s,key)
    # BTC is its own benchmark and therefore cannot become a residual outlier.
    assert fc['BTCUSDT']['dsr_z'].isna().all()


def test_literal_recovery_requires_prior_dispersion_liquidity_and_direction():
    d=S.indicators(tape()); i=850
    d.loc[:,'atr']=1.0; d.loc[:,'v']=1000.0
    d.loc[i,['o','c','v']]=[99.0,100.0,1200.0]
    z=pd.Series(0.0,index=d.index); z.loc[i-1]=-3.0; z.loc[i]=-2.0
    high=pd.Series(False,index=d.index); high.loc[i-1]=True
    n=pd.Series(10,index=d.index)
    ctx=dict(dsr_z=z,dsr_high=high,dsr_n=n)
    assert bool(S.sig_dispersion_reversion_1h(d,S.DSR1H,ctx)['le'].iloc[i])
    for name in ('no_event','not_recovering','illiquid','wrong_candle'):
        x=d.copy(); c={k:v.copy() for k,v in ctx.items()}
        if name=='no_event': c['dsr_high'].loc[i-1]=False
        if name=='not_recovering': c['dsr_z'].loc[i]=-2.6
        if name=='illiquid': x.loc[i,'v']=100.0
        if name=='wrong_candle': x.loc[i,'c']=98.5
        assert not bool(S.sig_dispersion_reversion_1h(x,S.DSR1H,c)['le'].iloc[i]),name


def test_one_hour_wrapper_scope_restores_shared_runner_table():
    before=dict(B.TFS); assert '1h' not in before
    with D.one_hour_scope(): assert B.TFS['1h']==3_600_000
    assert B.TFS==before
