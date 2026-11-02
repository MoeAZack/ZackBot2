import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_volume_pressure_inventory_candidate as V  # noqa: E402


def tape(n=300):
    c = np.full(n, 100.0)
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                             o=c, h=c + 1, l=c - 1, c=c, v=1000.0))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[V.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == S.VPI1H
    assert st['params'] == dict(decision_hour=0, window=48, imbalance=.10,
                                max_displacement_atr=1.0, volume_floor=.8, cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=24)


def test_literal_symmetric_pressure_and_daily_sampling():
    d = S.indicators(tape()); i = 240
    assert pd.Timestamp(d.t.iloc[i]).hour == 0
    d.loc[:, 'atr'] = 2.0
    # alternating tiny moves keep price absorbed; upward moves carry larger volume
    for j in range(i - 47, i + 1):
        d.loc[j, 'c'] = 100.2 if (j - (i - 47)) % 2 == 0 else 100.0
        d.loc[j, 'v'] = 2000.0 if d.loc[j, 'c'] > d.loc[j - 1, 'c'] else 500.0
    sig = S.sig_volume_pressure_inventory_1h(d, S.VPI1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])
    assert not bool(sig['le'].iloc[i + 1])
    d = S.indicators(tape()); d.loc[:, 'atr'] = 2.0
    for j in range(i - 47, i + 1):
        d.loc[j, 'c'] = 99.8 if (j - (i - 47)) % 2 == 0 else 100.0
        d.loc[j, 'v'] = 2000.0 if d.loc[j, 'c'] < d.loc[j - 1, 'c'] else 500.0
    sig = S.sig_volume_pressure_inventory_1h(d, S.VPI1H, {})
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])


def test_scoped_runner_restores_timeframe_and_funding():
    before, fund = dict(V.B.TFS), V.B.BT.FUND_PER_BAR
    with V.one_hour_actual_scope({}, {}):
        assert V.B.TFS['1h'] == V.TF_MS and V.B.BT.FUND_PER_BAR == 0
    assert V.B.TFS == before and V.B.BT.FUND_PER_BAR == fund
