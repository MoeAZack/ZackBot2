"""VRC-15 Stage-A: frozen research isolation and causal signal mechanics."""
import numpy as np
import pandas as pd

import strategies as S


KEY = 'vwap_reclaim_15m'


def tape(n=96):
    t = pd.date_range('2026-01-01', periods=n, freq='15min', tz='UTC')
    c = np.linspace(100, 104, n)
    o = np.r_[c[0], c[:-1]]
    return S.indicators(pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + .15,
                                           l=np.minimum(o, c) - .15, c=c, v=np.full(n, 1000.0))))


def test_frozen_research_only_registry_and_costed_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '15m' and st['sides'] == 'both'
    assert st['params'] == S.VRC15 == dict(session_min_bars=8, vol_median_bars=20, body_atr=.20,
                                           max_deviation_atr=1.0, stop_atr=1.25, cost_x=4.0)
    assert st['reentry_lock'] == 8
    assert st['mgmt'] == dict(stop_atr=1.25, tp1_r=1.0, tp1_frac=.5, be_r=1.0, tp_r=1.8, max_bars=16)


def test_session_vwap_is_prior_only_and_resets_at_utc_midnight():
    d = tape(97)
    vw, count = S.vrc15_session_vwap_before(d)
    assert np.isnan(vw.iloc[0]) and count.iloc[0] == 0
    expected = ((d.h.iloc[:8] + d.l.iloc[:8] + d.c.iloc[:8]) / 3).mean()
    assert np.isclose(vw.iloc[8], expected) and count.iloc[8] == 8
    assert np.isnan(vw.iloc[-1]) and count.iloc[-1] == 0  # first candle of day two


def test_prefix_causality_future_mutation_cannot_move_old_signals():
    d = tape()
    cut = 72
    before = S.sig_vwap_reclaim_15m(d.iloc[:cut].copy(), S.VRC15, {})
    changed = d.copy()
    changed.loc[cut:, ['o', 'h', 'l', 'c', 'v']] *= [20, 25, .2, 30, 40]
    after = S.sig_vwap_reclaim_15m(changed, S.VRC15, {})
    for key in ('le', 'se', 'lx', 'sx', 'atr'):
        a, b = np.asarray(before[key]), np.asarray(after[key])[:cut]
        assert np.array_equal(a, b, equal_nan=True), key


def test_each_confirmation_gate_is_required(monkeypatch):
    d = tape()
    i = 40
    # Freeze a simple reference so this test isolates admission terms rather than VWAP arithmetic.
    ref = pd.Series(101.0, index=d.index)
    cnt = pd.Series(20, index=d.index)
    monkeypatch.setattr(S, 'vrc15_session_vwap_before', lambda frame: (ref.loc[frame.index], cnt.loc[frame.index]))
    d.loc[i - 1, 'c'] = 100.9
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [100.8, 101.8, 100.7, 101.7, 2000.0]
    d.loc[i - 1, 'h'] = 101.2
    d.loc[:, 'e20'] = 103.0; d.loc[:, 'e50'] = 100.0
    d.loc[:, 'atr'] = 1.0
    base = S.sig_vwap_reclaim_15m(d, S.VRC15, {})
    assert bool(base['le'].iloc[i])
    mutations = [
        ('prior_above', {'c_prev': 101.1}), ('no_break', {'h_prev': 101.8}),
        ('small_body', {'o': 101.6}), ('low_volume', {'v': 500.0}),
        ('wrong_trend', {'e20': 99.0}), ('too_deep', {'c_prev': 99.5}),
    ]
    for name, m in mutations:
        x = d.copy()
        if 'c_prev' in m: x.loc[i - 1, 'c'] = m['c_prev']
        if 'h_prev' in m: x.loc[i - 1, 'h'] = m['h_prev']
        for field in ('o', 'v', 'e20'):
            if field in m: x.loc[i, field] = m[field]
        got = S.sig_vwap_reclaim_15m(x, S.VRC15, {})
        assert not bool(got['le'].iloc[i]), name


def test_long_short_rules_are_price_mirrors(monkeypatch):
    d = tape()
    ref = pd.Series(102.0, index=d.index); cnt = pd.Series(20, index=d.index)
    monkeypatch.setattr(S, 'vrc15_session_vwap_before', lambda frame: (ref.loc[frame.index], cnt.loc[frame.index]))
    long = S.sig_vwap_reclaim_15m(d, S.VRC15, {})
    k = 204.0
    m = d.copy()
    m['o'], m['h'], m['l'], m['c'] = k-d.o, k-d.l, k-d.h, k-d.c
    m['e20'], m['e50'], m['atr'] = k-d.e20, k-d.e50, d.atr
    monkeypatch.setattr(S, 'vrc15_session_vwap_before', lambda frame: (k-ref.loc[frame.index], cnt.loc[frame.index]))
    short = S.sig_vwap_reclaim_15m(m, S.VRC15, {})
    assert np.array_equal(long['le'], short['se'])
    assert np.array_equal(long['se'], short['le'])
