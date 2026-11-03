import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_large_trade_impulse_candidate as L  # noqa: E402


def tape(n=300):
    c = np.full(n, 100.0)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[L.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == dict(notional_multiple=2.0, move_threshold=.006,
                                baseline_hours=168, cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=12)


def test_large_trade_impulse_sides_are_mirrored():
    d = tape(); d.loc[:, 'atr'] = 2.0; i = 240
    ctx = {'avg_trade_notional': np.full(len(d), 100.0)}; ctx['avg_trade_notional'][i] = 250.0
    d.loc[i, 'c'] = 101.0
    sig = S.sig_large_trade_impulse_1h(d, S.LTI1H, ctx)
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])
    d.loc[i, 'c'] = 99.0
    sig = S.sig_large_trade_impulse_1h(d, S.LTI1H, ctx)
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])


def test_context_aligns_hour_and_restores_builder():
    d = tape(); dfs = {'BTCUSDT': d, 'ETHUSDT': d.copy()}
    t = pd.to_datetime(d.t, utc=True).map(lambda x: int(x.timestamp() * 1000))
    features = {s: {int(x): 250.0 for x in t} for s in dfs}; before = L.B.ST.build_context
    with L.trade_size_context_scope({}, {}, features):
        ctx = L.B.ST.build_context(dfs)
        assert np.isclose(ctx['ETHUSDT']['avg_trade_notional'][-1], 250.0)
    assert L.B.ST.build_context is before
