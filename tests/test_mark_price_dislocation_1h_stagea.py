import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_mark_price_dislocation_candidate as M  # noqa: E402


def tape(n=300):
    c = np.full(n, 100.0)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[M.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == dict(hours=(0, 4, 8, 12, 16, 20), basis_threshold=.0008, cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=1.5, tp_r=1.5, max_bars=8)


def test_literal_basis_sides_and_fixed_hours():
    d = tape(); d.loc[:, 'atr'] = 2.0; i = 240
    ctx = {'mark_basis': np.zeros(len(d))}; ctx['mark_basis'][i] = -.001
    sig = S.sig_mark_price_dislocation_1h(d, S.MPD1H, ctx)
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])
    ctx['mark_basis'][i] = .001
    sig = S.sig_mark_price_dislocation_1h(d, S.MPD1H, ctx)
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])
    ctx['mark_basis'][i + 1] = .002
    sig = S.sig_mark_price_dislocation_1h(d, S.MPD1H, ctx)
    assert not bool(sig['se'].iloc[i + 1])


def test_mark_context_uses_close_timestamp_and_restores_builder():
    d = tape(); dfs = {'BTCUSDT': d, 'ETHUSDT': d.copy()}
    t = pd.to_datetime(d.t, utc=True).map(lambda x: int(x.timestamp() * 1000))
    marks = {s: [(int(x + M.TF_MS), 99.0) for x in t] for s in dfs}
    before = M.B.ST.build_context
    with M.mark_basis_scope({}, marks):
        ctx = M.B.ST.build_context(dfs)
        assert np.isclose(ctx['ETHUSDT']['mark_basis'][-1], 1 / 99)
    assert M.B.ST.build_context is before
