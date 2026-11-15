"""LONGSCOUT-V1: causality and regime-rule tests on synthetic data (no network, no repo data)."""
import os, sys, json
import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'research')]
import strategies as S
import longscout_v1 as L

L.register()


def _mk(n, freq, seed):
    rng = np.random.default_rng(seed)
    t = pd.date_range('2021-01-01', periods=n, freq=freq)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, .01, n))); l = np.minimum(o, c) * (1 - abs(rng.normal(0, .01, n)))
    return pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=rng.lognormal(10, 1, n)))


def _sig(key, coin, btc):
    L.set_btc(btc)
    ctx = S.build_context({'BTCUSDT': S.indicators(btc), 'X': S.indicators(coin)})['X']
    return S.signals(key, S.indicators(coin), ctx, None, mask_sides=False)


@pytest.mark.parametrize('key', sorted(L.CANDIDATES))
def test_signals_use_no_future_bars(key):
    tf = L.CANDIDATES[key]['tf']
    n, freq = (2600, '4h') if tf == '4h' else (900, '1D')
    coin, btc = _mk(n, freq, 7), _mk(n, freq, 8)
    full = _sig(key, coin, btc)
    for cut in (n // 2, n - 37, n - 1):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)
        if 'atr' in full:
            np.testing.assert_allclose(part['atr'], full['atr'][:cut], equal_nan=True)


def test_regime_r1_states_and_warmup():
    idx = pd.date_range('2022-01-01', periods=300, freq='1D')
    up = pd.Series(np.linspace(100, 400, 300), index=idx)
    r = L.regime_daily(up)
    assert r.iloc[:119].isna().all() and (r.iloc[119:] == 1).all()
    assert (L.regime_daily(up[::-1].set_axis(idx)).iloc[119:] == -1).all()
    flat = pd.Series(100.0 + np.sin(np.arange(300)), index=idx)
    assert set(L.regime_daily(flat).dropna().unique()) <= {0.0, 1.0, -1.0}


def test_daily_value_reaches_4h_rows_only_after_the_day_closes():
    t = pd.Series(pd.date_range('2023-01-01', periods=18, freq='4h'))          # 3 days
    vals = pd.Series([1.0, 2.0, 3.0], index=pd.date_range('2023-01-01', periods=3, freq='1D'))
    got = L.on_rows(vals, t)
    assert np.isnan(got[:5]).all()                       # day 1 bars that close before 2023-01-02 00:00
    assert got[5] == 1.0 and got[6] == 1.0 and got[11] == 2.0 and got[17] == 3.0


def test_daily_closes_drop_incomplete_days():
    t = pd.Series(pd.date_range('2023-01-01', periods=12, freq='4h')).drop(index=[3]).reset_index(drop=True)
    dc = L.daily_closes(t, np.arange(len(t), dtype=float))
    assert list(dc.index) == [pd.Timestamp('2023-01-02')]


def test_frozen_cases_match_registered_rules():
    p = os.path.join(ROOT, 'research', 'extra31_screen', 'longscout_v1_cases.json')
    with open(p, encoding='utf-8') as f:
        cases = json.load(f)['cases']
    for k, v in L.CANDIDATES.items():
        c = cases[k]
        assert c['management'] == v['mgmt'] and c['params'] == v['params'] and c['tf'] == v['tf'] and c['side'] == v['sides']


import longscout_v2 as L2
L2.register()


@pytest.mark.parametrize('key', sorted(L2.CANDIDATES))
def test_v2_signals_use_no_future_bars(key):
    tf = L2.CANDIDATES[key]['tf']
    n, freq = (2600, '4h') if tf == '4h' else (900, '1D')
    coin, btc = _mk(n, freq, 11), _mk(n, freq, 12)
    full = _sig(key, coin, btc)
    for cut in (n // 2, n - 37, n - 1):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)


def test_v2_frozen_cases_match_registered_rules():
    p = os.path.join(ROOT, 'research', 'extra31_screen', 'longscout_v2_cases.json')
    with open(p, encoding='utf-8') as f:
        cases = json.load(f)['cases']
    for k, v in L2.CANDIDATES.items():
        c = cases[k]
        assert c['management'] == v['mgmt'] and c['params'] == v['params'] and c['tf'] == v['tf'] and c['side'] == v['sides']


import longscout_v3 as L3
L3.register()


@pytest.mark.parametrize('key', sorted(L3.CANDIDATES))
def test_v3_signals_use_no_future_bars(key):
    coin, btc = _mk(2600, '4h', 21), _mk(2600, '4h', 22)
    full = _sig(key, coin, btc)
    for cut in (1300, 2563, 2599):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)


import longscout_v4 as L4
L4.register()


@pytest.mark.parametrize('key', sorted(L4.CANDIDATES))
def test_v4_signals_use_no_future_bars(key):
    coin, btc = _mk(2600, '4h', 31), _mk(2600, '4h', 32)
    full = _sig(key, coin, btc)
    for cut in (1300, 2563, 2599):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)
        if 'atr' in full:
            np.testing.assert_allclose(part['atr'], full['atr'][:cut], equal_nan=True)


import longscout_v5 as L5
L5.register()


@pytest.mark.parametrize('key', sorted(L5.CANDIDATES))
def test_v5_signals_use_no_future_bars(key):
    coin, btc = _mk(2600, '4h', 41), _mk(2600, '4h', 42)
    full = _sig(key, coin, btc)
    for cut in (1300, 2563, 2599):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)


import longscout_v6 as L6
L6.register()


@pytest.mark.parametrize('key', sorted(L6.CANDIDATES))
def test_v6_signals_use_no_future_bars(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    full = _sig(key, coin, btc)
    for cut in (1300, 2563, 2599):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)
        if 'atr' in full:
            np.testing.assert_allclose(part['atr'], full['atr'][:cut], equal_nan=True)


@pytest.mark.parametrize('key', sorted(L6.CANDIDATES))
def test_v6_signals_fire_on_synthetic_data(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    s = _sig(key, coin, btc)
    assert (np.asarray(s['le']).sum() + np.asarray(s['se']).sum()) > 0, key


import longscout_v7 as L7
L7.register()


@pytest.mark.parametrize('key', sorted(L7.CANDIDATES))
def test_v7_signals_use_no_future_bars(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    full = _sig(key, coin, btc)
    for cut in (1300, 2563, 2599):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)
        if 'atr' in full:
            np.testing.assert_allclose(part['atr'], full['atr'][:cut], equal_nan=True)


@pytest.mark.parametrize('key', sorted(L7.CANDIDATES))
def test_v7_signals_fire_on_synthetic_data(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    if key == 's7_blowoff_fade_short_4h':                  # needs a +50% week then a sharp red bar: scale all prices
        f = np.ones(len(coin)); f[1500:1542] = np.linspace(1, 1.8, 42); f[1542:] = 1.8; f[1532:] *= 0.92
        coin = coin.assign(o=coin.o * np.r_[f[0], f[:-1]], h=coin.h * f * 1.0, l=coin.l * np.minimum(f, np.r_[f[0], f[:-1]]), c=coin.c * f)
        coin = coin.assign(h=np.maximum(coin.h, coin[['o', 'c']].max(axis=1)), l=np.minimum(coin.l, coin[['o', 'c']].min(axis=1)))
    s = _sig(key, coin, btc)
    assert (np.asarray(s['le']).sum() + np.asarray(s['se']).sum()) > 0, key


@pytest.mark.parametrize('key', sorted(L6.CANDIDATES) + sorted(L7.CANDIDATES))
def test_v6_v7_dense_prefix_cuts(key):
    """Many cuts: a one-bar lookahead only shows when a signal sits on the cut's last bar, so three cuts can miss it."""
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    full = _sig(key, coin, btc)
    for cut in range(1250, 2600, 15):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)
        if 'atr' in full:
            np.testing.assert_allclose(part['atr'], full['atr'][:cut], equal_nan=True)


import longscout_v8 as L8
L8.register()


@pytest.mark.parametrize('key', sorted(L8.CANDIDATES))
def test_v8_dense_prefix_cuts_and_fires(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    full = _sig(key, coin, btc)
    assert (np.asarray(full['le']).sum() + np.asarray(full['se']).sum()) > 0, key
    for cut in list(range(1250, 2300, 15)) + list(range(2300, 2600)):   # every bar near the end: a 1-bar lookahead
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))  # shows only at cut-1
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)


import longscout_v9 as L9
L9.register()


@pytest.mark.parametrize('key', sorted(L9.CANDIDATES))
def test_v9_dense_prefix_cuts_and_fires(key):
    a, b = (56, 55) if key == 'l9_decoupled_strength_4h' else (55, 56)   # needs coin rising while BTC falls
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    assert (np.asarray(full['le']).sum() + np.asarray(full['se']).sum()) > 0, key
    for cut in list(range(1250, 2300, 15)) + list(range(2300, 2600)):
        part = _sig(key, coin.iloc[:cut].reset_index(drop=True), btc.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:cut]).all(), (key, k, cut)


def test_v9_weekly_range_uses_only_closed_monday_bars():
    t = pd.date_range('2024-01-01', periods=12, freq='4h')          # 2024-01-01 is a Monday
    d = pd.DataFrame(dict(t=t, o=1.0, h=[1, 1, 5, 1, 1, 1, 4, 6, 4, 7, 4, 4], l=0.5, c=[1, 1, 1, 1, 1, 1, 4, 6, 4, 7, 4, 4], v=1.0))
    up = pd.Series(1.0, index=d.index)
    import unittest.mock as um
    with um.patch.object(L9.L1, 'coin_regime', lambda _d: up.values):
        s = L9.sig_weekly_range_breakout(d, {}, None)
    assert list(np.flatnonzero(s['le'])) == [7]                    # Monday high 5; first close above it on Tuesday = bar 7


@pytest.mark.parametrize('key', sorted(L9.CANDIDATES) + ['r6_regime_flip_4h'])
def test_signal_bars_survive_a_cut_right_after_them(key):
    """Cut right after every bar where an entry fires (and a sample of exit flips): a one-bar lookahead drops it."""
    a, b = (56, 55) if key == 'l9_decoupled_strength_4h' else (55, 56)
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]))
    assert bars, key
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v10 as L10
L10.register()


def _v10_data(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    if 'base' in key:                                       # calmer path so tight 15-day ranges occur
        coin = coin.assign(**{k: 100 * (coin[k] / 100) ** 0.3 for k in ('o', 'h', 'l', 'c')})
    return coin, btc


@pytest.mark.parametrize('key', sorted(L10.CANDIDATES))
def test_v10_cut_right_after_signals_and_prefixes(key):
    coin, btc = _v10_data(key)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599})
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v11 as L11
L11.register()


def _v11_data(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    if 'capitulation' in key:                               # add sharp BTC drops so the trigger exists
        f = np.ones(len(btc))
        for i in range(1300, 2600, 60): f[i:] *= 0.88
        btc = btc.assign(o=btc.o * np.r_[f[0], f[:-1]], h=btc.h * np.maximum(f, np.r_[f[0], f[:-1]]), l=btc.l * f, c=btc.c * f)
    if 'mature' in key:                                     # long steady trends so a 30-day unbroken regime exists
        g = np.exp((1 if 'trend_breakout' in key else -1) * 0.003 * np.arange(len(coin)))
        coin = coin.assign(**{k: coin[k] * g for k in ('o', 'h', 'l', 'c')})
        btc = btc.assign(**{k: btc[k] * g for k in ('o', 'h', 'l', 'c')})
    return coin, btc


@pytest.mark.parametrize('key', sorted(L11.CANDIDATES))
def test_v11_cut_right_after_signals_and_prefixes(key):
    coin, btc = _v11_data(key)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599})
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v12 as L12
L12.register()


@pytest.mark.parametrize('key', sorted(L12.CANDIDATES))
def test_v12_cut_right_after_signals_and_prefixes(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | {1300, 2563, 2599} | set(range(2500, 2600, 7)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v12_prior_week_levels_use_only_the_completed_week():
    t = pd.date_range('2024-01-01', periods=84, freq='4h')         # two weeks, Monday start
    h = np.r_[np.full(42, 10.0), np.full(42, 20.0)]; h[50] = 99.0
    d = pd.DataFrame(dict(t=t, o=1.0, h=h, l=1.0, c=1.0, v=1.0))
    wk, ph, pl = L12._prior_week(d)
    assert np.isnan(ph[:42]).all() and (ph[42:] == 10.0).all()     # week 2 sees week 1's high only, never its own 99


import longscout_v13 as L13
L13.register()


@pytest.mark.parametrize('key', sorted(L13.CANDIDATES))
def test_v13_cut_right_after_signals_and_prefixes(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v13_daily_levels_reach_bars_only_after_the_day_closes():
    t = pd.date_range('2024-01-01', periods=18, freq='4h')
    h = np.r_[np.full(6, 10.0), np.full(6, 20.0), np.full(6, 30.0)]
    d = pd.DataFrame(dict(t=t, o=1.0, h=h, l=1.0, c=1.0, v=1.0))
    _, ph, _ = L13._stairs(d, 1, False)
    assert np.isnan(ph[:5]).all() and (ph[5:11] == 10).all() and (ph[11:17] == 20).all() and ph[17] == 30


def test_v13_incomplete_days_are_not_used():
    t = pd.Series(pd.date_range('2024-01-01', periods=12, freq='4h')).drop(index=[3]).reset_index(drop=True)
    d = pd.DataFrame(dict(t=t, o=1.0, h=np.arange(11.0), l=1.0, c=1.0, v=1.0))
    hi, lo = L13._daily_hl(d)
    assert list(hi.index) == [pd.Timestamp('2024-01-02')]


import longscout_v14 as L14
L14.register()


@pytest.mark.parametrize('key', sorted(L14.CANDIDATES))
def test_v14_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('s14') else (55, 56)     # s14 needs BTC falling
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v15 as L15
L15.register()


@pytest.mark.parametrize('key', sorted(L15.CANDIDATES))
def test_v15_cut_right_after_signals_and_prefixes(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v15_pivots_reach_bars_only_after_the_day_closes():
    t = pd.date_range('2024-01-01', periods=12, freq='4h')
    d = pd.DataFrame(dict(t=t, o=1.0, h=np.r_[np.full(6, 12.0), np.full(6, 99.0)], l=np.r_[np.full(6, 6.0), np.full(6, 1.0)],
                          c=np.r_[np.full(6, 9.0), np.full(6, 50.0)], v=1.0))
    r1, s1 = L15._pivots(d)
    assert np.isnan(r1[:5]).all() and np.allclose(r1[5:11], 2 * 9 - 6) and np.allclose(s1[5:11], 2 * 9 - 12)


import longscout_v16 as L16
L16.register()


@pytest.mark.parametrize('key', sorted(L16.CANDIDATES))
def test_v16_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l16_inside') else (55, 56)   # needs a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v16_inside_day_needs_the_previous_calendar_day():
    t = pd.Series(pd.date_range('2024-01-01', periods=24, freq='4h')).drop(index=range(6, 12)).reset_index(drop=True)
    h = np.r_[np.full(6, 10.0), np.full(6, 9.0), np.full(6, 8.0)]; l = np.r_[np.full(6, 1.0), np.full(6, 2.0), np.full(6, 3.0)]
    d = pd.DataFrame(dict(t=t, o=5.0, h=h, l=l, c=5.0, v=1.0))   # days 1, 3, 4 (day 2 missing)
    ins, H, _ = L16._inside_day(d)
    assert not ins[:17].any() and ins[17]          # day 4 inside day 3 is known only when day 4 closes; day 3 vs day 1 never


import longscout_v17 as L17
L17.register()


@pytest.mark.parametrize('key', sorted(L17.CANDIDATES))
def test_v17_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l17') else (55, 56)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v18 as L18
L18.register()


@pytest.mark.parametrize('key', sorted(L18.CANDIDATES))
def test_v18_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l18') else (55, 56)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v18_sunday_last_bar_is_the_2000_utc_bar():
    d = pd.DataFrame(dict(t=pd.date_range('2024-01-06', periods=12, freq='4h'), o=1.0, h=1.0, l=1.0, c=1.0, v=1.0))
    assert list(np.flatnonzero(L18._sunday_last(d))) == [11]       # 2024-01-07 is a Sunday; bar 11 opens 20:00


import longscout_v19 as L19
L19.register()


@pytest.mark.parametrize('key', sorted(L19.CANDIDATES))
def test_v19_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l19') else (55, 56)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    if 'hammer' in key or 'star' in key:                     # long wicks on every 23rd bar so the pattern exists
        m = np.arange(len(coin)) % 23 == 0
        coin = coin.assign(l=np.where(m, coin.l * 0.94, coin.l), h=np.where(m, coin.h * 1.06, coin.h))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)
        if 'atr' in full:
            np.testing.assert_allclose(part['atr'], full['atr'][:i + 1], equal_nan=True)


import longscout_v20 as L20
L20.register()


@pytest.mark.parametrize('key', sorted(L20.CANDIDATES))
def test_v20_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l20') else (55, 56)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    if 'quiet' in key:                                         # quiet stretches, then normal volume again
        q = (np.arange(len(coin)) % 200) >= 150
        coin = coin.assign(v=np.where(q, coin.v * 0.2, coin.v * np.where(np.arange(len(coin)) % 200 < 10, 3.0, 1.0)))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v21 as L21
L21.register()


@pytest.mark.parametrize('key', sorted(L21.CANDIDATES))
def test_v21_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l21') else (55, 56)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v22 as L22
L22.register()


@pytest.mark.parametrize('key', sorted(L22.CANDIDATES))
def test_v22_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l22') else (55, 56)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    if 'expansion' in key:                                     # a few wide days so the pattern exists
        m = (np.arange(len(coin)) % 54) == 0                   # first bar of every 9th day gets a long wick
        if key.startswith('l22'): coin = coin.assign(l=np.where(m, coin.l * 0.85, coin.l))   # close near the day's top
        else: coin = coin.assign(h=np.where(m, coin.h * 1.15, coin.h))                        # close near the day's bottom
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v23 as L23
L23.register()


@pytest.mark.parametrize('key', sorted(L23.CANDIDATES))
def test_v23_cut_right_after_signals_and_prefixes(key):
    a, b = (56, 55) if key.startswith('l23') else (55, 56)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


import longscout_v24 as L24
L24.register()


@pytest.mark.parametrize('key', sorted(L24.CANDIDATES))
def test_v24_cut_right_after_signals_and_prefixes(key):
    coin, btc = _mk(2600, '4h', 55), _mk(2600, '4h', 56)
    if 'day' in key:                                           # every 15th day: a +/-12% day on 4x volume
        f = np.ones(len(coin)); ev = (np.arange(len(coin)) // 6) % 15 == 7
        step = 1.02 if key.startswith('l24') else 0.98
        for i in np.flatnonzero(ev): f[i:] *= step
        coin = coin.assign(**{k: coin[k] * f for k in ('o', 'h', 'l', 'c')}, v=np.where(ev, coin.v * 4, coin.v))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v24_return_rank_values_match_on_every_prefix():
    coin = S.indicators(_mk(2600, '4h', 55)); p = L24.CANDIDATES['r24_return_rank_4h']['params']
    full = L24._rank(coin, p).values
    for cut in range(2200, 2600, 13):
        part = L24._rank(coin.iloc[:cut].reset_index(drop=True), p).values
        np.testing.assert_array_equal(part, full[:cut])


import longscout_v25 as L25
L25.register()


@pytest.mark.parametrize('key', sorted(L25.CANDIDATES))
def test_v25_cut_right_after_signals_and_prefixes(key):
    a, b = (55, 56) if key.startswith('s25') else (56, 55)       # longs need a mostly rising coin
    coin, btc = _mk(2600, '4h', a), _mk(2600, '4h', b)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v25_weekly_matches_independent_resample_and_prefixes():
    c = S.indicators(_mk(2600, '4h', 56)); t = pd.to_datetime(c.t)
    got = L25._weekly(c, dict(weeks=12), True)
    s = pd.Series(c.c.values, index=t); cnt = s.resample('W-SUN').count(); W = s.resample('W-SUN').last()[cnt == 42]
    ok = W[W > W.rolling(12).max().shift()]
    exp = np.isin(t.values, (ok.index + pd.Timedelta(days=1)).values)
    assert got.sum() > 5 and (got == exp).all()
    for cut in range(1500, 2600, 23):
        part = L25._weekly(c.iloc[:cut].reset_index(drop=True), dict(weeks=12), True)
        np.testing.assert_array_equal(part, got[:cut])


def test_v25_heikin_flip_values_match_on_every_prefix():
    c = S.indicators(_mk(2600, '4h', 56))
    for up in (True, False):
        full = L25._ha_flip(c, dict(run=3), up)
        assert full.sum() > 5
        for cut in range(600, 2600, 17):
            np.testing.assert_array_equal(L25._ha_flip(c.iloc[:cut].reset_index(drop=True), dict(run=3), up), full[:cut])


def test_v25_incomplete_previous_day_blocks_day_entries():
    c = S.indicators(_mk(2600, '4h', 56)); t = pd.to_datetime(c.t)
    full = L25._ha_flip(c, dict(run=3), True)
    i = int(np.flatnonzero(full)[2])
    drop = c.drop(index=i - 2).reset_index(drop=True)          # previous day loses a bar -> not complete
    out = L25._ha_flip(drop, dict(run=3), True)
    j = int(np.flatnonzero(pd.to_datetime(drop.t).values == t.values[i])[0])
    assert not out[j]


import longscout_v26 as L26
L26.register()


def _ms(df):
    return df.assign(t=pd.to_datetime(df.t).astype('datetime64[ms]'))     # the real loader yields datetime64[ms]


@pytest.mark.parametrize('key', sorted(L26.CANDIDATES))
def test_v26_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    a, b = (55, 56) if key.startswith('s26') else (56, 55)
    coin, btc = _ms(_mk(2600, '4h', a)), _ms(_mk(2600, '4h', b))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


@pytest.mark.parametrize('unit', ['ns', 'ms', 's'])
def test_v26_timestamp_unit_does_not_change_signals(unit):
    base = S.indicators(_mk(2600, '4h', 56))
    other = base.assign(t=pd.to_datetime(base.t).astype(f'datetime64[{unit}]'))
    for up in (True, False):
        w0, w1 = L26._weekly(base, dict(weeks=12), up), L26._weekly(other, dict(weeks=12), up)
        h0, h1 = L26._ha_flip(base, dict(run=3), up), L26._ha_flip(other, dict(run=3), up)
        assert w0.sum() > 0 and h0.sum() > 0
        np.testing.assert_array_equal(w0, w1); np.testing.assert_array_equal(h0, h1)


def test_v25_defect_recorded_ms_timestamps_gave_no_day_entries():
    c = S.indicators(_ms(_mk(2600, '4h', 56)))                 # documents why V25 L25A/S25A/R25A opened zero trades
    assert L25._weekly(c, dict(weeks=12), True).sum() == 0 and L25._ha_flip(c, dict(run=3), True).sum() == 0
    assert L26._weekly(c, dict(weeks=12), True).sum() > 0 and L26._ha_flip(c, dict(run=3), True).sum() > 0


def test_v26_weekly_matches_independent_resample_on_ms():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t)
    got = L26._weekly(c, dict(weeks=12), True)
    s = pd.Series(c.c.values, index=t); cnt = s.resample('W-SUN').count(); W = s.resample('W-SUN').last()[cnt == 42]
    ok = W[W > W.rolling(12).max().shift()]
    assert got.sum() > 5 and (got == np.isin(t.values, (ok.index + pd.Timedelta(days=1)).values)).all()


def test_v26_incomplete_previous_day_blocks_day_entries_on_ms():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t)
    full = L26._ha_flip(c, dict(run=3), True)
    i = int(np.flatnonzero(full)[2])
    drop = c.drop(index=i - 2).reset_index(drop=True)
    j = int(np.flatnonzero(pd.to_datetime(drop.t).values == t.values[i])[0])
    assert full[i] and not L26._ha_flip(drop, dict(run=3), True)[j]


def test_v26_stale_day_after_a_gap_does_not_fire_twice():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t)
    for fn, p in ((L26._ha_flip, dict(run=3)), (L26._weekly, dict(weeks=12))):
        full = fn(c, p, True)
        i = int(np.flatnonzero(full)[2])                           # entry bar = first bar after the event day
        drop = c.drop(index=i + 1).reset_index(drop=True)          # the entry day becomes incomplete
        nxt = t.values[i + 6]                                      # first bar of the following day
        j = int(np.flatnonzero(pd.to_datetime(drop.t).values == nxt)[0])
        assert not fn(drop, p, True)[j], fn.__name__


import longscout_v27 as L27
L27.register()


def _v27_coin(seed, dry=False):
    c = _ms(_mk(2600, '4h', seed))
    if dry:                                                    # days 17-19 of every 20-day block trade on 20% volume
        q = ((np.arange(len(c)) // 6) % 20) >= 17
        c = c.assign(v=np.where(q, c.v * 0.2, c.v))
    return c


@pytest.mark.parametrize('key', sorted(L27.CANDIDATES))
def test_v27_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _v27_coin(56, 'dryup' in key), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v27_helper_values_match_on_every_prefix():
    c = S.indicators(_v27_coin(56, True))
    fns = [(L27._staircase, dict(days=3), True), (L27._staircase, dict(days=3), False),
           (L27._close_loc, dict(days=10, hi=0.65), True), (L27._close_loc, dict(days=10, hi=0.65), False)]
    full = [f(c, p, u) for f, p, u in fns]
    dp = L27.CANDIDATES['l27_volume_dryup_near_high_4h']['params']; fd = L27._dryup(c, dp)
    assert all(x.sum() > 0 for x in full) and fd.sum() > 0
    for cut in range(700, 2600, 19):
        part = c.iloc[:cut].reset_index(drop=True)
        for (f, p, u), x in zip(fns, full):
            np.testing.assert_array_equal(f(part, p, u), x[:cut])
        np.testing.assert_array_equal(L27._dryup(part, dp), fd[:cut])


def test_v27_stale_day_after_a_gap_does_not_fire_twice():
    c = S.indicators(_v27_coin(56)); t = pd.to_datetime(c.t)
    full = L27._staircase(c, dict(days=3), True)
    i = int(np.flatnonzero(full)[3])
    drop = c.drop(index=i + 1).reset_index(drop=True)
    j = int(np.flatnonzero(pd.to_datetime(drop.t).values == t.values[i + 6])[0])
    assert not L27._staircase(drop, dict(days=3), True)[j]


import longscout_v28 as L28
L28.register()


@pytest.mark.parametrize('key', sorted(L28.CANDIDATES))
def test_v28_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _ms(_mk(2600, '4h', 56)), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v28_helper_values_match_on_every_prefix():
    c = S.indicators(_ms(_mk(2600, '4h', 56)))
    P = {k: v['params'] for k, v in L28.CANDIDATES.items()}
    fns = [lambda x, u=u: L28._band_walk(x, P['r28_band_walk_4h'], u) for u in (True, False)] + \
          [lambda x, u=u: L28._obv_break(x, P['r28_obv_confirmed_break_4h'], u) for u in (True, False)] + \
          [lambda x: L28._nr7(x, P['l28_nr7_breakout_4h'])[0], lambda x: L28._nr7(x, P['l28_nr7_breakout_4h'])[1].values]
    full = [f(c) for f in fns]
    assert all(np.nansum(np.asarray(x, float)) > 0 for x in full)
    for cut in range(700, 2600, 19):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


def test_v28_nr7_entry_only_on_the_day_after_the_nr7_day():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t)
    ok, _ = L28._nr7(c, dict(n=7))
    D = L27._days_v(c); rng = D.h - D.l
    nr = set((rng[(rng <= rng.rolling(7).min())].index + pd.Timedelta(days=1)).values)
    assert ok.sum() > 0 and all(v in nr for v in t.dt.normalize().values[ok])


import longscout_v29 as L29
L29.register()


@pytest.mark.parametrize('key', sorted(L29.CANDIDATES))
def test_v29_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    a = {'l29_weekly_rsi_cross_4h': 52, 's29_ema20_reclaim_fail_4h': 51}.get(key, 56)
    coin, btc = _ms(_mk(2600, '4h', a)), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v29_helper_values_match_on_every_prefix():
    c = S.indicators(_ms(_mk(2600, '4h', 52)))
    P = {k: v['params'] for k, v in L29.CANDIDATES.items()}
    fns = [lambda x, u=u: L29._squeeze(x, P['r29_daily_squeeze_release_4h'], u) for u in (True, False)] + \
          [lambda x: L29._weekly_rsi(x, P['l29_weekly_rsi_cross_4h']), lambda x: L29._reclaim_fail(x, P['s29_ema20_reclaim_fail_4h'])]
    full = [f(c) for f in fns]
    assert all(x.sum() > 0 for x in full)
    for cut in range(700, 2600, 19):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


def test_v29_weekly_rsi_matches_independent_resample():
    c = S.indicators(_ms(_mk(2600, '4h', 52))); t = pd.to_datetime(c.t)
    got = L29._weekly_rsi(c, dict(n=14, lvl=50.0))
    s = pd.Series(c.c.values, index=t); cnt = s.resample('W-SUN').count(); W = s.resample('W-SUN').last()[cnt == 42]
    r = S.rsi(W, 14); ok = W[(r > 50) & (r.shift() <= 50)]
    assert got.sum() > 0 and (got == np.isin(t.values, (ok.index + pd.Timedelta(days=1)).values)).all()


def test_v29_weekly_entry_not_repeated_after_a_broken_week():
    c = S.indicators(_ms(_mk(2600, '4h', 52))); t = pd.to_datetime(c.t)
    p = dict(n=14, lvl=50.0)
    full = L29._weekly_rsi(c, p)
    i = int(np.flatnonzero(full)[0])                             # Monday 00:00 entry
    drop = c.drop(index=[i + 1 + 6 * k for k in range(7)]).reset_index(drop=True)   # every day of that week incomplete
    nxt = t.values[i + 42]                                       # the following Monday 00:00
    j = int(np.flatnonzero(pd.to_datetime(drop.t).values == nxt)[0])
    assert not L29._weekly_rsi(drop, p)[j]


import longscout_v30 as L30
L30.register()


def _v30_coin(seed, crash=False):
    c = _mk(2600, '4h', seed)
    if crash:                                                  # every 25th day block: 3 days down ~6%/day, last on 3x volume
        day = np.arange(len(c)) // 6; ph = day % 25
        f = np.ones(len(c))
        for i in np.flatnonzero((ph >= 20) & (ph <= 22) & (np.arange(len(c)) % 6 == 5)): f[i:] *= 0.94
        for i in np.flatnonzero((ph < 20) & (np.arange(len(c)) % 6 == 5)): f[i:] *= 1.012       # drift up between crashes
        c = c.assign(**{k: c[k] * f for k in ('o', 'h', 'l', 'c')}, v=np.where(ph == 22, c.v * 3, c.v))
    return _ms(c)


@pytest.mark.parametrize('key', sorted(L30.CANDIDATES))
def test_v30_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _v30_coin(56, 'capitulation' in key), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v30_helper_values_match_on_every_prefix():
    c = S.indicators(_v30_coin(56, True))
    P = {k: v['params'] for k, v in L30.CANDIDATES.items()}
    fns = [lambda x: L30._monthly(x, P['l30_monthly_close_breakout_4h']),
           lambda x: L30._capitulation(x, P['l30_capitulation_dip_4h'])] + \
          [lambda x, u=u: L30._macd_flip(x, P['r30_daily_macd_flip_4h'], u) for u in (True, False)]
    full = [f(c) for f in fns]
    assert all(x.sum() > 0 for x in full)
    for cut in range(700, 2600, 19):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


def test_v30_monthly_matches_independent_resample():
    c = S.indicators(_v30_coin(56)); t = pd.to_datetime(c.t)
    got = L30._monthly(c, dict(months=6))
    s = pd.Series(c.c.values, index=t); cnt = s.resample('MS').count()
    M = s.resample('MS').last()[cnt.values == cnt.index.days_in_month * 6]
    ok = M[M > M.rolling(6).max().shift()]
    exp = np.isin(t.values, (ok.index + pd.offsets.MonthBegin(1)).values)
    assert got.sum() > 0 and (got == exp).all()


def test_v30_monthly_entry_not_repeated_after_a_broken_day():
    c = S.indicators(_v30_coin(56)); t = pd.to_datetime(c.t)
    full = L30._monthly(c, dict(months=6))
    i = int(np.flatnonzero(full)[0])
    mon = t.dt.to_period('M').values == t.dt.to_period('M').values[i]
    nxt = int(np.flatnonzero(~mon & (np.arange(len(t)) > i))[0])          # first bar of the following month
    hit = [k for k in np.flatnonzero(mon & (t.dt.hour.values == 4)) if k >= i]
    drop = c.drop(index=hit).reset_index(drop=True)            # every day of the entry month incomplete
    j = int(np.flatnonzero(pd.to_datetime(drop.t).values == t.values[nxt])[0])
    assert not L30._monthly(drop, dict(months=6))[j]


import longscout_v31 as L31
L31.register()


@pytest.mark.parametrize('key', sorted(L31.CANDIDATES))
def test_v31_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    n, a = (4400, 52) if 'year_high' in key else (2600, 55 if 'skew' in key else 56)
    coin, btc = _ms(_mk(n, '4h', a)), _ms(_mk(n, '4h', 55 if a != 55 else 56))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, n - 37, n - 1} | set(range(n - 100, n, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v31_helper_values_match_on_every_prefix():
    P = {k: v['params'] for k, v in L31.CANDIDATES.items()}
    c = S.indicators(_ms(_mk(4400, '4h', 55)))
    fns = [lambda x, u=u: L31._skew(x, P['r31_return_skew_regime_4h'], u) for u in (True, False)] + \
          [lambda x: L31._year_high(x, P['l31_year_high_proximity_4h'])]
    full = [f(c) for f in fns]
    assert all(x.sum() > 0 for x in full)
    for cut in range(700, 4400, 29):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


def test_v31_weekend_entries_are_friday_20utc_only():
    coin, btc = _ms(_mk(2600, '4h', 56)), _ms(_mk(2600, '4h', 55))
    le = np.asarray(_sig('l31_weekend_hold_4h', coin, btc)['le'])
    t = pd.to_datetime(coin.t)[le]
    assert le.sum() > 5 and (t.dt.weekday == 4).all() and (t.dt.hour == 20).all()


import longscout_v32 as L32
L32.register()


@pytest.mark.parametrize('key', sorted(L32.CANDIDATES))
def test_v32_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    a = 52 if 'efficiency' in key else 56
    coin, btc = _ms(_mk(2600, '4h', a)), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    ex = np.flatnonzero(np.diff(np.asarray(full['lx']).astype(int)) | np.diff(np.asarray(full['sx']).astype(int))) + 1
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | set(ex[ex >= 300][:20]) | {1300, 2563, 2599}
                  | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v32_helper_values_match_on_every_prefix():
    P = {k: v['params'] for k, v in L32.CANDIDATES.items()}
    c = S.indicators(_ms(_mk(2600, '4h', 52)))
    fns = [lambda x, u=u: L32._er(x, P['r32_efficiency_ratio_4h'], u) for u in (True, False)] + \
          [lambda x: L32._updown(x, P['l32_updown_volume_4h']), lambda x: L32._vwap(x, P['r32_vwap30_cross_4h']).values]
    full = [f(c) for f in fns]
    assert all(np.nansum(np.asarray(x, float)) > 0 for x in full)
    for cut in range(700, 2600, 19):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


import longscout_v33 as L33
L33.register()


def _v33_pair(seed, n=2600, churn=False):
    btc = _mk(n, '4h', seed + 100)
    rng = np.random.default_rng(seed)
    lb = np.diff(np.log(btc.c.values), prepend=np.log(btc.c.values[0]))
    w = ((np.arange(n) // 360) % 2 == 0).astype(float)            # 60-day coupled / decoupled blocks
    lr = w * 1.5 * lb + rng.normal(0.0004, 0.015, n)
    c = 100 * np.exp(np.cumsum(lr))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, .01, n))); l = np.minimum(o, c) * (1 - abs(rng.normal(0, .01, n)))
    v = rng.lognormal(10, 0.3, n)
    if churn:                                                    # every 40th day: big spike high, close near the low, 6x volume
        day = np.arange(n) // 6; ev = (day % 40 == 39)
        h = np.where(ev & (np.arange(n) % 6 == 0), h * 1.25, h); v = np.where(ev, v * 6, v)
    coin = pd.DataFrame(dict(t=btc.t, o=o, h=h, l=l, c=c, v=v))
    return _ms(coin), _ms(btc)


@pytest.mark.parametrize('key', sorted(L33.CANDIDATES))
def test_v33_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _v33_pair(1 if 'beta' in key else 3, churn='churn' in key)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v33_btc_future_rows_do_not_change_coin_signals():
    coin, btc = _v33_pair(3, churn=True)
    for key in sorted(L33.CANDIDATES):
        a = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc.iloc[:2000].reset_index(drop=True))
        b = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc)        # BTC frame runs 600 bars past the coin
        for k in ('le', 'se', 'lx', 'sx'):
            assert (a[k] == b[k]).all(), (key, k)


def test_v33_helper_values_match_on_every_prefix():
    P = {k: v['params'] for k, v in L33.CANDIDATES.items()}
    out = []
    for seed, fn, key in ((3, L33._churn, 's33_churn_exhaustion_4h'), (3, L33._decouple, 'l33_btc_decoupling_4h'),
                          (1, L33._beta_turn, 'l33_high_beta_btc_up_4h')):
        coin, btc = _v33_pair(seed, churn=True)
        L.set_btc(btc); c = S.indicators(coin)
        full = fn(c, P[key]); assert full.sum() > 0, key
        for cut in range(700, 2600, 23):
            L.set_btc(btc.iloc[:cut].reset_index(drop=True))
            np.testing.assert_array_equal(fn(c.iloc[:cut].reset_index(drop=True), P[key]), full[:cut])


import longscout_v34 as L34
L34.register()


@pytest.mark.parametrize('key', sorted(L34.CANDIDATES))
def test_v34_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    a = 52 if key.startswith('s34') else 56
    coin, btc = _ms(_mk(2600, '4h', a)), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v34_band_edges_and_helper_prefixes():
    np.testing.assert_array_equal(L34._band([0.9, 1, 1.99, 2, 4.99, 5, 9.99, 10, 0.05]), [-1, 0, 0, 1, 1, 2, 2, 3, -4])
    for seed, up in ((56, True), (52, False)):
        c = S.indicators(_ms(_mk(2600, '4h', seed)))
        full = L34._half_retrace(c, L34.HR, up); rc = L34._round_cross(c, {})
        assert full.sum() > 0 and rc.sum() > 0
        for cut in range(700, 2600, 23):
            part = c.iloc[:cut].reset_index(drop=True)
            np.testing.assert_array_equal(L34._half_retrace(part, L34.HR, up), full[:cut])
            np.testing.assert_array_equal(L34._round_cross(part, {}), rc[:cut])


def test_v34_swing_mid_needs_the_extreme_order():
    c = pd.Series([10, 8, 6, 7, 8.5], dtype=float)                 # high 10 then low 6: decline swing, mid 8
    mid, size = L34._swing_mid(c, 5, True)
    assert mid.iloc[-1] == 8 and abs(size.iloc[-1] - 0.4) < 1e-12
    mid2, _ = L34._swing_mid(pd.Series([6, 7, 8, 9, 10], dtype=float), 5, True)   # no decline: high is last
    assert np.isnan(mid2.iloc[-1])


import longscout_v35 as L35
L35.register()


@pytest.mark.parametrize('key', sorted(L35.CANDIDATES))
def test_v35_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    a = 55 if key.startswith('s35') else 56
    coin, btc = _ms(_mk(2600, '4h', a)), _ms(_mk(2600, '4h', 55 if a != 55 else 56))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v35_week_high_matches_independent_resample():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t)
    H, _ = L35._week_high(c)
    s = pd.Series(c.h.values, index=t); cnt = s.resample('W-SUN').count(); W = s.resample('W-SUN').max()[cnt == 42]
    prev_sun = (t - pd.to_timedelta(t.dt.weekday, unit='D')).dt.normalize() - pd.Timedelta(days=1)
    exp = prev_sun.map(lambda x: W.get(x, np.nan)).values
    sun20 = ((t.dt.weekday == 6) & (t.dt.hour == 20)).values      # by design: NaN on the last bar of a week
    assert np.isnan(H.values[sun20]).all()
    np.testing.assert_allclose(H.values[~sun20], exp[~sun20])


def test_v35_orb_matches_independent_loop():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t)
    got = L35._orb(c, True)
    exp = np.zeros(len(c), bool); day = t.dt.normalize().values; h, cl = c.h.values, c.c.values
    for i in range(1, len(c)):
        j = np.flatnonzero((day == day[i]) & (t.dt.hour.values == 0))
        if not len(j) or j[0] >= i or (t.dt.hour.values[i] == 0): continue
        lvl = h[j[0]]
        if cl[i] > lvl and cl[i - 1] <= lvl and not exp[j[0]:i].any() and day[i - 1] == day[i]:
            exp[i] = True
    assert got.sum() > 20 and (got == exp).all()


def test_v35_week_break_entries_match_independent_loop():
    coin, btc = _ms(_mk(2600, '4h', 56)), _ms(_mk(2600, '4h', 55))
    c = S.indicators(coin); t = pd.to_datetime(c.t)
    H, mon = L35._week_high(c)
    exp = np.zeros(len(c), bool); cl = c.c.values; hv = H.values; mv = mon.values
    for i in range(1, len(c)):
        if np.isnan(hv[i]) or not (cl[i] > hv[i] and cl[i - 1] <= hv[i]): continue
        if not exp[(mv == mv[i]) & (np.arange(len(c)) < i)].any(): exp[i] = True
    cr = L.coin_regime(c)
    le = np.asarray(_sig('l35_prior_week_high_break_4h', coin, btc)['le'])
    assert le.sum() > 5 and (le == (exp & (cr == 1))).all()


import longscout_v36 as L36
L36.register()


def _v36_coin(seed, n=2600, drift=0.0006):
    rng = np.random.default_rng(seed)
    t = pd.date_range('2021-01-01', periods=n, freq='4h')
    day = np.arange(n) // 6
    sd = np.where((day // 30) % 2 == 0, 0.02, 0.004)               # alternating loud / quiet 30-day blocks
    lr = rng.normal(drift, sd)
    lr = np.where((day % 45 == 44) & (np.arange(n) % 6 == 5), np.log(1.3), lr)   # a +30% close every 45th day
    c = 100 * np.exp(np.cumsum(lr)); o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, .005, n))); l = np.minimum(o, c) * (1 - abs(rng.normal(0, .005, n)))
    return _ms(pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=rng.lognormal(10, 1, n))))


@pytest.mark.parametrize('key', sorted(L36.CANDIDATES))
def test_v36_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin = _v36_coin(3, drift=-0.001 if 'max' in key else 0.0006) if 'trend' not in key else _ms(_mk(2600, '4h', 55))
    btc = _ms(_mk(2600, '4h', 56))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v36_helper_values_and_tstat_reference():
    y = pd.Series(np.log(np.r_[1.0, 1.1, 1.05, 1.2, 1.3, 1.25, 1.4])); x = np.arange(7.0)
    b, a = np.polyfit(x, y.values, 1)
    res = y.values - (a + b * x); se = np.sqrt((res ** 2).sum() / 5 / ((x - x.mean()) ** 2).sum())
    assert abs(L36._tstat(y, 7).iloc[-1] - b / se) < 1e-9
    P = {k: v['params'] for k, v in L36.CANDIDATES.items()}
    c = S.indicators(_v36_coin(3))
    fns = [lambda x, u=u: L36._trend_t(x, P['r36_trend_tstat_4h'], u) for u in (True, False)] + \
          [lambda x: L36._max_effect(x, P['s36_max_effect_4h']), lambda x: L36._vol_collapse(x, P['l36_vol_collapse_4h'])]
    full = [f(c) for f in fns]
    assert all(x.sum() > 0 for x in full)
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


import longscout_v37 as L37
L37.register()


def _v37_hammer_coin(seed=50):
    c = _mk(2600, '4h', seed)
    day = np.arange(len(c)) // 6; ph = np.arange(len(c)) % 6
    ev = day % 15 == 14                                            # every 15th day: flat body, deep lower wick
    first_o = c.o.values[np.flatnonzero(ph == 0)][day]
    cl = np.where(ev & (ph == 5), first_o * 1.002, c.c.values)
    lo = np.where(ev & (ph == 2), np.minimum(c.l.values, first_o * 0.80), c.l.values)
    hi = np.where(ev, np.minimum(c.h.values, first_o * 1.003), c.h.values)
    hi = np.maximum(hi, np.maximum(c.o.values, cl)); lo = np.minimum(lo, np.minimum(c.o.values, cl))
    return _ms(c.assign(c=cl, l=lo, h=hi))


@pytest.mark.parametrize('key', sorted(L37.CANDIDATES))
def test_v37_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin = _v37_hammer_coin() if 'hammer' in key else _ms(_mk(2600, '4h', 56 if key.startswith('l37') else 55))
    btc = _ms(_mk(2600, '4h', 55 if key.startswith('l37') else 56))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v37_pivot_level_and_helper_prefixes():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t)
    got = L37._pivot(c, True)
    i = int(np.flatnonzero(got)[5]); day = t.dt.normalize().values
    prev = c[day == day[i] - np.timedelta64(1, 'D')]
    P = (prev.h.max() + prev.l.min() + prev.c.iloc[-1]) / 3; R1 = 2 * P - prev.l.min()
    assert c.c.values[i] > R1 >= c.c.values[i - 1] and day[i - 1] == day[i]
    hc = S.indicators(_v37_hammer_coin())
    for fn, x in ((lambda z: L37._pivot(z, True), c), (lambda z: L37._pivot(z, False), c),
                  (lambda z: L37._hammer(z, dict(wick=2.0, n=20)), hc)):
        full = fn(x); assert full.sum() > 0
        for cut in range(700, 2600, 23):
            np.testing.assert_array_equal(fn(x.iloc[:cut].reset_index(drop=True)), full[:cut])


def test_v37_pivot_needs_yesterday_complete():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t); day = t.dt.normalize().values
    days = np.unique(day)[5:-1]
    js = [int(np.flatnonzero(day == x)[2]) for x in days[::2]]           # every other day loses a bar
    drop = c.drop(index=js).reset_index(drop=True)
    dd = pd.to_datetime(drop.t).dt.normalize().values
    after_broken = np.isin(dd, days[::2] + np.timedelta64(1, 'D'))
    assert not L37._pivot(drop, True)[after_broken].any() and not L37._pivot(drop, False)[after_broken].any()


import longscout_v38 as L38
L38.register()


@pytest.mark.parametrize('key', sorted(L38.CANDIDATES))
def test_v38_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    a = 56 if key.startswith('l38') else 52
    coin, btc = _ms(_mk(2600, '4h', a)), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v38_psar_reference_and_helper_prefixes():
    h = np.array([10, 11, 12, 13, 12.5, 11, 10, 9.5, 10.5, 12, 13], float); l = h - 1
    tr = L38._psar(h, l, 0.02, 0.2)
    assert tr[1] == 1 and tr[-1] == 1 and (tr == -1).any()        # up, flips down in the fall, back up at the end
    P = {k: v['params'] for k, v in L38.CANDIDATES.items()}
    c = S.indicators(_ms(_mk(2600, '4h', 56)))
    fns = [lambda x, u=u: L38._psar_flip(x, P['r38_daily_psar_flip_4h'], u) for u in (True, False)] + \
          [lambda x, u=u: L38._aroon(x, P['r38_daily_aroon_4h'], u) for u in (True, False)] + \
          [lambda x: L38._cmf(x, P['l38_daily_cmf_4h'])]
    full = [f(c) for f in fns]
    assert all(x.sum() > 0 for x in full)
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


import longscout_v39 as L39
L39.register()


@pytest.mark.parametrize('key', sorted(L39.CANDIDATES))
def test_v39_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin = _v36_coin(1) if 'mass' in key else _ms(_mk(2600, '4h', 52))
    btc = _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v39_helper_values_match_on_every_prefix():
    P = {k: v['params'] for k, v in L39.CANDIDATES.items()}
    c = S.indicators(_v36_coin(1))
    fns = [lambda x, u=u: L39._vortex(x, P['r39_daily_vortex_4h'], u) for u in (True, False)] + \
          [lambda x, u=u: L39._force(x, P['r39_daily_force_index_4h'], u) for u in (True, False)] + \
          [lambda x: L39._mass(x, P['l39_mass_index_bulge_4h'])]
    full = [f(c) for f in fns]
    assert all(x.sum() > 0 for x in full)
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(part), x[:cut])


import longscout_v40 as L40
L40.register()

_V40_SEED = {'r40_trend_consensus_4h': 56, 'l40_dual_momentum_4h': 52, 's40_relative_weakness_4h': 55}


@pytest.mark.parametrize('key', sorted(L40.CANDIDATES))
def test_v40_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    a = _V40_SEED[key]
    coin, btc = _ms(_mk(2600, '4h', a)), _ms(_mk(2600, '4h', 55 if a != 55 else 56))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)
    a2 = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc.iloc[:2000].reset_index(drop=True))
    b2 = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc)                  # BTC runs 600 bars past the coin
    for k in ('le', 'se', 'lx', 'sx'):
        assert (a2[k] == b2[k]).all(), (key, k)


def test_v40_helper_values_match_on_every_prefix():
    P = {k: v['params'] for k, v in L40.CANDIDATES.items()}
    coin, btc = _ms(_mk(2600, '4h', 52)), _ms(_mk(2600, '4h', 55))
    L.set_btc(btc); c = S.indicators(coin)
    fns = [lambda x, u=u: L40._consensus(x, P['r40_trend_consensus_4h'], u) for u in (True, False)] + \
          [lambda x: L40._dual(x, P['l40_dual_momentum_4h']), lambda x: L40._weak(x, P['s40_relative_weakness_4h'])]
    full = [f(c) for f in fns]
    assert all(x.sum() > 0 for x in full)
    for cut in range(700, 2600, 23):
        L.set_btc(btc.iloc[:cut].reset_index(drop=True))
        for f, x in zip(fns, full):
            np.testing.assert_array_equal(f(c.iloc[:cut].reset_index(drop=True)), x[:cut])


import longscout_v41 as L41
L41.register()


@pytest.mark.parametrize('key', sorted(L41.CANDIDATES))
def test_v41_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _ms(_mk(2600, '4h', 56)), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v41_setup9_reference_and_helper_prefixes():
    c = S.indicators(_ms(_mk(2600, '4h', 56)))
    D = L27._days_v(c); dc = D.c.values
    got = L41._setup9(c, dict(lag=4, count=9), True)
    t = pd.to_datetime(c.t).dt.normalize().values
    for i in np.flatnonzero(got):
        k = int(np.flatnonzero(D.index.values == t[i] - np.timedelta64(1, 'D'))[0])
        assert all(dc[k - j] < dc[k - j - 4] for j in range(9)) and not all(dc[k - 1 - j] < dc[k - 5 - j] for j in range(9))
    for fn in (lambda x: L41._setup9(x, dict(lag=4, count=9), True), lambda x: L41._setup9(x, dict(lag=4, count=9), False),
               lambda x: L41._shock(x, L41.SH)):
        full = fn(c); assert full.sum() > 0
        for cut in range(700, 2600, 23):
            np.testing.assert_array_equal(fn(c.iloc[:cut].reset_index(drop=True)), full[:cut])


import longscout_v42 as L42
L42.register()


@pytest.mark.parametrize('key', sorted(L42.CANDIDATES))
def test_v42_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    if 'mfi' in key:
        coin, btc = _ms(_mk(2600, '4h', 52)), _ms(_mk(2600, '4h', 55))
    else:
        coin, btc = _v33_pair(5 if key.startswith('l42') else 1)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)
    a2 = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc.iloc[:2000].reset_index(drop=True))
    b2 = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc)
    for k in ('le', 'se', 'lx', 'sx'):
        assert (a2[k] == b2[k]).all(), (key, k)


def test_v42_helper_values_match_on_every_prefix():
    P = L42.CANDIDATES['r42_daily_mfi_extreme_4h']['params']
    coin, btc = _v33_pair(5)
    L.set_btc(btc); c = S.indicators(coin); m = S.indicators(_ms(_mk(2600, '4h', 52)))
    fns = [(lambda x, u=u: L42._diverge(x, L42.DV, u), c) for u in (True, False)] + \
          [(lambda x, u=u: L42._mfi(x, P, u), m) for u in (True, False)]
    full = [f(x) for f, x in fns]
    assert all(v.sum() > 0 for v in full)
    for cut in range(700, 2600, 23):
        L.set_btc(btc.iloc[:cut].reset_index(drop=True))
        for (f, x), v in zip(fns, full):
            np.testing.assert_array_equal(f(x.iloc[:cut].reset_index(drop=True)), v[:cut])


import longscout_v43 as L43
L43.register()

_V43_COIN = {'l43_big_up_week_4h': (1, 0.0006), 's43_big_down_week_4h': (2, -0.001), 'l43_down_week_in_uptrend_4h': (3, 0.0006)}


@pytest.mark.parametrize('key', sorted(L43.CANDIDATES))
def test_v43_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    s, dr = _V43_COIN[key]
    coin, btc = _v36_coin(s, drift=dr), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v43_week_event_matches_independent_resample_and_prefixes():
    for up, mv, seed in ((True, 0.2, 1), (False, 0.15, 3)):
        c = S.indicators(_v36_coin(seed)); t = pd.to_datetime(c.t)
        got = L43._week_event(c, dict(move=mv), up)
        s = pd.Series(c.c.values, index=t); cnt = s.resample('W-SUN').count(); W = s.resample('W-SUN').last()[cnt == 42]
        r = W / W.shift() - 1; ok = W[(r >= mv) if up else (r <= -mv)]
        ok = ok[ok.index.isin(W.index[1:][np.diff(W.index.values) == np.timedelta64(7, 'D')])]
        assert got.sum() > 0 and (got == np.isin(t.values, (ok.index + pd.Timedelta(days=1)).values)).all()
        for cut in range(700, 2600, 23):
            np.testing.assert_array_equal(L43._week_event(c.iloc[:cut].reset_index(drop=True), dict(move=mv), up), got[:cut])


def test_v43_week_after_a_broken_week_does_not_fire():
    c = S.indicators(_v36_coin(1)); t = pd.to_datetime(c.t)
    s = pd.Series(c.c.values, index=t); W = s.resample('W-SUN').last()
    got = L43._week_event(c, dict(move=0.2), True)
    done = 0
    for i in np.flatnonzero(got):
        sun = t.values[i] - np.timedelta64(1, 'D') - np.timedelta64(0, 'h')
        sun = pd.Timestamp(sun).normalize()
        prev, prev2 = sun - pd.Timedelta(days=7), sun - pd.Timedelta(days=14)
        if prev2 not in W.index or W[sun] / W[prev2] - 1 < 0.2: continue        # want a case the 2-week move also clears
        j = int(np.flatnonzero((t >= prev - pd.Timedelta(days=2)) & (t < prev))[0])   # a bar inside week prev
        drop = c.drop(index=j).reset_index(drop=True)
        k = int(np.flatnonzero(pd.to_datetime(drop.t).values == t.values[i])[0])
        assert not L43._week_event(drop, dict(move=0.2), True)[k]
        done += 1
    assert done > 0


import longscout_v44 as L44
L44.register()

_V44_COIN = {'l44_thrust_bar_4h': (1, 0.0006), 's44_thrust_bar_4h': (2, -0.001), 'l44_two_day_reversal_4h': (1, 0.0006)}


@pytest.mark.parametrize('key', sorted(L44.CANDIDATES))
def test_v44_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    s, dr = _V44_COIN[key]
    coin, btc = _v36_coin(s, drift=dr), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v44_helper_reference_and_prefixes():
    c = S.indicators(_v36_coin(1)); t = pd.to_datetime(c.t)
    th = np.asarray(L44._thrust(c, L44.TB, True)); r = (c.h - c.l).values
    for i in np.flatnonzero(th):
        assert r[i] > r[i - 180:i].max() and (c.c.values[i] - c.l.values[i]) >= 0.8 * r[i]
    two = L44._two_day(c, dict(fall=0.06)); assert two.sum() > 0
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        np.testing.assert_array_equal(np.asarray(L44._thrust(part, L44.TB, True)), th[:cut])
        np.testing.assert_array_equal(L44._two_day(part, dict(fall=0.06)), two[:cut])


import longscout_v45 as L45
L45.register()


@pytest.mark.parametrize('key', sorted(L45.CANDIDATES))
def test_v45_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _ms(_mk(2600, '4h', 55)), _ms(_mk(2600, '4h', 53))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert le.sum() and se.sum(), key
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le | se] == 0).all()                              # trades only in the sideways regime
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v45_helper_values_match_on_every_prefix():
    c = S.indicators(_ms(_mk(2600, '4h', 55)))
    P = {k: v['params'] for k, v in L45.CANDIDATES.items()}
    fns = [(f, P[k]) for f, k in ((L45._bb, 'r45_sideways_bollinger_4h'), (L45._don, 'r45_sideways_donchian_fade_4h'),
                                  (L45._rsi, 'r45_sideways_rsi_4h'))]
    full = {(i, lo): L45._side(c, p, f, lo) for i, (f, p) in enumerate(fns) for lo in (True, False)}
    assert all(v.sum() > 0 for v in full.values())
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        for (i, lo), v in full.items():
            f, p = fns[i]
            np.testing.assert_array_equal(L45._side(part, p, f, lo), v[:cut])


def test_v45_long_side_follows_the_low_extreme():
    coin, btc = _ms(_mk(2600, '4h', 55)), _ms(_mk(2600, '4h', 53))
    c = S.indicators(coin); D = L27._days_v(c); t = pd.to_datetime(c.t).dt.normalize().values
    ma = D.c.rolling(20).mean(); sd = D.c.rolling(20).std()
    for side, below in (('le', True), ('se', False)):
        sig = np.asarray(_sig('r45_sideways_bollinger_4h', coin, btc)[side])
        for i in np.flatnonzero(sig):
            k = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
            assert (D.c.iloc[k] < ma.iloc[k] - 2 * sd.iloc[k]) if below else (D.c.iloc[k] > ma.iloc[k] + 2 * sd.iloc[k])


import longscout_v46 as L46
L46.register()


def _v46_crash_coin(seed=4):
    c = _mk(2600, '4h', seed)
    day = np.arange(len(c)) // 6; ph = day % 30; f = np.ones(len(c))
    for i in np.flatnonzero((ph >= 25) & (ph <= 27) & (np.arange(len(c)) % 6 == 5)): f[i:] *= 0.90   # three -10% days
    for i in np.flatnonzero((ph < 25) & (np.arange(len(c)) % 6 == 5)): f[i:] *= 1.004
    return _ms(c.assign(**{k: c[k] * f for k in ('o', 'h', 'l', 'c')}))


_V46_COIN = {'l46_falling_oversold_bounce_4h': lambda: _v36_coin(2, drift=-0.001),
             's46_rising_overbought_fade_4h': lambda: _v36_coin(1),
             'l46_falling_crash_bounce_4h': _v46_crash_coin}


@pytest.mark.parametrize('key', sorted(L46.CANDIDATES))
def test_v46_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V46_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[ent] == (1 if key.startswith('s46') else -1)).all()          # counter-regime by construction
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v46_helper_values_match_on_every_prefix():
    for c, fn in ((S.indicators(_v36_coin(2, drift=-0.001)), lambda x: L46._rsi_x(x, dict(lvl=20.0), True)),
                  (S.indicators(_v36_coin(1)), lambda x: L46._rsi_x(x, dict(lvl=85.0), False)),
                  (S.indicators(_v46_crash_coin()), lambda x: L46._crash(x, dict(days=3, fall=0.25)))):
        full = fn(c); assert full.sum() > 0
        for cut in range(700, 2600, 23):
            np.testing.assert_array_equal(fn(c.iloc[:cut].reset_index(drop=True)), full[:cut])


import longscout_v47 as L47
L47.register()


@pytest.mark.parametrize('key', sorted(L47.CANDIDATES))
def test_v47_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _ms(_mk(2600, '4h', 56)), _ms(_mk(2600, '4h', 55))
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    hours = set(pd.to_datetime(coin.t).dt.hour.values[ent])
    assert hours == ({8} if 'us_' in key else {20})
    bars = sorted(set(ent[ent >= 300][:60]) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v47_strong_close_reference():
    c = S.indicators(_ms(_mk(2600, '4h', 56))); t = pd.to_datetime(c.t); day = t.dt.normalize().values
    got = L47._strong_close(c, dict(edge=0.1)); assert got.sum() > 10
    for i in range(len(c)):
        m = day == day[i]
        if t.dt.hour.values[i] != 20 or m.sum() != 6:
            assert not got[i]; continue
        hi, lo = c.h.values[m].max(), c.l.values[m].min()
        assert got[i] == ((c.c.values[i] - lo) / (hi - lo) >= 0.9)


import longscout_v48 as L48
L48.register()


@pytest.mark.parametrize('key', sorted(L48.CANDIDATES))
def test_v48_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _v33_pair(3)
    full = _sig(key, coin, btc)
    ent = np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))
    assert len(ent), key
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)
    a2 = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc.iloc[:2000].reset_index(drop=True))
    b2 = _sig(key, coin.iloc[:2000].reset_index(drop=True), btc)
    for k in ('le', 'se', 'lx', 'sx'):
        assert (a2[k] == b2[k]).all(), (key, k)


def test_v48_btc_day_reference_and_prefixes():
    coin, btc = _v33_pair(3)
    L.set_btc(btc); c = S.indicators(coin); t = pd.to_datetime(c.t).dt.normalize().values
    got = L48._btc_day(c, dict(move=0.05), True); assert got.sum() > 0
    bd = pd.Series(btc.c.values, index=pd.to_datetime(btc.t)).resample('1D').last()
    for i in np.flatnonzero(got):
        y = pd.Timestamp(t[i]) - pd.Timedelta(days=1)
        assert bd[y] / bd[y - pd.Timedelta(days=1)] - 1 <= -0.05
    for fn in (lambda x: L48._btc_day(x, dict(move=0.05), True), lambda x: L48._btc_day(x, dict(move=0.05), False),
               lambda x: L48._lag(x, dict(n=20))):
        full = fn(c); assert full.sum() > 0
        for cut in range(700, 2600, 23):
            L.set_btc(btc.iloc[:cut].reset_index(drop=True))
            np.testing.assert_array_equal(fn(c.iloc[:cut].reset_index(drop=True)), full[:cut])
        L.set_btc(btc)


import longscout_v49 as L49
L49.register()

_V49_COIN = {'l49_daily_stoch_up_4h': lambda: _ms(_mk(2600, '4h', 56)),
             's49_daily_stoch_down_4h': lambda: _v36_coin(2, drift=-0.001),
             'r49_daily_trix_4h': lambda: _ms(_mk(2600, '4h', 55))}


@pytest.mark.parametrize('key', sorted(L49.CANDIDATES))
def test_v49_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V49_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert le.sum() or se.sum(), key
    if key.startswith('r49'):
        assert le.sum() and se.sum()
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v49_helper_values_match_on_every_prefix():
    c = S.indicators(_ms(_mk(2600, '4h', 55)))
    D = L27._days_v(c)
    k, x = L49._stoch_k(D, L49.ST), L49._trix_v(D, dict(n=15))
    full = {(f, up): f(c, p, up) for f, p in ((L49._stoch, L49.ST), (L49._trix, dict(n=15))) for up in (True, False)}
    assert all(v.sum() > 0 for v in full.values())
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        Dp = L27._days_v(part)
        np.testing.assert_allclose(L49._stoch_k(Dp, L49.ST).values, k.values[:len(Dp)], equal_nan=True)
        np.testing.assert_allclose(L49._trix_v(Dp, dict(n=15)).values, x.values[:len(Dp)], equal_nan=True)
        for (f, up), v in full.items():
            np.testing.assert_array_equal(f(part, L49.ST if f is L49._stoch else dict(n=15), up), v[:cut])


def test_v49_indicators_match_an_independent_loop():
    D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', 55))))
    h, l, c = D.h.values, D.l.values, D.c.values
    fast = np.full(len(c), np.nan)
    for i in range(13, len(c)):
        lo, hi = l[i - 13:i + 1].min(), h[i - 13:i + 1].max()
        fast[i] = 100 * (c[i] - lo) / (hi - lo)
    slow = np.full(len(c), np.nan)
    for i in range(15, len(c)):
        slow[i] = fast[i - 2:i + 1].mean()
    np.testing.assert_allclose(L49._stoch_k(D, L49.ST).values, slow, equal_nan=True, rtol=1e-9)
    e = c.astype(float)
    for _ in range(3):
        out = np.empty_like(e); out[0] = e[0]; a = 2 / 16
        for i in range(1, len(e)):
            out[i] = a * e[i] + (1 - a) * out[i - 1]
        e = out
    ref = np.r_[np.nan, 100 * (e[1:] / e[:-1] - 1)]
    np.testing.assert_allclose(L49._trix_v(D, dict(n=15)).values, ref, equal_nan=True, rtol=1e-9)


def test_v49_entries_follow_the_cross_of_the_day_before():
    coin, btc = _V49_COIN['s49_daily_stoch_down_4h'](), _ms(_mk(2600, '4h', 52))
    c = S.indicators(coin); D = L27._days_v(c); k = L49._stoch_k(D, L49.ST)
    t = pd.to_datetime(c.t).dt.normalize().values
    sig = np.asarray(_sig('s49_daily_stoch_down_4h', coin, btc)['se'])
    assert sig.sum()
    for i in np.flatnonzero(sig):
        j = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
        assert k.iloc[j] < 80 <= k.iloc[j - 1]
        assert pd.Timestamp(c.t.iloc[i]).hour == 0


def test_v49_a_cross_across_a_missing_day_does_not_fire():
    coin, btc = _V49_COIN['s49_daily_stoch_down_4h'](), _ms(_mk(2600, '4h', 52))
    t = pd.to_datetime(coin.t).dt.normalize()
    fired = 0
    for i in np.flatnonzero(np.asarray(_sig('s49_daily_stoch_down_4h', coin, btc)['se'])):
        gap_day = t.iloc[i] - pd.Timedelta(days=2)                     # remove the day before the cross day
        cut = coin[t != gap_day].reset_index(drop=True)
        c = S.indicators(cut); D = L27._days_v(c); k = L49._stoch_k(D, L49.ST)
        j = D.index.get_loc(t.iloc[i] - pd.Timedelta(days=1))
        if k.iloc[j] < 80 <= k.iloc[j - 1]:                           # the row-to-row cross still exists across the gap
            fired += 1
            sig = np.asarray(_sig('s49_daily_stoch_down_4h', cut, btc)['se'])
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            assert not sig[k0]
    assert fired


def test_v49_trix_cross_across_a_missing_day_does_not_fire():
    coin, btc = _V49_COIN['r49_daily_trix_4h'](), _ms(_mk(2600, '4h', 52))
    t = pd.to_datetime(coin.t).dt.normalize()
    full = _sig('r49_daily_trix_4h', coin, btc)
    fired = 0
    for side, up in (('le', True), ('se', False)):
        for i in np.flatnonzero(np.asarray(full[side])):
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=2)].reset_index(drop=True)
            D = L27._days_v(S.indicators(cut)); x = L49._trix_v(D, dict(n=15))
            j = D.index.get_loc(t.iloc[i] - pd.Timedelta(days=1))
            if (x.iloc[j] > 0 >= x.iloc[j - 1]) if up else (x.iloc[j] < 0 <= x.iloc[j - 1]):
                fired += 1
                sig = np.asarray(_sig('r49_daily_trix_4h', cut, btc)[side])
                assert not sig[np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]]
    assert fired


import longscout_v50 as L50
L50.register()

_V50_COIN = {'l50_atr_pullback_4h': lambda: _v36_coin(1),
             's50_atr_rebound_4h': lambda: _v36_coin(2, drift=-0.001),
             'l50_elder_impulse_4h': lambda: _ms(_mk(2600, '4h', 56))}


@pytest.mark.parametrize('key', sorted(L50.CANDIDATES))
def test_v50_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V50_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert le.sum() or se.sum(), key
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v50_helper_values_match_on_every_prefix():
    c = S.indicators(_v36_coin(1))
    D = L27._days_v(c)
    a, g = L50._datr(D, 14), L50._green(D)
    full = {'pl': L50._pb(c, L50.PB, True), 'ps': L50._pb(c, L50.PB, False), 'im': L50._impulse(c, {})}
    assert full['pl'].sum() and full['im'].sum()
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        Dp = L27._days_v(part)
        np.testing.assert_allclose(L50._datr(Dp, 14).values, a.values[:len(Dp)], equal_nan=True)
        np.testing.assert_array_equal(L50._green(Dp).values, g.values[:len(Dp)])
        np.testing.assert_array_equal(L50._pb(part, L50.PB, True), full['pl'][:cut])
        np.testing.assert_array_equal(L50._pb(part, L50.PB, False), full['ps'][:cut])
        np.testing.assert_array_equal(L50._impulse(part, {}), full['im'][:cut])


def test_v50_pullback_matches_an_independent_loop():
    coin, btc = _V50_COIN['s50_atr_rebound_4h'](), _ms(_mk(2600, '4h', 52))
    c = S.indicators(coin); D = L27._days_v(c)
    h, l, cl = D.h.values, D.l.values, D.c.values
    tr = np.r_[np.nan, [max(h[i] - l[i], abs(h[i] - cl[i - 1]), abs(l[i] - cl[i - 1])) for i in range(1, len(cl))]]
    flag = np.zeros(len(cl), bool)
    for i in range(15, len(cl)):
        atr = tr[i - 13:i + 1].mean()
        flag[i] = cl[i] >= cl[i - 10:i].min() + 3 * atr
    t = pd.to_datetime(c.t).dt.normalize().values
    sig = np.asarray(_sig('s50_atr_rebound_4h', coin, btc)['se'])
    assert sig.sum()
    for i in np.flatnonzero(sig):
        j = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
        assert flag[j] and not flag[j - 1] and pd.Timestamp(c.t.iloc[i]).hour == 0


def test_v50_first_flag_across_a_missing_day_does_not_fire():
    btc = _ms(_mk(2600, '4h', 52))
    fired = 0
    for key, side, flagf in (('s50_atr_rebound_4h', 'se', lambda D: L50._pb_flag(D, L50.PB, False)),
                             ('l50_elder_impulse_4h', 'le', L50._green)):
        coin = _V50_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(np.asarray(_sig(key, coin, btc)[side])):
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=2)].reset_index(drop=True)
            D = L27._days_v(S.indicators(cut)); f = flagf(D)
            j = D.index.get_loc(t.iloc[i] - pd.Timedelta(days=1))
            if f.iloc[j] and not f.iloc[j - 1]:
                fired += 1
                sig = np.asarray(_sig(key, cut, btc)[side])
                assert not sig[np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]], key
    assert fired


import longscout_v51 as L51
L51.register()

_V51 = {'r51_btc_quiet_squeeze_4h': (lambda: _ms(_mk(2600, '4h', 56)), lambda: _v36_coin(3)),
        'l51_momentum_accel_4h': (lambda: _ms(_mk(2600, '4h', 56)), lambda: _ms(_mk(2600, '4h', 52))),
        's51_momentum_accel_4h': (lambda: _v36_coin(2, drift=-0.001), lambda: _ms(_mk(2600, '4h', 52)))}


@pytest.mark.parametrize('key', sorted(L51.CANDIDATES))
def test_v51_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V51[key][0](), _V51[key][1]()
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert le.sum() or se.sum(), key
    if key.startswith('r51'):
        assert le.sum() and se.sum()
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v51_helper_values_match_on_every_prefix():
    coin, btc = _ms(_mk(2600, '4h', 56)), _v36_coin(3)
    c, b = S.indicators(coin), S.indicators(btc)
    P = dict(w=7, look=60)
    L.set_btc(b); fq = L51._squeeze(c, P); D, J = L33._joint(c); fl = L51._squeeze_flag(J, P)
    roc, acc = L51._accel_v(D, dict(n=10))
    fa = {up: L51._accel(c, dict(n=10), up) for up in (True, False)}
    assert fq.sum() and fa[True].sum() and fa[False].sum()
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        L.set_btc(b.iloc[:cut].reset_index(drop=True))
        Dp, Jp = L33._joint(part)
        np.testing.assert_array_equal(L51._squeeze_flag(Jp, P).values, fl.values[:len(Dp)])
        np.testing.assert_array_equal(L51._squeeze(part, P), fq[:cut])
        r2, a2 = L51._accel_v(Dp, dict(n=10))
        np.testing.assert_allclose(a2.values, acc.values[:len(Dp)], equal_nan=True)
        for up in (True, False):
            np.testing.assert_array_equal(L51._accel(part, dict(n=10), up), fa[up][:cut])


def test_v51_squeeze_matches_an_independent_loop():
    coin, btc = _ms(_mk(2600, '4h', 56)), _v36_coin(3)
    c = S.indicators(coin); L.set_btc(S.indicators(btc))
    D, J = L33._joint(c)
    rb = J.rb.values
    sd = np.full(len(rb), np.nan)
    for i in range(6, len(rb)):
        w = rb[i - 6:i + 1]
        if not np.isnan(w).any():
            sd[i] = np.std(w, ddof=1)
    flag = np.zeros(len(rb), bool)
    for i in range(61, len(rb)):
        prev = sd[i - 60:i]
        if not np.isnan(prev).any() and not np.isnan(sd[i]):
            flag[i] = sd[i] < prev.min()
    np.testing.assert_array_equal(L51._squeeze_flag(J, dict(w=7, look=60)).values, flag)
    assert flag.sum()


def test_v51_first_flag_across_a_missing_day_does_not_fire():
    fired = 0
    for key, side in (('r51_btc_quiet_squeeze_4h', None), ('s51_momentum_accel_4h', 'se')):
        coin, btc = _V51[key][0](), _V51[key][1]()
        full = _sig(key, coin, btc); t = pd.to_datetime(coin.t).dt.normalize()
        sig0 = np.asarray(full['le']) | np.asarray(full['se'])
        for i in np.flatnonzero(sig0):
            gap = t.iloc[i] - pd.Timedelta(days=2)
            cut = coin[t != gap].reset_index(drop=True)
            part = _sig(key, cut, btc)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            fired += 1
            assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired


def test_v51_entries_sit_on_the_first_flag_or_cross_day():
    coin, btc = _V51['r51_btc_quiet_squeeze_4h'][0](), _V51['r51_btc_quiet_squeeze_4h'][1]()
    full = _sig('r51_btc_quiet_squeeze_4h', coin, btc)
    c = S.indicators(coin); D, J = L33._joint(c); f = L51._squeeze_flag(J, dict(w=7, look=60))
    t = pd.to_datetime(c.t).dt.normalize().values
    sig = np.asarray(full['le']) | np.asarray(full['se'])
    assert sig.sum()
    for i in np.flatnonzero(sig):
        j = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
        assert f.iloc[j] and not f.iloc[j - 1] and pd.Timestamp(c.t.iloc[i]).hour == 0
    assert (f & f.shift(fill_value=False)).sum()                 # multi-day squeezes exist, so "first day" matters
    coin, btc = _V51['s51_momentum_accel_4h'][0](), _V51['s51_momentum_accel_4h'][1]()
    c = S.indicators(coin); D = L27._days_v(c); t = pd.to_datetime(c.t).dt.normalize().values
    cl = D.c.values
    sig = np.asarray(_sig('s51_momentum_accel_4h', coin, btc)['se'])
    assert sig.sum()
    for i in np.flatnonzero(sig):
        j = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
        roc = lambda k: cl[k] / cl[k - 10] - 1
        assert roc(j) < 0 and roc(j) - roc(j - 10) < 0 <= roc(j - 1) - roc(j - 11)


import longscout_v52 as L52
L52.register()

_V52_COINS = {'r52_daily_chop_trend_4h': (lambda: _v36_coin(1), lambda: _v36_coin(2, drift=-0.001)),
              'l52_weekly_ema_touch_4h': (lambda: _ms(_mk(2600, '4h', 55)),),
              's52_weekly_ema_touch_4h': (lambda: _ms(_mk(2600, '4h', 55)),)}


@pytest.mark.parametrize('key', sorted(L52.CANDIDATES))
def test_v52_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    btc = _ms(_mk(2600, '4h', 52))
    sides = set()
    for mk in _V52_COINS[key]:
        coin = mk()
        full = _sig(key, coin, btc)
        le, se = np.asarray(full['le']), np.asarray(full['se'])
        sides |= {'le'} if le.sum() else set(); sides |= {'se'} if se.sum() else set()
        cr = L.coin_regime(S.indicators(coin))
        assert (cr[le] == 1).all() and (cr[se] == -1).all()
        ent = np.flatnonzero(le | se)
        bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
        for i in bars:
            part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
            for k in ('le', 'se', 'lx', 'sx'):
                assert (part[k] == full[k][:i + 1]).all(), (key, k, i)
    assert sides == ({'le', 'se'} if key.startswith('r52') else {'le'} if key.startswith('l52') else {'se'})


def test_v52_helper_values_match_on_every_prefix():
    c = S.indicators(_ms(_mk(2600, '4h', 55)))
    D = L27._days_v(c)
    ch = L52._chop_v(D, 14); e, eo = L52._weekly_ema(D, 10)
    assert e.notna().sum() > 100
    full = {k: f(c) for k, f in (('ch', lambda x: L52._chop(x, dict(n=14, lvl=38.2))),
                                 ('tl', lambda x: L52._touch(x, dict(n=10), True)),
                                 ('ts', lambda x: L52._touch(x, dict(n=10), False)))}
    assert full['tl'].sum() and full['ts'].sum()
    for cut in range(700, 2600, 23):
        part = c.iloc[:cut].reset_index(drop=True)
        Dp = L27._days_v(part)
        np.testing.assert_allclose(L52._chop_v(Dp, 14).values, ch.values[:len(Dp)], equal_nan=True)
        e2, o2 = L52._weekly_ema(Dp, 10)
        np.testing.assert_allclose(e2.values, e.values[:len(Dp)], equal_nan=True)
        np.testing.assert_allclose(o2.values, eo.values[:len(Dp)], equal_nan=True)
        np.testing.assert_array_equal(L52._chop(part, dict(n=14, lvl=38.2)), full['ch'][:cut])
        np.testing.assert_array_equal(L52._touch(part, dict(n=10), True), full['tl'][:cut])
        np.testing.assert_array_equal(L52._touch(part, dict(n=10), False), full['ts'][:cut])


def test_v52_weekly_ema_and_chop_match_an_independent_loop():
    D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', 55))))
    days = list(D.index)
    weeks = {}
    for dday, cl in zip(days, D.c.values):
        weeks.setdefault(dday - pd.Timedelta(days=dday.dayofweek), []).append((dday, cl))
    full = sorted((m, v[-1][1]) for m, v in weeks.items() if len(v) == 7)
    ema, out, prev_m, run = None, {}, None, 0
    for m, cl in full:
        run = run + 1 if prev_m is not None and m - prev_m == pd.Timedelta(days=7) else 1
        ema = cl if ema is None else (2 / 11) * cl + (9 / 11) * ema
        out[m] = ema if run >= 11 else np.nan
        prev_m = m
    e, _ = L52._weekly_ema(D, 10)
    for dday, v in e.items():
        ref = out.get(dday - pd.Timedelta(days=dday.dayofweek) - pd.Timedelta(days=7), np.nan)
        assert (np.isnan(v) and np.isnan(ref)) or abs(v - ref) < 1e-9 * abs(ref), dday
    h, l, c = D.h.values, D.l.values, D.c.values
    tr = np.r_[h[0] - l[0], [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]]
    ch = np.full(len(c), np.nan)
    for i in range(13, len(c)):
        ch[i] = 100 * np.log10(tr[i - 13:i + 1].sum() / (h[i - 13:i + 1].max() - l[i - 13:i + 1].min())) / np.log10(14)
    np.testing.assert_allclose(L52._chop_v(D, 14).values, ch, equal_nan=True, rtol=1e-9)


def test_v52_chop_cross_across_a_missing_day_does_not_fire():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for mk in _V52_COINS['r52_daily_chop_trend_4h']:
        coin = mk(); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig('r52_daily_chop_trend_4h', coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=3)].reset_index(drop=True)
            D = L27._days_v(S.indicators(cut)); x = L52._chop_v(D, 14)
            j = D.index.get_loc(t.iloc[i] - pd.Timedelta(days=1))
            if x.iloc[j] < 38.2 <= x.iloc[j - 1]:
                fired += 1
                part = _sig('r52_daily_chop_trend_4h', cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0])
    assert fired


def test_v52_touch_entries_touch_and_close_on_the_right_side():
    btc = _ms(_mk(2600, '4h', 52)); coin = _ms(_mk(2600, '4h', 55))
    c = S.indicators(coin); D = L27._days_v(c); e, eo = L52._weekly_ema(D, 10)
    t = pd.to_datetime(c.t).dt.normalize().values
    for key, side in (('l52_weekly_ema_touch_4h', 'le'), ('s52_weekly_ema_touch_4h', 'se')):
        sig = np.asarray(_sig(key, coin, btc)[side])
        assert sig.sum()
        for i in np.flatnonzero(sig):
            j = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
            if side == 'le':
                assert e.iloc[j] > eo.iloc[j] and D.l.iloc[j] <= e.iloc[j] < D.c.iloc[j]
            else:
                assert e.iloc[j] < eo.iloc[j] and D.h.iloc[j] >= e.iloc[j] > D.c.iloc[j]


import longscout_v53 as L53
L53.register()

_V53_COIN = {'r53_daily_tk_cross_4h': lambda: _ms(_mk(2600, '4h', 55)),
             'l53_daily_coppock_4h': lambda: _ms(_mk(2600, '4h', 56)),
             'r53_daily_kst_4h': lambda: _ms(_mk(2600, '4h', 56))}


@pytest.mark.parametrize('key', sorted(L53.CANDIDATES))
def test_v53_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V53_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert le.sum() and (se.sum() or key.startswith('l53')), key
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def test_v53_helper_values_match_on_every_prefix():
    c = S.indicators(_ms(_mk(2600, '4h', 56)))
    D = L27._days_v(c)
    tk, cp, ks = L53._tk_v(D), L53._coppock_v(D), L53._kst_v(D)
    full = {'tu': L53._tk(c, {}, True), 'td': L53._tk(c, {}, False), 'cp': L53._coppock(c, {}),
            'ku': L53._kst(c, {}, True), 'kd': L53._kst(c, {}, False)}
    assert all(v.sum() for v in full.values())
    for cut in range(700, 2600, 29):
        part = c.iloc[:cut].reset_index(drop=True)
        Dp = L27._days_v(part); m = len(Dp)
        for a, b in zip(L53._tk_v(Dp) + (L53._coppock_v(Dp),) + L53._kst_v(Dp), tk + (cp,) + ks):
            np.testing.assert_allclose(a.values, b.values[:m], equal_nan=True)
        got = {'tu': L53._tk(part, {}, True), 'td': L53._tk(part, {}, False), 'cp': L53._coppock(part, {}),
               'ku': L53._kst(part, {}, True), 'kd': L53._kst(part, {}, False)}
        for k, v in got.items():
            np.testing.assert_array_equal(v, full[k][:cut])


def test_v53_indicators_match_an_independent_loop():
    D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', 56))))
    h, l, c = D.h.values, D.l.values, D.c.values; n = len(c)
    mid = lambda i, k: (h[i - k + 1:i + 1].max() + l[i - k + 1:i + 1].min()) / 2 if i >= k - 1 else np.nan
    t, k = L53._tk_v(D)
    np.testing.assert_allclose(t.values, [mid(i, 9) for i in range(n)], equal_nan=True, rtol=1e-12)
    np.testing.assert_allclose(k.values, [mid(i, 26) for i in range(n)], equal_nan=True, rtol=1e-12)
    roc = lambda i, k: 100 * (c[i] / c[i - k] - 1) if i >= k else np.nan
    s = [roc(i, 14) + roc(i, 11) for i in range(n)]
    cop = [np.nan if i < 23 else sum((j + 1) * s[i - 9 + j] for j in range(10)) / 55 for i in range(n)]
    np.testing.assert_allclose(L53._coppock_v(D).values, cop, equal_nan=True, rtol=1e-9)
    avg = lambda f, i, w: np.mean([f(j) for j in range(i - w + 1, i + 1)])
    kst = [np.nan if i < 44 else avg(lambda j: roc(j, 10), i, 10) + 2 * avg(lambda j: roc(j, 15), i, 10)
           + 3 * avg(lambda j: roc(j, 20), i, 10) + 4 * avg(lambda j: roc(j, 30), i, 15) for i in range(n)]
    np.testing.assert_allclose(L53._kst_v(D)[0].values, kst, equal_nan=True, rtol=1e-9)


def test_v53_entries_follow_the_cross_of_the_day_before():
    btc = _ms(_mk(2600, '4h', 52))
    for key, side, f in (('r53_daily_tk_cross_4h', 'se', lambda D: L53._tk_v(D)),
                         ('r53_daily_kst_4h', 'le', lambda D: L53._kst_v(D))):
        coin = _V53_COIN[key](); c = S.indicators(coin); D = L27._days_v(c); a, b = f(D)
        t = pd.to_datetime(c.t).dt.normalize().values
        sig = np.asarray(_sig(key, coin, btc)[side]); assert sig.sum()
        for i in np.flatnonzero(sig):
            j = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
            assert (a.iloc[j] > b.iloc[j] and a.iloc[j - 1] <= b.iloc[j - 1]) if side == 'le' else \
                   (a.iloc[j] < b.iloc[j] and a.iloc[j - 1] >= b.iloc[j - 1])
    coin = _V53_COIN['l53_daily_coppock_4h'](); c = S.indicators(coin); D = L27._days_v(c); x = L53._coppock_v(D)
    t = pd.to_datetime(c.t).dt.normalize().values
    sig = np.asarray(_sig('l53_daily_coppock_4h', coin, btc)['le']); assert sig.sum()
    for i in np.flatnonzero(sig):
        j = D.index.get_loc(pd.Timestamp(t[i]) - pd.Timedelta(days=1))
        assert x.iloc[j] < 0 and x.iloc[j] > x.iloc[j - 1] and x.iloc[j - 1] <= x.iloc[j - 2]


def test_v53_cross_across_a_missing_day_does_not_fire():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L53.CANDIDATES):
        coin = _V53_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            for back in (2, 3):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                same = (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0])
                if back == 2 or key.startswith('l53'):
                    fired += 1
                    assert not same, (key, back)
    assert fired


import longscout_v54 as L54
L54.register()


@pytest.mark.parametrize('key', sorted(L54.CANDIDATES))
def test_v54_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _ms(_mk(2600, '4h', 56)), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert le.sum() if key.startswith('l54') else se.sum(), key
    cr = L.coin_regime(S.indicators(coin))
    assert np.isin(cr[le], (0, 1)).all() and np.isin(cr[se], (0, -1)).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v54_ref(D, bottom, tol=0.03, depth=0.05):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); res = []
    for j in range(len(days)):
        if j < 41 or days[j] - days[j - 40] != pd.Timedelta(days=40):
            res.append(False); continue
        ext = (min if bottom else max)
        side = Lo if bottom else H
        w1 = list(range(j - 40, j - 20)); w2 = list(range(j - 20, j - 2))
        i1 = min(w1, key=lambda k: (side[k] if bottom else -side[k], k))
        i2 = min(w2, key=lambda k: (side[k] if bottom else -side[k], k))
        a, b = side[i1], side[i2]
        if bottom:
            neck = max(H[i1:i2 + 1]); ok = abs(b / a - 1) <= tol and neck >= (1 + depth) * max(a, b)
            res.append(ok and C[j] > neck >= C[j - 1])
        else:
            neck = min(Lo[i1:i2 + 1]); ok = abs(b / a - 1) <= tol and neck <= (1 - depth) * min(a, b)
            res.append(ok and C[j] < neck <= C[j - 1])
    return np.array(res)


def test_v54_pattern_matches_an_independent_loop_and_prefixes():
    for seed in range(50, 80):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for bottom in (True, False):
            v = L54._pattern_v(D, L54.DP, bottom).values
            np.testing.assert_array_equal(v, _v54_ref(D, bottom), err_msg=str((seed, bottom)))
            for cut in (range(100, len(D), 37) if seed in (55, 56, 58) else ()):
                np.testing.assert_array_equal(L54._pattern_v(D.iloc[:cut], L54.DP, bottom).values, v[:cut])
    D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', 56))))
    assert L54._pattern_v(D, L54.DP, True).sum() and L54._pattern_v(D, L54.DP, False).sum()


def test_v54_a_missing_day_inside_the_pattern_blocks_it():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L54.CANDIDATES):
        coin = _ms(_mk(2600, '4h', 56)); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            for back in (2, 20, 41):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


def test_v54_shallow_pattern_is_rejected_by_the_depth_rule():
    idx = pd.date_range('2024-01-01', periods=45, freq='D')
    for bottom in (True, False):
        base = np.full(45, 100.0)
        h, l, c = base + 0.5, base - 0.5, base.copy()
        j = 44
        if bottom:
            l[j - 30] = l[j - 10] = 97.0                                    # two troughs 3% under, neckline only ~3.6% up
            c[j - 1], c[j] = 100.0, 101.0; h[j] = 101.5; h[j - 20] = 100.5
        else:
            h[j - 30] = h[j - 10] = 103.0
            c[j - 1], c[j] = 100.0, 99.0; l[j] = 98.5; l[j - 20] = 99.5
        D = pd.DataFrame({'h': h, 'l': l, 'c': c, 'v': 1.0}, index=idx)
        assert not L54._pattern_v(D, L54.DP, bottom).iloc[j], bottom
        assert L54._pattern_v(D, dict(tol=0.03, depth=0.02), bottom).iloc[j], bottom


import longscout_v55 as L55
L55.register()

_V55_COIN = {'s55_head_shoulders_4h': lambda: _v36_coin(2, drift=-0.001),
             'l55_inverse_head_shoulders_4h': lambda: _ms(_mk(2600, '4h', 57)),
             'l55_bull_flag_4h': lambda: _v36_coin(1)}


@pytest.mark.parametrize('key', sorted(L55.CANDIDATES))
def test_v55_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V55_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (se if key.startswith('s55') else le).sum(), key
    cr = L.coin_regime(S.indicators(coin))
    ok = {'s55_head_shoulders_4h': (0, -1), 'l55_inverse_head_shoulders_4h': (0, 1), 'l55_bull_flag_4h': (1,)}[key]
    assert np.isin(cr[le | se], ok).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v55_hs_ref(D, top, head=0.05, tol=0.05):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); res = []
    for j in range(len(days)):
        if j < 61 or days[j] - days[j - 60] != pd.Timedelta(days=60):
            res.append(False); continue
        pick = (lambda w: max(w, key=lambda k: (H[k], -k))) if top else (lambda w: min(w, key=lambda k: (Lo[k], k)))
        a, b, r = pick(range(j - 60, j - 40)), pick(range(j - 40, j - 20)), pick(range(j - 20, j - 2))
        if top:
            ls, hd, rs = H[a], H[b], H[r]
            shape = hd >= (1 + head) * max(ls, rs) and abs(rs / ls - 1) <= tol
            neck = min(min(Lo[a:b + 1]), min(Lo[b:r + 1]))
            res.append(shape and C[j] < neck <= C[j - 1])
        else:
            ls, hd, rs = Lo[a], Lo[b], Lo[r]
            shape = hd <= (1 - head) * min(ls, rs) and abs(rs / ls - 1) <= tol
            neck = max(max(H[a:b + 1]), max(H[b:r + 1]))
            res.append(shape and C[j] > neck >= C[j - 1])
    return np.array(res)


def _v55_flag_ref(D, pole=0.20, flag=0.12):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); res = []
    for j in range(len(days)):
        if j < 21 or days[j] - days[j - 20] != pd.Timedelta(days=20):
            res.append(False); continue
        q = max(range(j - 10, j - 3), key=lambda k: (C[k], -k))
        ok = C[q] / min(Lo[q - 5:q + 1]) - 1 >= pole and max(H[q + 1:j]) <= H[q] and min(Lo[q + 1:j]) >= (1 - flag) * C[q]
        res.append(ok and C[j] > H[q] >= C[j - 1])
    return np.array(res)


def test_v55_patterns_match_an_independent_loop_and_prefixes():
    hits = {'hs': 0, 'ihs': 0, 'fl': 0}
    for seed in list(range(50, 80)) + ['v1', 'v2']:
        coin = _v36_coin(1) if seed == 'v1' else _v36_coin(2, drift=-0.001) if seed == 'v2' else _ms(_mk(2600, '4h', seed))
        D = L27._days_v(S.indicators(coin))
        for name, v, ref in (('hs', L55._hs_v(D, L55.HS, True).values, _v55_hs_ref(D, True)),
                             ('ihs', L55._hs_v(D, L55.HS, False).values, _v55_hs_ref(D, False)),
                             ('fl', L55._flag_v(D, dict(pole=0.20, flag=0.12)).values, _v55_flag_ref(D))):
            np.testing.assert_array_equal(v, ref, err_msg=str((seed, name)))
            hits[name] += int(v.sum())
        if seed in (57, 'v1', 'v2'):
            for cut in range(100, len(D), 41):
                Dp = D.iloc[:cut]
                np.testing.assert_array_equal(L55._hs_v(Dp, L55.HS, True).values, L55._hs_v(D, L55.HS, True).values[:cut])
                np.testing.assert_array_equal(L55._hs_v(Dp, L55.HS, False).values, L55._hs_v(D, L55.HS, False).values[:cut])
                np.testing.assert_array_equal(L55._flag_v(Dp, dict(pole=0.20, flag=0.12)).values,
                                              L55._flag_v(D, dict(pole=0.20, flag=0.12)).values[:cut])
    assert all(hits.values()), hits


def test_v55_synthetic_shapes_respect_each_threshold():
    idx = pd.date_range('2024-01-01', periods=65, freq='D')
    def hs(head_pct, rs_pct, top=True):
        base = np.full(65, 100.0); h, l, c = base + 0.5, base - 0.5, base.copy(); j = 64
        sgn = 1 if top else -1
        h_, l_ = (h, l) if top else (l, h)
        h_[j - 50] = 100 + sgn * 10; h_[j - 30] = 100 + sgn * (10 + head_pct); h_[j - 10] = 100 + sgn * (10 + rs_pct)
        c[j] = 100 - sgn * 1.0
        if top: l[j] = 98.5
        else: h[j] = 101.5
        return pd.DataFrame({'h': h, 'l': l, 'c': c, 'v': 1.0}, index=idx), j
    for top in (True, False):
        D, j = hs(8.0, 1.0, top); assert L55._hs_v(D, L55.HS, top).iloc[j], top           # good shape fires
        D, j = hs(3.0, 1.0, top); assert not L55._hs_v(D, L55.HS, top).iloc[j], top       # head too small
        D, j = hs(8.0, 6.5, top); assert not L55._hs_v(D, L55.HS, top).iloc[j], top       # shoulders too uneven
    idx = pd.date_range('2024-01-01', periods=25, freq='D')
    def fl(rise, dip):
        c = np.full(25, 100.0); j = 24; q = j - 6
        c[q - 5:q + 1] = np.linspace(100, 100 * (1 + rise), 6)
        c[q + 1:j] = c[q] * (1 - dip); c[j] = c[q] * 1.05
        h, l = c + 0.1, c - 0.1
        return pd.DataFrame({'h': h, 'l': l, 'c': c, 'v': 1.0}, index=idx), j
    P = dict(pole=0.20, flag=0.12)
    D, j = fl(0.30, 0.05); assert L55._flag_v(D, P).iloc[j]
    D, j = fl(0.15, 0.05); assert not L55._flag_v(D, P).iloc[j]                        # pole too short
    D, j = fl(0.30, 0.15); assert not L55._flag_v(D, P).iloc[j]                        # flag too deep


def test_v55_a_missing_day_inside_the_pattern_blocks_it():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L55.CANDIDATES):
        coin = _V55_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        span = 20 if key == 'l55_bull_flag_4h' else 60
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            for back in (2, span // 2, span + 1):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


def test_v55_right_shoulder_window_stops_two_days_before_the_break():
    idx = pd.date_range('2024-01-01', periods=65, freq='D')
    base = np.full(65, 100.0); h, l, c = base + 0.5, base - 0.5, base.copy(); j = 64
    h[j - 50], h[j - 30], h[j - 10] = 110.0, 118.0, 111.0
    c[j] = 99.0; l[j] = 98.5
    D = pd.DataFrame({'h': h, 'l': l, 'c': c, 'v': 1.0}, index=idx)
    assert L55._hs_v(D, L55.HS, True).iloc[j]
    D.loc[idx[j - 2], 'h'] = 125.0                                        # a later spike is outside the shoulder window
    assert L55._hs_v(D, L55.HS, True).iloc[j]
