"""FDMR-1H causal funding feature and frozen signal tests."""
import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_breadth_candidate as B  # noqa: E402
import run_funding_dislocation_candidate as F  # noqa: E402


def tape(n=200):
    c = 100 + np.sin(np.arange(n) / 8)
    return pd.DataFrame(dict(t=np.arange(n) * F.TF_MS, o=c - .1, h=c + .4, l=c - .4, c=c, v=1000.0, qv=100000.0))


def test_registry_is_frozen_research_only():
    st = S.STRATEGIES[F.KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == S.FDMR1H and st['reentry_lock'] is True
    assert st['mgmt'] == dict(stop_atr=1.5, tp1_r=.8, tp1_frac=.5, be_r=.8, tp_r=1.5, max_bars=18)


def test_event_z_uses_prior_events_only():
    ev = [(i * 8 * F.TF_MS + 5, float(i % 7) / 10000) for i in range(100)]
    a = F.event_z(ev)
    b = F.event_z(ev[:-1] + [(ev[-1][0], 99.0)])
    assert all(np.isnan(a[i][2]) for i in range(F.MIN_EVENTS))
    assert [(x[0], x[1]) for x in a[:-1]] == [(x[0], x[1]) for x in b[:-1]]
    assert np.allclose([x[2] for x in a[:-1]], [x[2] for x in b[:-1]], equal_nan=True)
    assert a[-1][2] != b[-1][2]


def test_fresh_event_is_strictly_known_before_open_and_prefix_causal():
    d = tape(10)
    event = [(F.TF_MS + 5, -.001, -2.0)]
    got = F.attach_features(d, event)
    # Event just after 01:00 is not available at 01:00; first complete post-event candle opens 02:00.
    assert not got.loc[1, 'funding_fresh'] and got.loc[2, 'funding_fresh']
    changed = F.attach_features(d, event + [(20 * F.TF_MS, .5, 99.0)])
    assert np.array_equal(got.funding_fresh, changed.funding_fresh)
    assert np.array_equal(got.funding_z, changed.funding_z, equal_nan=True)


def test_actual_funding_uses_entry_inclusive_exit_exclusive_and_causal_mark():
    ev = [(8 * F.TF_MS, .001, 2.0), (16 * F.TF_MS, -.002, -2.0)]
    marks = [(7 * F.TF_MS, 100.0), (15 * F.TF_MS, 110.0)]
    assert F.actual_funding('X', 1, 2, 8 * F.TF_MS, 16 * F.TF_MS, ev, marks) == .2
    assert F.actual_funding('X', -1, 2, 8 * F.TF_MS, 17 * F.TF_MS, ev, marks) == .24


def test_literal_long_and_short_rules_are_mirrors():
    d = S.indicators(tape(200)); i = 150
    d['funding_z'] = 0.0; d['funding_fresh'] = False
    d.loc[i, ['funding_z', 'funding_fresh', 'o', 'c', 'v', 'atr']] = [-2.0, True, 97.0, 98.0, 1200.0, 1.0]
    d.loc[i-6, 'c'] = 101.0; d.loc[i-1, 'c'] = 97.5
    assert bool(S.sig_funding_dislocation_mr_1h(d, S.FDMR1H, {})['le'].iloc[i])
    d.loc[i, ['funding_z', 'o', 'c']] = [2.0, 103.0, 102.0]
    d.loc[i-6, 'c'] = 99.0; d.loc[i-1, 'c'] = 102.5
    assert bool(S.sig_funding_dislocation_mr_1h(d, S.FDMR1H, {})['se'].iloc[i])


def test_one_hour_scope_restores_shared_globals():
    before, fund = dict(B.TFS), B.BT.FUND_PER_BAR
    with F.one_hour_actual_scope({}, {}):
        assert B.TFS['1h'] == F.TF_MS and B.BT.FUND_PER_BAR == 0
    assert B.TFS == before and B.BT.FUND_PER_BAR == fund
