"""ORH-15 Stage A: causal session anchor, frozen hold rule and isolation."""
import numpy as np
import pandas as pd

import strategies as S


KEY = 'utc_opening_range_hold_15m'


def flat(n=192):
    return S.indicators(pd.DataFrame(dict(
        t=pd.date_range('2025-01-01', periods=n, freq='15min', tz='UTC'),
        o=np.full(n, 100.0), h=np.full(n, 101.0), l=np.full(n, 99.0),
        c=np.full(n, 100.0), v=np.full(n, 1000.0))))


def literal_long():
    d, i = flat(), 106
    d.loc[:, 'atr'] = 1.0
    d.loc[i-2, ['o', 'h', 'l', 'c']] = [100.0, 100.5, 99.7, 100.0]
    d.loc[i-1, ['o', 'h', 'l', 'c', 'v']] = [100.0, 101.7, 99.9, 101.5, 1500.0]
    d.loc[i, ['o', 'h', 'l', 'c', 'v']] = [101.4, 101.6, 100.9, 101.4, 800.0]
    return d, i


def test_frozen_research_only_registry_and_management():
    st = S.STRATEGIES[KEY]
    assert st['research_only'] is True and st['tf'] == '15m' and st['sides'] == 'both'
    assert 'when_by_side' not in st and st['params'] == S.ORH15 and st['reentry_lock'] == 8
    assert st['mgmt'] == dict(stop_atr=1.0, tp1_r=.8, tp1_frac=.5,
                               be_r=.8, tp_r=1.8, max_bars=12)


def test_opening_range_is_causal_complete_then_fixed_and_resets():
    d = flat(100)
    hi, lo, pos = S.orh15_opening_range(d, 4)
    assert hi.iloc[:4].isna().all() and lo.iloc[:4].isna().all()
    assert hi.iloc[4] == 101 and lo.iloc[4] == 99 and pos.iloc[4] == 4
    d.loc[10, ['h', 'l']] = [150.0, 50.0]
    hi2, lo2, _ = S.orh15_opening_range(d, 4)
    assert hi2.iloc[10] == 101 and lo2.iloc[10] == 99
    assert np.isnan(hi2.iloc[96]) and np.isnan(lo2.iloc[96])  # new day, not yet complete


def test_literal_break_then_separate_hold_and_all_gates():
    d, i = literal_long()
    got = S.sig_utc_opening_range_hold_15m(d, S.ORH15, {})
    assert not bool(got['le'].iloc[i-1]) and bool(got['le'].iloc[i])
    mutations = [
        ('no_cross', (i-2, 'c', 101.2)),
        ('break_volume', (i-1, 'v', 1100.0)),
        ('hold_close', (i, 'c', 100.9)),
        ('hold_low', (i, 'l', 100.7)),
        ('hold_volume', (i, 'v', 700.0)),
    ]
    for name, (row, col, value) in mutations:
        x = d.copy(); x.loc[row, col] = value
        out = S.sig_utc_opening_range_hold_15m(x, S.ORH15, {})
        assert not bool(out['le'].iloc[i]), name


def test_long_short_rules_are_exact_price_mirrors():
    d, i = literal_long(); long = S.sig_utc_opening_range_hold_15m(d, S.ORH15, {})
    k = 200.0; m = d.copy()
    m['o'], m['h'], m['l'], m['c'] = k-d.o, k-d.l, k-d.h, k-d.c
    short = S.sig_utc_opening_range_hold_15m(m, S.ORH15, {})
    assert bool(long['le'].iloc[i]) and bool(short['se'].iloc[i])
    assert np.array_equal(long['le'], short['se'])
    assert np.array_equal(long['se'], short['le'])


def test_future_mutation_cannot_change_prior_signals():
    d = flat(192); cut = 140
    before = S.sig_utc_opening_range_hold_15m(d.iloc[:cut].copy(), S.ORH15, {})
    changed = d.copy()
    changed.loc[cut:, ['o', 'h', 'l', 'c', 'v']] *= [20, 25, .2, 30, 40]
    after = S.sig_utc_opening_range_hold_15m(changed, S.ORH15, {})
    for key in ('le', 'se', 'lx', 'sx', 'atr'):
        assert np.array_equal(np.asarray(before[key]), np.asarray(after[key])[:cut], equal_nan=True), key
