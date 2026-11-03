import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_mfi_oversold_recovery_c36 as C  # noqa: E402


def tape(n=400):
    c = np.linspace(100.0, 110.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
        o=c, h=c + 0.5, l=c - 0.5, c=c, v=1000.0, qv=100_000.0)))


def setup_recovery(d, i=300):
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'qv'] = 100_000.0; d.loc[:, 'v'] = 1000.0
    value = 110.0
    for j in range(i - 16, i - 2):
        value -= 1.0
        d.loc[j, ['o', 'h', 'l', 'c']] = [value + 0.2, value + 0.3, value - 0.3, value]
    for j in range(i - 2, i + 1):
        value += 0.5
        d.loc[j, ['o', 'h', 'l', 'c']] = [value - 0.2, value + 0.3, value - 0.3, value]
    return d, i


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.MFIR1H
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=1.5, max_bars=12)


def test_literal_mfi_oversold_recovery_signal():
    d, i = setup_recovery(tape())
    sig = S.sig_mfi_oversold_recovery_long_1h(d, S.MFIR1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_recovery_without_bullish_candle_is_rejected():
    d, i = setup_recovery(tape()); d.loc[i, 'o'] = d.c.iloc[i] + 0.1
    assert not bool(S.sig_mfi_oversold_recovery_long_1h(d, S.MFIR1H, {})['le'].iloc[i])


def test_current_quote_volume_does_not_enter_its_own_baseline():
    d, i = setup_recovery(tape()); d.loc[i, 'qv'] = 110_000.0
    a = S.sig_mfi_oversold_recovery_long_1h(d, S.MFIR1H, {})['le'].iloc[i]
    d.loc[i, 'qv'] = 100_000.0
    b = S.sig_mfi_oversold_recovery_long_1h(d, S.MFIR1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
