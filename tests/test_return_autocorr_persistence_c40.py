import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_return_autocorr_persistence_c40 as C  # noqa: E402


def tape(n=400):
    rng = np.random.default_rng(40)
    ret = np.empty(n); ret[0] = 0.0
    for i in range(1, n): ret[i] = 0.75 * ret[i - 1] + rng.normal(0.001, 0.001)
    c = 100.0 * np.exp(np.cumsum(ret))
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
        o=np.r_[c[0], c[:-1]], h=c + 1.0, l=c - 1.0, c=c, v=1000.0, qv=100_000.0)))


def setup_signal(d, i=300):
    d.loc[:, 'atr'] = 2.0; d.loc[:, 'qv'] = 100_000.0
    d.loc[:, 'e20'] = d.c * 0.99; d.loc[:, 'e50'] = np.linspace(90.0, 100.0, len(d))
    base = d.c.iloc[i - 40]
    persistent = np.linspace(0.0005, 0.0020, 40)
    d.loc[d.index[i - 39:i + 1], 'c'] = base * np.exp(np.cumsum(persistent))
    d.loc[i, 'c'] = d.c.iloc[i - 1] + 1.00
    d.loc[d.index[i - 39:i + 1], 'e20'] = d.c.iloc[i - 39:i + 1] * 0.99
    return d, i


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.RAPL1H
    assert st['mgmt'] == dict(stop_atr=2.0, tp_r=2.0, max_bars=12)


def test_literal_positive_autocorrelation_signal():
    d, i = setup_signal(tape())
    sig = S.sig_return_autocorr_persistence_long_1h(d, S.RAPL1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_nonpositive_serial_dependence_is_rejected():
    d, i = setup_signal(tape())
    r = pd.Series(np.tile([0.002, -0.002], 30), index=d.index[i - 59:i + 1])
    base = d.c.iloc[i - 60]
    d.loc[d.index[i - 59:i + 1], 'c'] = base * np.exp(r.cumsum()).values
    d.loc[d.index[i], 'c'] = d.c.iloc[i - 1] + 1.00
    assert not bool(S.sig_return_autocorr_persistence_long_1h(d, S.RAPL1H, {})['le'].iloc[i])


def test_current_volume_does_not_enter_its_own_baseline():
    d, i = setup_signal(tape()); d.loc[i, 'qv'] = 110_000.0
    a = S.sig_return_autocorr_persistence_long_1h(d, S.RAPL1H, {})['le'].iloc[i]
    d.loc[i, 'qv'] = 100_000.0
    b = S.sig_return_autocorr_persistence_long_1h(d, S.RAPL1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
