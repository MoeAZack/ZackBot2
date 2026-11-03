import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_inside_bar_breakout_c32 as C  # noqa: E402


def tape(n=400):
    c = np.linspace(100.0, 110.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
        o=c, h=c + 0.5, l=c - 0.5, c=c, v=1000.0, qv=100_000.0)))


def setup_pattern(d, i=300):
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'qv'] = 100_000.0
    d.loc[i - 2, ['h', 'l']] = [102.0, 98.0]
    d.loc[i - 1, ['h', 'l']] = [101.0, 99.0]
    d.loc[i, 'c'] = 109.0
    return d, i


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.IBB1H
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=16)


def test_literal_inside_bar_breakout_signal():
    d, i = setup_pattern(tape())
    sig = S.sig_inside_bar_breakout_long_1h(d, S.IBB1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_non_inside_bar_is_rejected():
    d, i = setup_pattern(tape())
    d.loc[i - 1, 'h'] = 102.5
    assert not bool(S.sig_inside_bar_breakout_long_1h(d, S.IBB1H, {})['le'].iloc[i])


def test_current_volume_does_not_enter_its_own_baseline():
    d, i = setup_pattern(tape()); d.loc[i, 'qv'] = 110_000.0
    a = S.sig_inside_bar_breakout_long_1h(d, S.IBB1H, {})['le'].iloc[i]
    d.loc[i, 'qv'] = 100_000.0
    b = S.sig_inside_bar_breakout_long_1h(d, S.IBB1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
