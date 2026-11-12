"""Focused synthetic and prefix-causality checks for the six fixed candidates."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent / 'source'))
import strategies as S
import hourly_candidates as H

def frame(n=300, seed=7):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, .8, n))
    return pd.DataFrame(dict(t=pd.date_range('2020-01-01', periods=n, freq='h', tz='UTC'), o=c-rng.normal(0,.4,n), h=c+1, l=c-1, c=c, v=rng.uniform(1, 10, n)))

def check_prefix():
    raw, btc = frame(), frame(seed=8)
    H.set_panel({'BTCUSDT': btc})
    full = S.indicators(raw)
    for _, fn, *_ in H._specs:
        allsig = fn(full, {}, {})
        for end in (80, 151, 240, 299):
            H.set_panel({'BTCUSDT': btc.iloc[:end].copy()})
            prefix = fn(S.indicators(raw.iloc[:end].copy()), {}, {})
            for k in ('le', 'se', 'lx', 'sx'):
                assert allsig[k].iloc[:end].equals(prefix[k]), (fn.__name__, end, k)
        H.set_panel({'BTCUSDT': btc})
    # Future BTC price changes cannot affect prior entries.
    changed = btc.copy(); changed.loc[200:, 'c'] *= 10
    a = H.h6(full, {}, {})
    H.set_panel({'BTCUSDT': changed}); b = H.h6(full, {}, {})
    assert a['le'].iloc[:200].equals(b['le'].iloc[:200])
    assert a['se'].iloc[:200].equals(b['se'].iloc[:200])

def base():
    n=80
    d = pd.DataFrame(dict(t=pd.date_range('2020-01-01', periods=n, freq='h', tz='UTC'), o=100., h=101., l=99., c=100., v=10., e20=100., e50=101., e200=100., atr=1., adx=15., rsi2=50.))
    return d

def check_predicates():
    d=base(); d.loc[:55,'e200']=99.; d.loc[78,['c','l']]=[99.,98.]; d.loc[79,['c','h']]=[102.,103.]
    assert H.h1(d,{}, {})['le'].iloc[-1]
    d.loc[79,'c']=100.; assert not H.h1(d,{}, {})['le'].iloc[-1]
    d=base(); d.loc[79,['c','h','v']]=[102.,103.,13.]; assert H.h2(d,{}, {})['le'].iloc[-1]
    d.loc[79,'v']=12.; assert not H.h2(d,{}, {})['le'].iloc[-1]
    d=base(); d.loc[78,'c']=98.; d.loc[79,'c']=100.; assert H.h3(d,{}, {})['le'].iloc[-1]
    d.loc[79,'adx']=20.; assert not H.h3(d,{}, {})['le'].iloc[-1]
    d=base(); d.loc[77:79,'rsi2']=[5.,25.,31.]; d.loc[79,'c']=101.; assert H.h4(d,{}, {})['le'].iloc[-1]
    d.loc[79,'rsi2']=30.; assert not H.h4(d,{}, {})['le'].iloc[-1]
    d=base(); d.loc[79,['o','h','l','c','v']]=[100.,102.,100.,102.,20.]; assert H.h5(d,{}, {})['le'].iloc[-1]
    d.loc[79,'v']=19.; assert not H.h5(d,{}, {})['le'].iloc[-1]
    d=base(); btc=base(); btc['c']=100.; H.set_panel({'BTCUSDT':btc}); d.loc[79,['c','h','v']]=[102.,103.,13.]
    assert H.h6(d,{}, {})['le'].iloc[-1]
    assert not H.h6(d,{}, {'symbol':'BTCUSDT'})['le'].any()
    d.loc[79,'v']=12.; assert not H.h6(d,{}, {})['le'].iloc[-1]
    # Exact timestamp matching fails closed if the BTC 24-hour comparison is absent.
    H.set_panel({'BTCUSDT':btc.drop(index=55)}); d.loc[79,'v']=13.
    assert not H.h6(d,{}, {})['le'].iloc[-1]

if __name__ == '__main__':
    check_predicates(); check_prefix()
    print('PASS: six synthetic predicates and six prefix-causality checks; BTC exclusion and missing-data checks')
