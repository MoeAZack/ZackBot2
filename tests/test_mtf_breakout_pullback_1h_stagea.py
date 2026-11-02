"""MBP-1H Stage A: causal 4h arming and separate 1h confirmation."""
import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_breadth_candidate as B  # noqa: E402
import run_mtf_pullback_candidate as M  # noqa: E402


KEY = 'mtf_breakout_pullback_1h'


def tape(n=1200, seed=19):
    rng = np.random.default_rng(seed)
    c = 100*np.exp(np.cumsum(.0002 + rng.normal(0, .003, n)))
    o = np.r_[c[0], c[:-1]]
    w = c*(.002+rng.random(n)*.002)
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                             o=o, h=np.maximum(o, c)+w, l=np.minimum(o, c)-w,
                             c=c, v=1000*(1+rng.random(n))))


def test_frozen_research_only_registry_and_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == S.MBP1H and st['reentry_lock'] is True
    assert st['mgmt'] == dict(stop_atr=1.5, tp1_r=1.0, tp1_frac=.5,
                               be_r=1.0, tp_r=2.5, max_bars=16)


def test_completed_4h_armed_levels_are_prefix_causal():
    raw = tape(); full = S.indicators(raw.copy())
    lf, sf = S.mbp1h_armed_levels(full, S.MBP1H)
    for cut in (700, 950, 1100):
        d = S.indicators(raw.iloc[:cut].copy())
        l, s = S.mbp1h_armed_levels(d, S.MBP1H)
        assert np.array_equal(l, lf.iloc[:cut], equal_nan=True)
        assert np.array_equal(s, sf.iloc[:cut], equal_nan=True)


def test_literal_pullback_then_separate_confirmation_and_gates(monkeypatch):
    d = S.indicators(tape(320)); i = 280
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'e20'] = 102.0; d.loc[:, 'e50'] = 100.0
    d.loc[:, 'v'] = 1000.0
    d.loc[i-1, ['o', 'h', 'l', 'c']] = [100.5, 100.8, 100.3, 100.4]
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [100.4, 101.4, 100.3, 101.2, 1500.0]
    level_l = pd.Series(np.nan, index=d.index); level_l.loc[i-1:i] = 100.0
    level_s = pd.Series(np.nan, index=d.index)
    monkeypatch.setattr(S, 'mbp1h_armed_levels', lambda frame, p: (level_l.loc[frame.index], level_s.loc[frame.index]))
    got = S.sig_mtf_breakout_pullback_1h(d, S.MBP1H, {})
    assert not bool(got['le'].iloc[i-1]) and bool(got['le'].iloc[i])
    for name, (row, col, value) in (
        ('no_hold', (i-1, 'c', 99.8)), ('no_break', (i, 'c', 100.7)),
        ('weak_body', (i, 'o', 101.0)), ('volume', (i, 'v', 1100.0)),
        ('local_trend', (i, 'e20', 99.0)),
    ):
        x = d.copy(); x.loc[row, col] = value
        assert not bool(S.sig_mtf_breakout_pullback_1h(x, S.MBP1H, {})['le'].iloc[i]), name


def test_one_hour_wrapper_scope_restores_shared_runner_table():
    before = dict(B.TFS); assert '1h' not in before
    with M.one_hour_scope():
        assert B.TFS['1h'] == 3_600_000
    assert B.TFS == before
