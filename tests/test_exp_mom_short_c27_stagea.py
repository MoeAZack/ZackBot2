import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_exp_mom_short_c27_candidate as C  # noqa: E402


def tape(n=300):
    c = np.linspace(150.0, 100.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_default_off_clone_is_exact_existing_cell():
    old, new = S.STRATEGIES['exp_mom_short'], S.STRATEGIES[C.KEY]
    assert new['research_only'] is True and new['tf'] == '4h' and new['sides'] == 'short'
    assert new['fn'] is old['fn'] and new['params'] == {}
    assert new['mgmt'] == old['mgmt'] and new['reentry_lock'] is False


def test_literal_cold_coin_breakdown_cell():
    d = tape(); i = 250
    d.loc[:, 'vol_ratio'] = 1.0; d.loc[i, 'vol_ratio'] = 2.0
    d.loc[:, 'e50'] = d.c + 10.0
    d.loc[:, 'll20'] = d.c - 1.0; d.loc[i, 'll20'] = d.c.iloc[i] + 1.0
    rank = pd.Series(18.0, index=d.index); n = pd.Series(20.0, index=d.index)
    sig = S.sig_exp_mom_short(d, {}, {'rank7': rank, 'rank7_n': n, 'n_symbols': 20})
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert C.R.KEY == 'rank_breadth_reversal_4h'
    assert C.KEY != C.R.KEY
