import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_illiquidity_impact_reversion_c31 as C  # noqa: E402


def tape(n=400):
    c = np.linspace(100.0, 110.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
        o=c, h=c + 1, l=c - 1, c=c, v=1000.0, qv=100_000.0)))


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.IIR1H
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=1.5, max_bars=8)


def test_literal_downside_liquidity_impact_signal():
    d = tape(); i = 300
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'qv'] = 100_000.0
    d.loc[i, 'c'] = d.c.iloc[i - 1] - 3.0
    d.loc[i, 'qv'] = 100_000.0
    sig = S.sig_illiquidity_impact_reversion_long_1h(d, S.IIR1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_current_quote_volume_does_not_enter_its_own_baselines():
    d = tape(); i = 300
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'qv'] = 100_000.0
    d.loc[i, 'c'] = d.c.iloc[i - 1] - 3.0
    d.loc[i, 'qv'] = 110_000.0
    a = S.sig_illiquidity_impact_reversion_long_1h(d, S.IIR1H, {})['le'].iloc[i]
    d.loc[i, 'qv'] = 100_000.0
    b = S.sig_illiquidity_impact_reversion_long_1h(d, S.IIR1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
