import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_atr_shock_snapback_candidate as A  # noqa: E402


def tape(n=300):
    c = 100 + np.sin(np.arange(n) / 10)
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                             o=c - .1, h=c + 1, l=c - 1, c=c, v=1000.0))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[A.KEY]
    assert st['research_only'] is True and st['tf'] == '4h' and st['sides'] == 'both'
    assert st['params'] == S.ASS4H and st['reentry_lock'] is True
    assert st['mgmt'] == dict(stop_atr=1.5, tp1_r=.8, tp1_frac=.5, be_r=.8, tp_r=1.5, max_bars=12)


def test_literal_long_and_short_two_bar_events():
    d = S.indicators(tape()); i = 250
    d.loc[:, ['e50', 'e200', 'atr', 'v']] = [110.0, 100.0, 2.0, 1000.0]
    d.loc[i - 1, ['o', 'c']] = [104.0, 100.0]
    d.loc[i, ['o', 'c']] = [100.0, 103.0]
    assert bool(S.sig_atr_shock_snapback_4h(d, S.ASS4H, {})['le'].iloc[i])
    d.loc[:, ['e50', 'e200']] = [90.0, 100.0]
    d.loc[i - 1, ['o', 'c']] = [96.0, 100.0]
    d.loc[i, ['o', 'c']] = [100.0, 97.0]
    assert bool(S.sig_atr_shock_snapback_4h(d, S.ASS4H, {})['se'].iloc[i])


def test_no_signal_without_midpoint_reclaim_or_trend():
    d = S.indicators(tape()); i = 250
    d.loc[:, ['e50', 'e200', 'atr', 'v']] = [110.0, 100.0, 2.0, 1000.0]
    d.loc[i - 1, ['o', 'c']] = [104.0, 100.0]
    d.loc[i, ['o', 'c']] = [100.0, 101.0]
    assert not bool(S.sig_atr_shock_snapback_4h(d, S.ASS4H, {})['le'].iloc[i])
