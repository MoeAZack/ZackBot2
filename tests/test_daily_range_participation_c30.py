import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_daily_range_participation_c30 as C  # noqa: E402


def tape(n=300):
    c = np.linspace(100.0, 120.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                                          o=c - .1, h=c + .2, l=c - .2, c=c, v=1000.0)))


def test_candidate_is_default_off_and_frozen():
    st = S.STRATEGIES[C.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'long'
    assert st['reentry_lock'] is True and st['params'] == S.DRP1H
    assert st['mgmt'] == dict(stop_atr=2.0, trail_atr=3.0, max_bars=24)


def test_literal_signal_reads_prior_range_and_prior_volume_baseline():
    d = tape(); i = 250
    d.loc[:, 'e50'] = 110.0; d.loc[:, 'e200'] = 100.0
    d.loc[:, 'v'] = 1000.0
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [120.0, 122.0, 119.0, 121.5, 2000.0]
    sig = S.sig_daily_range_participation_long_1h(d, S.DRP1H, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_current_volume_cannot_change_its_own_baseline():
    d = tape(); i = 250
    d.loc[:, 'e50'] = 110.0; d.loc[:, 'e200'] = 100.0; d.loc[:, 'v'] = 1000.0
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [120.0, 122.0, 119.0, 121.5, 10_000.0]
    a = S.sig_daily_range_participation_long_1h(d, S.DRP1H, {})['le'].iloc[i]
    d.loc[i, 'v'] = 2000.0
    b = S.sig_daily_range_participation_long_1h(d, S.DRP1H, {})['le'].iloc[i]
    assert bool(a) and bool(b)


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.V.KEY == 'volume_pressure_inventory_1h'
    assert C.KEY != C.V.KEY
