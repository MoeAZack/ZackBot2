"""TLP-15 Stage A: frozen mechanics, cost isolation and prefix causality."""
import numpy as np
import pandas as pd

import strategies as S


KEY = 'trend_liquidity_pullback_15m'


def tape(n=520, seed=71):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, .003, n)))
    o = np.r_[c[0], c[:-1]]
    w = c * (.002 + rng.random(n) * .002)
    return pd.DataFrame(dict(t=pd.date_range('2025-01-01', periods=n, freq='15min', tz='UTC'),
                             o=o, h=np.maximum(o, c)+w, l=np.minimum(o, c)-w,
                             c=c, v=1000*(1+rng.random(n))))


def literal_long():
    n, i = 280, 250
    d = S.indicators(pd.DataFrame(dict(
        t=pd.date_range('2025-01-01', periods=n, freq='15min', tz='UTC'),
        o=np.full(n, 101.0), h=np.full(n, 101.5), l=np.full(n, 100.7),
        c=np.full(n, 101.0), v=np.full(n, 1000.0))))
    d.loc[:, 'atr'] = 1.0; d.loc[:, 'adx'] = 20.0
    d.loc[:, 'e20'] = 101.6; d.loc[:, 'e50'] = np.linspace(100.0, 100.8, n); d.loc[:, 'e200'] = 99.0
    d.loc[i, 'adx'] = 30.0
    d.loc[i-2, ['o', 'h', 'l', 'c']] = [102.2, 102.3, 101.7, 102.0]
    d.loc[i-1, ['o', 'h', 'l', 'c']] = [102.0, 102.1, 101.2, 101.5]
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [101.5, 102.5, 101.4, 102.4, 2500.0]
    return d, i


def test_frozen_research_only_registry_and_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '15m' and st['sides'] == 'both'
    assert st['when_by_side'] == {'long': 'bull', 'short': 'bear'}
    assert st['params'] == S.TLP15 and st['reentry_lock'] == 8
    assert st['mgmt'] == dict(stop_atr=1.25, tp1_r=1.0, tp1_frac=.5,
                               be_r=1.0, tp_r=2.0, max_bars=16)


def test_literal_long_and_each_confirmation_gate(monkeypatch):
    d, i = literal_long()
    pdi = pd.Series(30.0, index=d.index); ndi = pd.Series(10.0, index=d.index)
    monkeypatch.setattr(S, 'tlp15_directional_strength', lambda frame: (pdi.loc[frame.index], ndi.loc[frame.index]))
    got = S.sig_trend_liquidity_pullback_15m(d, S.TLP15, {})
    assert bool(got['le'].iloc[i]) and not bool(got['se'].iloc[i])
    mutations = [
        ('no_pullback', (i-2, 'c', 101.4)),
        ('ema50_breach', (i-1, 'l', 100.0)),
        ('no_prior_break', (i, 'c', 102.0)),
        ('weak_body', (i, 'o', 102.2)),
        ('weak_close_location', (i, 'h', 103.0)),
        ('weak_liquidity', (i, 'v', 1100.0)),
        ('adx_not_rising', (i, 'adx', 20.0)),
        ('trend_not_strict', (i, 'e20', 100.5)),
    ]
    for name, (row, col, value) in mutations:
        x = d.copy(); x.loc[row, col] = value
        out = S.sig_trend_liquidity_pullback_15m(x, S.TLP15, {})
        assert not bool(out['le'].iloc[i]), name


def test_directional_strength_gate_is_side_specific(monkeypatch):
    d, i = literal_long()
    pdi = pd.Series(10.0, index=d.index); ndi = pd.Series(30.0, index=d.index)
    monkeypatch.setattr(S, 'tlp15_directional_strength', lambda frame: (pdi.loc[frame.index], ndi.loc[frame.index]))
    assert not bool(S.sig_trend_liquidity_pullback_15m(d, S.TLP15, {})['le'].iloc[i])


def test_future_mutation_cannot_change_prior_signals():
    raw = tape(); cut = 410
    before = S.sig_trend_liquidity_pullback_15m(S.indicators(raw.iloc[:cut].copy()), S.TLP15, {})
    changed = raw.copy()
    changed.loc[cut:, ['o', 'h', 'l', 'c', 'v']] *= [20, 25, .2, 30, 40]
    after = S.sig_trend_liquidity_pullback_15m(S.indicators(changed), S.TLP15, {})
    for key in ('le', 'se', 'lx', 'sx', 'atr'):
        assert np.array_equal(np.asarray(before[key]), np.asarray(after[key])[:cut], equal_nan=True), key
