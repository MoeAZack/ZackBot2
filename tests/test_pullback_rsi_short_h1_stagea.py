import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_pullback_rsi_short_h1_candidate as H  # noqa: E402


def tape(n=300):
    c = np.linspace(150.0, 100.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_default_off_clone_is_exact_existing_short_cell():
    old, new = S.STRATEGIES['pullback_rsi'], S.STRATEGIES[H.KEY]
    assert new['research_only'] is True and new['tf'] == '4h' and new['sides'] == 'short'
    assert new['fn'] is old['fn'] and new['params'] == old.get('params', {})
    assert new['mgmt'] == old['mgmt']
    assert new['reentry_lock'] is False


def test_literal_short_is_downtrend_rsi_cross_below_60():
    d = tape(); i = 250
    d.loc[:, 'e50'], d.loc[:, 'e200'] = 90.0, 100.0
    d.loc[:, 'c'] = 80.0
    d.loc[:, 'rsi14'] = 65.0; d.loc[i, 'rsi14'] = 55.0
    sig = S.sig_trend_pullback_rsi(d, {}, {})
    assert bool(sig['se'].iloc[i]) and not bool(sig['le'].iloc[i])


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert H.R.KEY == 'rank_breadth_reversal_4h'
    assert H.KEY != H.R.KEY
