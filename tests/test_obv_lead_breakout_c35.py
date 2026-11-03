import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_obv_lead_breakout_c35 as C  # noqa: E402


def tape(n=400):
    c = np.linspace(100.0, 110.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
        o=c, h=c + 0.5, l=c - 0.5, c=c, v=1000.0, qv=100_000.0)))


def setup_lead(d, i=300):
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'qv'] = 100_000.0
    prior_close = float(d.c.iloc[i - 1])
    d.loc[i - 20, 'c'] = prior_close + 2.0
    d.loc[i, ['o', 'c', 'qv']] = [prior_close - 0.1, prior_close + 0.3, 1_000_000.0]
    return d, i


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.OBVL1H
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=16)


def test_literal_obv_leads_price_signal():
    d, i = setup_lead(tape())
    sig = S.sig_obv_lead_breakout_long_1h(d, S.OBVL1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_price_breakout_is_not_the_lead_cell():
    d, i = setup_lead(tape()); d.loc[i, 'c'] = d.c.iloc[i - 20] + 0.1
    assert not bool(S.sig_obv_lead_breakout_long_1h(d, S.OBVL1H, {})['le'].iloc[i])


def test_current_volume_does_not_enter_its_own_baseline():
    d, i = setup_lead(tape())
    a = S.sig_obv_lead_breakout_long_1h(d, S.OBVL1H, {})['le'].iloc[i]
    d.loc[i, 'qv'] = 900_000.0
    b = S.sig_obv_lead_breakout_long_1h(d, S.OBVL1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
