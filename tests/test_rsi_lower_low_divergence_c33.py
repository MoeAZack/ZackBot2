import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_rsi_lower_low_divergence_c33 as C  # noqa: E402


def tape(n=400):
    c = np.linspace(100.0, 110.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
        o=c, h=c + 0.5, l=c - 0.5, c=c, v=1000.0, qv=100_000.0)))


def setup_divergence(d, i=300):
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'qv'] = 100_000.0; d.loc[:, 'rsi14'] = 50.0
    d.loc[i - 10, 'c'] = 95.0; d.loc[i - 10, 'rsi14'] = 20.0
    d.loc[i, ['o', 'c', 'rsi14']] = [93.0, 94.0, 30.0]
    return d, i


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.RDL1H
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=1.5, max_bars=12)


def test_literal_price_lower_low_rsi_divergence_signal():
    d, i = setup_divergence(tape())
    sig = S.sig_rsi_lower_low_divergence_long_1h(d, S.RDL1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_confirming_rsi_lower_low_is_rejected():
    d, i = setup_divergence(tape()); d.loc[i, 'rsi14'] = 19.0
    assert not bool(S.sig_rsi_lower_low_divergence_long_1h(d, S.RDL1H, {})['le'].iloc[i])


def test_four_bar_separation_excludes_recent_cluster():
    d, i = setup_divergence(tape()); d.loc[i - 2, ['c', 'rsi14']] = [90.0, 10.0]
    assert bool(S.sig_rsi_lower_low_divergence_long_1h(d, S.RDL1H, {})['le'].iloc[i])


def test_current_volume_does_not_enter_its_own_baseline():
    d, i = setup_divergence(tape()); d.loc[i, 'qv'] = 110_000.0
    a = S.sig_rsi_lower_low_divergence_long_1h(d, S.RDL1H, {})['le'].iloc[i]
    d.loc[i, 'qv'] = 100_000.0
    b = S.sig_rsi_lower_low_divergence_long_1h(d, S.RDL1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
