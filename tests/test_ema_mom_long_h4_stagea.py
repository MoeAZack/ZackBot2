import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_ema_mom_long_h4_candidate as H  # noqa: E402


def tape(n=300):
    c = np.linspace(100.0, 150.0, n)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_default_off_clone_is_exact_existing_h4_cell():
    old, new = S.STRATEGIES['ema_mom'], S.STRATEGIES[H.KEY]
    assert new['research_only'] is True and new['tf'] == '4h' and new['sides'] == 'long'
    assert new['fn'] is old['fn'] and new['params'] == {}
    assert new['mgmt'] == old['mgmt'] == dict(stop_atr=2.5)
    assert new['reentry_lock'] is False


def test_h4_is_management_distinct_from_h3_despite_shared_entry():
    h3, h4 = S.STRATEGIES['exp_trend_long_h3'], S.STRATEGIES[H.KEY]
    d = tape()
    a, b = h3['fn'](d, {}, {}), h4['fn'](d, {}, {})
    assert a['le'].equals(b['le']) and a['lx'].equals(b['lx'])
    assert h3['mgmt'] != h4['mgmt']
    assert 'tp_r' not in h4['mgmt'] and 'max_bars' not in h4['mgmt']


def test_literal_ema_mom_long_cell_is_causal():
    d = tape(); i = 250
    d.loc[:, 'ret42'] = 0.1; d.loc[:, 'ret180'] = 0.2; d.loc[:, 'e200'] = 90.0
    d.loc[:, 'e20'] = 100.0; d.loc[:, 'e50'] = 101.0; d.loc[i:, 'e20'] = 102.0
    sig = S.sig_ema_mom(d, {}, {})
    assert bool(sig['le'].iloc[i]) and not bool(sig['se'].iloc[i])


def test_wrapper_does_not_mutate_shared_runner_on_import():
    assert H.R.KEY == 'rank_breadth_reversal_4h'
    assert H.KEY != H.R.KEY
