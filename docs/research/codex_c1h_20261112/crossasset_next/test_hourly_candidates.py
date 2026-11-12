import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'source'))
import numpy as np
import pandas as pd
import pytest
import hourly_candidates as m

def frame(c):
    c=np.asarray(c,dtype=float); n=len(c)
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01',periods=n,freq='h',tz='UTC'),o=c,h=c+3,l=c-3,c=c,v=np.full(n,100.)))
def sample(kind,side):
    n=260; ix=np.arange(n); sign=1 if side=='le' else -1
    btc=frame(np.full(n,100.)); a=frame(100+sign*ix*.015); b=frame(np.full(n,100.)); c=frame(np.full(n,100.))
    if kind=='u1':
        a.loc[216:240,'c']=a.loc[216:240,'c']+sign*np.linspace(0,8,25)
    elif kind=='u2':
        a=frame(100-sign*ix*.20+np.sin(ix)*.30)
        a.loc[240,'o']=a.loc[240,'c']-sign*.2
    else:
        btc.loc[240,'c']=100+sign*5; btc.loc[240,'v']=200
    return {'BTCUSDT':btc,'AAAUSDT':a,'BBBUSDT':b,'CCCUSDT':c}

@pytest.mark.parametrize('kind',['u1','u2','u3'])
@pytest.mark.parametrize('side',['le','se'])
def test_direction_and_prefix(kind,side):
    raw=sample(kind,side); m.set_panel(raw)
    whole=getattr(m,kind)(raw['AAAUSDT'],{}, {'symbol':'AAAUSDT'})
    assert whole[side].iloc[240], (kind,side,whole[side].iloc[240])
    for end in (200,240,241,250):
        prefix={s:d.iloc[:end].copy() for s,d in raw.items()}; m.set_panel(prefix)
        result=getattr(m,kind)(prefix['AAAUSDT'],{}, {'symbol':'AAAUSDT'})
        for k in ('le','se','lx','sx'):
            np.testing.assert_array_equal(result[k].to_numpy(),whole[k].iloc[:end].to_numpy())

@pytest.mark.parametrize('kind',['u1','u2','u3'])
def test_missing_btc_fail_closed(kind):
    raw=sample(kind,'le'); del raw['BTCUSDT']; m.set_panel(raw)
    result=getattr(m,kind)(raw['AAAUSDT'],{}, {'symbol':'AAAUSDT'})
    assert not result['le'].any() and not result['se'].any()

def test_tie_alphabetical_and_missing_candle():
    raw=sample('u1','le'); raw['BBBUSDT']=raw['AAAUSDT'].copy(); raw['CCCUSDT']=frame(np.full(260,100.)); raw['DDDUSDT']=frame(np.full(260,100.))
    m.set_panel(dict(reversed(list(raw.items()))))
    assert m.u1(raw['AAAUSDT'],{}, {'symbol':'AAAUSDT'})['le'].iloc[240]
    assert not m.u1(raw['BBBUSDT'],{}, {'symbol':'BBBUSDT'})['le'].iloc[240]
    raw['CCCUSDT']=raw['CCCUSDT'].drop(index=230)
    m.set_panel(raw)
    assert not m.u1(raw['AAAUSDT'],{}, {'symbol':'AAAUSDT'})['le'].iloc[240]

def test_register_and_management():
    m.register()
    for key,cfg in m.CANDIDATES.items():
        assert m.S.STRATEGIES[key] is cfg
        assert cfg['mgmt']['stop_atr']==3 and cfg['mgmt']['be_r']==cfg['mgmt']['trail_atr']==0


