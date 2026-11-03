import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_btc_orderflow_leadlag_candidate as B  # noqa: E402


def tape(n=300):
    c = np.full(n, 100.0)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[B.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == dict(btc_imbalance=.15, btc_move=.006, target_lag_band=.002, cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=12)


def test_leadlag_sides_and_target_filter_are_mirrored():
    d = tape(); d.loc[:, 'atr'] = 2.0; i = 240
    ctx = {'btc_taker_imbalance': np.zeros(len(d)), 'btc_hour_move': np.zeros(len(d))}
    ctx['btc_taker_imbalance'][i], ctx['btc_hour_move'][i] = .20, .01
    sig = S.sig_btc_orderflow_leadlag_1h(d, S.BOL1H, ctx)
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])
    ctx['btc_taker_imbalance'][i], ctx['btc_hour_move'][i] = -.20, -.01
    sig = S.sig_btc_orderflow_leadlag_1h(d, S.BOL1H, ctx)
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])
    d.loc[i, 'c'] = 101.0
    assert not S.sig_btc_orderflow_leadlag_1h(d, S.BOL1H, ctx)['se'].iloc[i]


def test_context_aligns_btc_flow_and_restores_builder():
    d = tape(); btc = d.copy(); btc.loc[:, 'c'] = 101.0
    dfs = {'BTCUSDT': btc, 'ETHUSDT': d.copy()}
    t = pd.to_datetime(d.t, utc=True).map(lambda x: int(x.timestamp() * 1000))
    flow = {int(x): .2 for x in t}; before = B.B.ST.build_context
    with B.leadlag_context_scope({}, {}, flow):
        ctx = B.B.ST.build_context(dfs)
        assert np.isclose(ctx['ETHUSDT']['btc_taker_imbalance'][-1], .2)
        assert np.isclose(ctx['ETHUSDT']['btc_hour_move'][-1], .01)
    assert B.B.ST.build_context is before
