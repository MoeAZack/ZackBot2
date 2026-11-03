import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_ema_spread_expansion_c39 as C  # noqa: E402


def tape(n=400):
    c = np.linspace(100.0, 110.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
        o=c, h=c + 0.5, l=c - 0.5, c=c, v=1000.0, qv=100_000.0)))


def setup_expansion(d, i=300):
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'qv'] = 100_000.0
    d.loc[:, 'e50'] = np.linspace(100.0, 104.0, len(d)); d.loc[:, 'e20'] = d.e50 + 0.40
    d.loc[i - 5, 'e20'] = d.e50.iloc[i - 5] + 0.20
    d.loc[i, 'e20'] = d.e50.iloc[i] + 0.60
    d.loc[i, ['o', 'c']] = [d.e20.iloc[i] - 0.2, d.e20.iloc[i] + 0.2]
    return d, i


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.ESEL1H
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=24)


def test_literal_ema_spread_expansion_signal():
    d, i = setup_expansion(tape())
    sig = S.sig_ema_spread_expansion_long_1h(d, S.ESEL1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_no_recent_compression_is_rejected():
    d, i = setup_expansion(tape()); d.loc[i - 5, 'e20'] = d.e50.iloc[i - 5] + 0.40
    assert not bool(S.sig_ema_spread_expansion_long_1h(d, S.ESEL1H, {})['le'].iloc[i])


def test_current_volume_does_not_enter_its_own_baseline():
    d, i = setup_expansion(tape()); d.loc[i, 'qv'] = 110_000.0
    a = S.sig_ema_spread_expansion_long_1h(d, S.ESEL1H, {})['le'].iloc[i]
    d.loc[i, 'qv'] = 100_000.0
    b = S.sig_ema_spread_expansion_long_1h(d, S.ESEL1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
