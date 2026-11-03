import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_rip_sell_dca_c28_candidate as C  # noqa: E402


def tape(n=300):
    c = np.linspace(150.0, 100.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_default_off_clone_is_exact_existing_cell():
    old, new = S.STRATEGIES['exp_rip_sell_dca'], S.STRATEGIES[C.KEY]
    assert new['research_only'] is True and new['tf'] == '4h' and new['sides'] == 'short'
    assert new['fn'] is old['fn'] and new['params'] == {}
    assert new['mgmt'] == old['mgmt'] and new['reentry_lock'] is False


def test_literal_downtrend_stretch_cross_cell():
    d = tape(); i = 250
    d.loc[:, 'e50'] = 90.0; d.loc[:, 'e200'] = 100.0
    d.loc[:, 'atr'] = 2.0; d.loc[:, 'e20'] = d.c - 2.0
    d.loc[i, 'e20'] = d.c.iloc[i] - 4.0
    sig = S.sig_exp_rip_sell_dca(d, {}, {})
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.R.KEY == 'rank_breadth_reversal_4h'
    assert C.KEY != C.R.KEY
