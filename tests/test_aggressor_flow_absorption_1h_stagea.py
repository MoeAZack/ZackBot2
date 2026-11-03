import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_aggressor_flow_absorption_candidate as A  # noqa: E402


def tape(n=300):
    c = np.full(n, 100.0)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[A.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == dict(imbalance_threshold=.20, max_hour_move=.004, cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=1.5, tp_r=1.5, max_bars=8)


def test_absorption_sides_are_mirrored():
    d = tape(); d.loc[:, 'atr'] = 2.0; i = 240
    ctx = {'taker_imbalance': np.zeros(len(d))}; ctx['taker_imbalance'][i] = -.25
    sig = S.sig_aggressor_flow_absorption_1h(d, S.AOF1H, ctx)
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])
    ctx['taker_imbalance'][i] = .25
    sig = S.sig_aggressor_flow_absorption_1h(d, S.AOF1H, ctx)
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])
    d.loc[i, 'c'] = 101.0
    assert not S.sig_aggressor_flow_absorption_1h(d, S.AOF1H, ctx)['se'].iloc[i]


def test_context_aligns_hour_and_restores_builder():
    d = tape(); dfs = {'BTCUSDT': d, 'ETHUSDT': d.copy()}
    t = pd.to_datetime(d.t, utc=True).map(lambda x: int(x.timestamp() * 1000))
    features = {s: {int(x): .25 for x in t} for s in dfs}
    before = A.B.ST.build_context
    with A.flow_context_scope({}, {}, features):
        ctx = A.B.ST.build_context(dfs)
        assert np.isclose(ctx['ETHUSDT']['taker_imbalance'][-1], .25)
    assert A.B.ST.build_context is before
