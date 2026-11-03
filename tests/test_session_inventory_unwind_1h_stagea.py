import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_session_inventory_unwind_candidate as U  # noqa: E402


def tape(n=300):
    c = 100 + np.sin(np.arange(n) / 10)
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                             o=c - .1, h=c + 1, l=c - 1, c=c, v=1000.0))


def test_frozen_registry_management_and_hours():
    st = S.STRATEGIES[U.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == S.SIU1H and tuple(st['params']['hours']) == (8, 16)
    assert st['mgmt'] == dict(stop_atr=1.5, tp1_r=.8, tp1_frac=.5, be_r=.8, tp_r=1.5, max_bars=6)


def test_literal_long_and_short_only_on_handoffs():
    d = S.indicators(tape()); i = 200  # 08:00 UTC
    assert pd.Timestamp(d.t.iloc[i]).hour == 8
    d.loc[:, ['atr', 'v']] = [2.0, 1000.0]
    d.loc[i - 9, 'c'], d.loc[i - 1, 'c'] = 105.0, 100.0
    d.loc[i, ['o', 'c']] = [100.0, 102.0]
    assert bool(S.sig_session_inventory_unwind_1h(d, S.SIU1H, {})['le'].iloc[i])
    d.loc[i - 9, 'c'], d.loc[i - 1, 'c'] = 95.0, 100.0
    d.loc[i, ['o', 'c']] = [100.0, 98.0]
    assert bool(S.sig_session_inventory_unwind_1h(d, S.SIU1H, {})['se'].iloc[i])
    j = i + 1
    d.loc[j - 9, 'c'], d.loc[j - 1, 'c'] = 105.0, 100.0
    d.loc[j, ['o', 'c']] = [100.0, 102.0]
    assert not bool(S.sig_session_inventory_unwind_1h(d, S.SIU1H, {})['le'].iloc[j])


def test_scoped_runner_restores_timeframe_and_funding():
    before, fund = dict(U.B.TFS), U.B.BT.FUND_PER_BAR
    with U.one_hour_actual_scope({}, {}):
        assert U.B.TFS['1h'] == U.TF_MS and U.B.BT.FUND_PER_BAR == 0
    assert U.B.TFS == before and U.B.BT.FUND_PER_BAR == fund
