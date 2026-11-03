import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_transient_jump_dislocation_candidate as T  # noqa: E402


def tape(n=300):
    c = np.full(n, 100.0)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[T.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == dict(jump_threshold=.006, max_retention=.50, participation=2.0, cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=1.5, tp_r=1.5, max_bars=8)


def test_literal_mirrored_jump_rules():
    d = tape(); d.loc[:, 'atr'] = 2.0; i = 240
    ctx = {'minute_jump': np.zeros(len(d)), 'jump_retention': np.ones(len(d)),
           'jump_participation': np.ones(len(d))}
    ctx['minute_jump'][i], ctx['jump_retention'][i], ctx['jump_participation'][i] = -.007, .25, 3.0
    sig = S.sig_transient_jump_dislocation_1h(d, S.TJD1H, ctx)
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])
    ctx['minute_jump'][i] = .007
    sig = S.sig_transient_jump_dislocation_1h(d, S.TJD1H, ctx)
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])
    ctx['jump_retention'][i] = .75
    assert not S.sig_transient_jump_dislocation_1h(d, S.TJD1H, ctx)['se'].iloc[i]


def test_context_aligns_hour_and_restores_builder():
    d = tape(); dfs = {'BTCUSDT': d, 'ETHUSDT': d.copy()}
    t = pd.to_datetime(d.t, utc=True).map(lambda x: int(x.timestamp() * 1000))
    features = {s: {int(x): {'jump': .01, 'jump_qv': 300., 'median_qv': 100.} for x in t} for s in dfs}
    before = T.B.ST.build_context
    with T.minute_context_scope({}, {}, features):
        ctx = T.B.ST.build_context(dfs)
        assert np.isclose(ctx['ETHUSDT']['minute_jump'][-1], .01)
        assert np.isclose(ctx['ETHUSDT']['jump_participation'][-1], 3.0)
    assert T.B.ST.build_context is before
