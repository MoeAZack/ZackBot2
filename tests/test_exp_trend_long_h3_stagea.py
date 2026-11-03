import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_exp_trend_long_h3_candidate as H  # noqa: E402


def tape(n=300):
    c = np.linspace(100.0, 150.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_default_off_clone_is_exact_existing_long_cell():
    old, new = S.STRATEGIES['exp_trend'], S.STRATEGIES[H.KEY]
    assert new['research_only'] is True and new['tf'] == '4h' and new['sides'] == 'long'
    assert new['fn'] is old['fn'] and new['params'] == {}
    assert new['mgmt'] == old['mgmt'] and new['reentry_lock'] is False


def test_literal_exp_trend_long_cell_is_one_sided_and_causal():
    d = tape(); i = 250
    d.loc[:, 'ret42'] = 0.1; d.loc[:, 'ret180'] = 0.2; d.loc[:, 'e200'] = 90.0
    d.loc[:, 'e20'] = 100.0; d.loc[:, 'e50'] = 101.0; d.loc[i:, 'e20'] = 102.0
    sig = S.sig_exp_trend(d, {}, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert H.R.KEY == 'rank_breadth_reversal_4h'
    assert H.KEY != H.R.KEY
