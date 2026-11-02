"""RAS-1H Stage A: causal 4h switch and frozen 1h sub-rules."""
import os
import sys

import numpy as np
import pandas as pd

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import run_breadth_candidate as B  # noqa: E402
import run_regime_adaptive_candidate as R  # noqa: E402


KEY = 'regime_adaptive_switch_1h'


def tape(n=1500, seed=31):
    rng = np.random.default_rng(seed)
    c = 100*np.exp(np.cumsum(rng.normal(0, .003, n)))
    o = np.r_[c[0], c[:-1]]; w = c*(.002+rng.random(n)*.002)
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='1h', tz='UTC'),
                             o=o, h=np.maximum(o,c)+w, l=np.minimum(o,c)-w,
                             c=c, v=1000*(1+rng.random(n))))


def test_frozen_research_only_registry_and_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '1h' and st['sides'] == 'both'
    assert st['params'] == S.RAS1H and st['reentry_lock'] is True
    assert st['mgmt'] == dict(stop_atr=1.5, tp1_r=1.0, tp1_frac=.5,
                               be_r=1.0, tp_r=2.0, max_bars=16)


def test_completed_4h_state_is_prefix_causal():
    raw = tape(); full = S.indicators(raw.copy()); tf, mf = S.ras1h_regime(full, S.RAS1H)
    for cut in (900, 1200, 1400):
        d = S.indicators(raw.iloc[:cut].copy()); t, m = S.ras1h_regime(d, S.RAS1H)
        assert np.array_equal(t, tf.iloc[:cut])
        assert np.array_equal(m, mf.iloc[:cut])


def test_trend_and_mean_modes_are_selected_before_entry(monkeypatch):
    d = S.indicators(tape(360)); i = 320
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'v'] = 1000.0
    d.loc[:, 'e20'] = 101.0; d.loc[:, 'e50'] = 100.0
    d.loc[i, ['o','h','l','c','v']] = [101.0,103.0,100.8,102.8,1200.0]
    trend = pd.Series(True, index=d.index); mean = pd.Series(False, index=d.index)
    monkeypatch.setattr(S, 'ras1h_regime', lambda frame, p: (trend.loc[frame.index], mean.loc[frame.index]))
    # Force a first six-bar breakout.
    d.loc[i-6:i-1, 'h'] = 102.0; d.loc[i-1, 'c'] = 101.5
    out = S.sig_regime_adaptive_switch_1h(d, S.RAS1H, {})
    assert bool(out['le'].iloc[i])
    # Same tape in mean state cannot use the trend rule.
    trend[:] = False; mean[:] = True
    assert not bool(S.sig_regime_adaptive_switch_1h(d, S.RAS1H, {})['le'].iloc[i])


def test_mean_mode_requires_stretch_then_reclaim(monkeypatch):
    d = S.indicators(tape(360)); i = 320
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'v'] = 1000.0; d.loc[:, 'e20'] = 100.0; d.loc[:, 'e50'] = 100.0
    d.loc[i-1, ['o','h','l','c']] = [99.0,99.2,98.0,98.2]
    d.loc[i, ['o','h','l','c','v']] = [98.2,99.8,98.1,99.6,1200.0]
    trend = pd.Series(False,index=d.index); mean = pd.Series(True,index=d.index)
    monkeypatch.setattr(S, 'ras1h_regime', lambda frame,p:(trend.loc[frame.index],mean.loc[frame.index]))
    assert bool(S.sig_regime_adaptive_switch_1h(d,S.RAS1H,{})['le'].iloc[i])
    d.loc[i-1,'c'] = 99.0
    assert not bool(S.sig_regime_adaptive_switch_1h(d,S.RAS1H,{})['le'].iloc[i])


def test_one_hour_wrapper_scope_restores_shared_runner_table():
    before=dict(B.TFS); assert '1h' not in before
    with R.one_hour_scope(): assert B.TFS['1h']==3_600_000
    assert B.TFS==before
