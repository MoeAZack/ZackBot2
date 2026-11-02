"""VER-15 Stage A: isolation, frozen mechanics, symmetry and causality."""
import numpy as np
import pandas as pd

import strategies as S


KEY = 'vol_expansion_retest_15m'


def tape(n=520, seed=41):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, .0025, n)))
    o = np.r_[c[0], c[:-1]]
    w = c * (.002 + rng.random(n) * .002)
    return pd.DataFrame(dict(t=pd.date_range('2025-01-01', periods=n, freq='15min', tz='UTC'),
                             o=o, h=np.maximum(o, c)+w, l=np.minimum(o, c)-w,
                             c=c, v=1000*(1+rng.random(n))))


def literal_long():
    n, i = 280, 250
    d = S.indicators(pd.DataFrame(dict(
        t=pd.date_range('2025-01-01', periods=n, freq='15min', tz='UTC'),
        o=np.full(n, 100.0), h=np.full(n, 101.0), l=np.full(n, 99.0),
        c=np.full(n, 100.0), v=np.full(n, 1000.0))))
    d.loc[:, 'atr'] = 1.0
    d.loc[:, 'bbw_pct'] = .50
    d.loc[:, 'e20'] = 103.0
    d.loc[:, 'e50'] = np.linspace(101.0, 102.0, n)
    d.loc[:, 'e200'] = 99.0
    d.loc[i-2, 'bbw_pct'] = .10
    d.loc[i-1, ['o', 'h', 'l', 'c', 'v']] = [100.0, 102.2, 99.8, 102.0, 2000.0]
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [102.0, 102.1, 101.05, 101.30, 1200.0]
    return d, i


def test_frozen_research_only_registry_and_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '15m' and st['sides'] == 'both'
    assert st['when_by_side'] == {'long': 'bull', 'short': 'bear'}
    assert st['params'] == S.VER15
    assert st['reentry_lock'] == 8
    assert st['mgmt'] == dict(stop_atr=1.25, tp1_r=1.0, tp1_frac=.5,
                               be_r=1.0, tp_r=2.0, max_bars=16)


def test_literal_long_requires_separate_breakout_then_retest():
    d, i = literal_long()
    got = S.sig_vol_expansion_retest_15m(d, S.VER15, {})
    assert not bool(got['le'].iloc[i-1])  # never chase the expansion candle
    assert bool(got['le'].iloc[i])
    mutations = [
        ('no_compression', (i-2, 'bbw_pct', .20)),
        ('weak_breakout_body', (i-1, 'o', 101.8)),
        ('weak_breakout_volume', (i-1, 'v', 1200.0)),
        ('no_retest', (i, 'l', 101.3)),
        ('no_reclaim', (i, 'c', 101.1)),
        ('trend_not_strict', (i, 'e20', 101.5)),
    ]
    for name, (row, col, value) in mutations:
        x = d.copy(); x.loc[row, col] = value
        out = S.sig_vol_expansion_retest_15m(x, S.VER15, {})
        assert not bool(out['le'].iloc[i]), name


def test_long_and_short_are_exact_price_mirrors():
    d, i = literal_long()
    long = S.sig_vol_expansion_retest_15m(d, S.VER15, {})
    k = 202.0
    m = d.copy()
    m['o'], m['h'], m['l'], m['c'] = k-d.o, k-d.l, k-d.h, k-d.c
    m['e20'], m['e50'], m['e200'] = k-d.e20, k-d.e50, k-d.e200
    short = S.sig_vol_expansion_retest_15m(m, S.VER15, {})
    assert bool(long['le'].iloc[i]) and bool(short['se'].iloc[i])
    assert np.array_equal(long['le'], short['se'])
    assert np.array_equal(long['se'], short['le'])


def test_future_mutation_cannot_change_prior_signals():
    raw = tape()
    cut = 410
    before = S.sig_vol_expansion_retest_15m(S.indicators(raw.iloc[:cut].copy()), S.VER15, {})
    changed = raw.copy()
    changed.loc[cut:, ['o', 'h', 'l', 'c', 'v']] *= [20, 25, .2, 30, 40]
    after = S.sig_vol_expansion_retest_15m(S.indicators(changed), S.VER15, {})
    for key in ('le', 'se', 'lx', 'sx', 'atr'):
        assert np.array_equal(np.asarray(before[key]), np.asarray(after[key])[:cut], equal_nan=True), key
