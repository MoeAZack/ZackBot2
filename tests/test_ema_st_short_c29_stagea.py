import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_ema_st_short_c29_candidate as C  # noqa: E402


def tape(n=300):
    c = np.linspace(150.0, 100.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_default_off_clone_uses_existing_emitted_short_cell():
    old, new = S.STRATEGIES['ema_st'], S.STRATEGIES[C.KEY]
    assert old['sides'] == 'long' and new['sides'] == 'short'
    assert new['research_only'] is True and new['tf'] == '4h'
    assert new['fn'] is old['fn'] and new['mgmt'] == old['mgmt'] == dict(stop_atr=2.5)


def test_literal_bearish_cross_and_supertrend_cell():
    d = tape(); i = 250
    d.loc[:, 'e20'] = 102.0; d.loc[:, 'e50'] = 101.0; d.loc[i:, 'e20'] = 100.0
    d.loc[:, 'e200'] = 120.0; d.loc[:, 'st'] = -1
    sig = S.sig_ema_st(d, {}, {})
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.R.KEY == 'rank_breadth_reversal_4h'
    assert C.KEY != C.R.KEY
