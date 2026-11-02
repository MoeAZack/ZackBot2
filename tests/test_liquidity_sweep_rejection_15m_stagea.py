"""LSR-15 Stage A: same-candle sweep, dynamic risk and strict cooldown."""
import numpy as np
import pandas as pd

import strategies as S


KEY = 'liquidity_sweep_rejection_15m'


def flat(n=400):
    return S.indicators(pd.DataFrame(dict(
        t=pd.date_range('2025-01-01', periods=n, freq='15min', tz='UTC'),
        o=np.full(n, 100.0), h=np.full(n, 101.0), l=np.full(n, 99.0),
        c=np.full(n, 100.0), v=np.full(n, 1000.0))))


def literal_long():
    d, i = flat(), 250
    d.loc[:, 'atr'] = 1.0
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [99.5, 99.8, 98.5, 99.4, 2500.0]
    return d, i


def test_frozen_research_only_registry_and_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '15m' and st['sides'] == 'both'
    assert st['params'] == S.LSR15 and st['params']['cooldown_bars'] == 16
    assert st['reentry_lock'] is True
    assert st['mgmt'] == dict(stop_atr=1.0, tp1_r=.8, tp1_frac=.5,
                               be_r=.8, tp_r=1.5, max_bars=12)


def test_literal_same_candle_sweep_and_every_gate():
    d, i = literal_long(); got = S.sig_liquidity_sweep_rejection_15m(d, S.LSR15, {})
    assert bool(got['le'].iloc[i]) and not bool(got['se'].iloc[i])
    assert np.isclose(got['atr'][i], 1.0)  # sweep distance + buffer, not generic ATR
    mutations = [
        ('no_sweep', ('l', 98.8)),
        ('outside_close', ('c', 98.9)),
        ('small_wick', ('l', 98.7)),
        ('bad_location', ('h', 100.5)),
        ('low_volume', ('v', 1900.0)),
    ]
    for name, (col, value) in mutations:
        x = d.copy(); x.loc[i, col] = value
        out = S.sig_liquidity_sweep_rejection_15m(x, S.LSR15, {})
        assert not bool(out['le'].iloc[i]), name


def test_long_short_rules_are_price_mirrors():
    d, i = literal_long(); long = S.sig_liquidity_sweep_rejection_15m(d, S.LSR15, {})
    k = 200.0; m = d.copy()
    m['o'], m['h'], m['l'], m['c'] = k-d.o, k-d.l, k-d.h, k-d.c
    short = S.sig_liquidity_sweep_rejection_15m(m, S.LSR15, {})
    assert bool(short['se'].iloc[i])
    assert np.array_equal(long['le'], short['se'])
    assert np.array_equal(long['se'], short['le'])
    assert np.allclose(long['atr'], short['atr'], equal_nan=True)


def test_signal_level_cooldown_blocks_next_sixteen_bars():
    d, i = literal_long()
    d.loc[i+5, ['o', 'h', 'l', 'c', 'v']] = [99.0, 99.3, 98.0, 98.9, 2500.0]
    d.loc[i+22, ['o', 'h', 'l', 'c', 'v']] = [98.5, 98.8, 97.5, 98.4, 2500.0]
    out = S.sig_liquidity_sweep_rejection_15m(d, S.LSR15, {})
    assert bool(out['le'].iloc[i]) and not bool(out['le'].iloc[i+5]) and bool(out['le'].iloc[i+22])


def test_future_mutation_cannot_change_prior_signals():
    d = flat(); cut = 310
    before = S.sig_liquidity_sweep_rejection_15m(d.iloc[:cut].copy(), S.LSR15, {})
    changed = d.copy()
    changed.loc[cut:, ['o', 'h', 'l', 'c', 'v']] *= [20, 25, .2, 30, 40]
    after = S.sig_liquidity_sweep_rejection_15m(changed, S.LSR15, {})
    for key in ('le', 'se', 'lx', 'sx', 'atr'):
        assert np.array_equal(np.asarray(before[key]), np.asarray(after[key])[:cut], equal_nan=True), key
