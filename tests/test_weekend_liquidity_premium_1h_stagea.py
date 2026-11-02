import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_weekend_liquidity_premium_candidate as W  # noqa: E402


def tape(n=24 * 14):
    c = np.full(n, 100.0)
    return S.indicators(pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                                          o=c, h=c + 1, l=c - 1, c=c, v=1000.0)))


def test_frozen_registry_and_management():
    st = S.STRATEGIES[W.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == dict(boundaries=((4, 23), (6, 23)), cost_multiple=5.0)
    assert st['mgmt'] == dict(stop_atr=2.5, tp_r=2.5, max_bars=24)


def test_only_two_weekend_boundaries_emit_both_cells():
    d = tape(); d.loc[:, 'atr'] = 2.0
    sig = S.sig_weekend_liquidity_premium_1h(d, S.WLP1H, {})
    got = pd.to_datetime(d.loc[sig['le'], 't'], utc=True)
    assert len(got) == 4
    assert list(zip(got.dt.weekday, got.dt.hour)) == [(4, 23), (6, 23), (4, 23), (6, 23)]
    assert np.array_equal(sig['le'], sig['se'])
    assert not sig['lx'].any() and not sig['sx'].any()


def test_base_runner_globals_are_not_mutated_on_import():
    assert W.V.KEY == 'volume_pressure_inventory_1h'
    assert W.KEY != W.V.KEY
