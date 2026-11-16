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


import longscout_v56 as L56
L56.register()

_V56_COIN = {'s56_daily_upthrust_4h': lambda: _ms(_mk(2600, '4h', 55)),
             'l56_daily_spring_4h': lambda: _ms(_mk(2600, '4h', 56)),
             'r56_key_reversal_4h': lambda: _v36_coin(2, drift=-0.001)}


@pytest.mark.parametrize('key', sorted(L56.CANDIDATES))
def test_v56_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V56_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    if key.startswith('r56'):
        assert le.sum() and se.sum()
    else:
        assert (se if key.startswith('s56') else le).sum()
        cr = L.coin_regime(S.indicators(coin))
        assert np.isin(cr[le], (0, 1)).all() and np.isin(cr[se], (0, -1)).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v56_ref(D, kind, n):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); res = []
    for j in range(len(days)):
        if j < n or days[j] - days[j - n] != pd.Timedelta(days=n):
            res.append(False); continue
        hi, lo = max(H[j - n:j]), min(Lo[j - n:j]); rng = H[j] - Lo[j]
        pos = (C[j] - Lo[j]) / rng if rng > 0 else np.nan
        if kind == 'up':
            res.append(H[j] > hi and C[j] < hi and pos < 0.5)
        elif kind == 'spring':
            res.append(Lo[j] < lo and C[j] > lo and pos > 0.5)
        elif kind == 'ktop':
            res.append(H[j] > hi and C[j] < Lo[j - 1])
        else:
            res.append(Lo[j] < lo and C[j] > H[j - 1])
    return np.array(res, bool)


def test_v56_flags_match_an_independent_loop_and_prefixes():
    hits = dict(up=0, spring=0, ktop=0, kbot=0)
    for seed in list(range(50, 70)) + ['v2']:
        coin = _v36_coin(2, drift=-0.001) if seed == 'v2' else _ms(_mk(2600, '4h', seed))
        D = L27._days_v(S.indicators(coin))
        for kind, v in (('up', L56._fail_v(D, dict(n=30), True)), ('spring', L56._fail_v(D, dict(n=30), False)),
                        ('ktop', L56._key_v(D, dict(n=20), True)), ('kbot', L56._key_v(D, dict(n=20), False))):
            np.testing.assert_array_equal(v.values, _v56_ref(D, kind, 30 if kind in ('up', 'spring') else 20),
                                          err_msg=str((seed, kind)))
            hits[kind] += int(v.sum())
            if seed in (55, 'v2'):
                for cut in range(60, len(D), 37):
                    f = L56._fail_v if kind in ('up', 'spring') else L56._key_v
                    arg = dict(n=30) if kind in ('up', 'spring') else dict(n=20)
                    np.testing.assert_array_equal(f(D.iloc[:cut], arg, kind in ('up', 'ktop')).values, v.values[:cut])
    assert all(hits.values()), hits


def test_v56_entries_are_next_day_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L56.CANDIDATES):
        coin = _V56_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 10):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v57 as L57
L57.register()

_V57_COIN = {'r57_triangle_break_4h': lambda: _ms(_mk(2600, '4h', 57)),
             's57_rising_wedge_4h': lambda: _ms(_mk(2600, '4h', 57)),
             'l57_falling_wedge_4h': lambda: _ms(_mk(2600, '4h', 56))}


@pytest.mark.parametrize('key', sorted(L57.CANDIDATES))
def test_v57_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V57_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'r57': le.sum() and se.sum(), 's57': se.sum(), 'l57': le.sum()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    assert np.isin(cr[le], (0, 1)).all() and np.isin(cr[se], (0, -1)).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v57_ref(D, n=20):
    days = list(D.index); H, Lo, C = np.asarray(D.h), np.asarray(D.l), np.asarray(D.c); m = len(days)
    sh, ph, sl, pl = (np.full(m, np.nan) for _ in range(4))
    for j in range(n, m):
        bh, ah = np.polyfit(np.arange(n), H[j - n:j], 1); bl, al = np.polyfit(np.arange(n), Lo[j - n:j], 1)
        sh[j], ph[j], sl[j], pl[j] = bh, ah + bh * n, bl, al + bl * n
    out = {k: np.zeros(m, bool) for k in ('tri_up', 'tri_dn', 'rw', 'fw')}
    for j in range(n + 1, m):
        if days[j] - days[j - n - 1] != pd.Timedelta(days=n + 1):
            continue
        up = C[j] > ph[j] and C[j - 1] <= ph[j - 1]
        dn = C[j] < pl[j] and C[j - 1] >= pl[j - 1]
        out['tri_up'][j] = sh[j] < 0 < sl[j] and up
        out['tri_dn'][j] = sh[j] < 0 < sl[j] and dn
        out['rw'][j] = 0 < sh[j] < sl[j] and dn
        out['fw'][j] = sh[j] < sl[j] < 0 and up
    return out, (sh, ph, sl, pl)


def test_v57_lines_and_shapes_match_an_independent_polyfit():
    hits = dict(tri_up=0, tri_dn=0, rw=0, fw=0)
    for seed in range(50, 62):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        ref, lines = _v57_ref(D)
        for a, b in zip(L57._lines(D, 20), lines):
            np.testing.assert_allclose(a.values, b, equal_nan=True, rtol=1e-7, atol=1e-9)
        got = L57._shapes(D, dict(n=20))
        for k in hits:
            np.testing.assert_array_equal(got[k].values, ref[k], err_msg=str((seed, k)))
            hits[k] += int(ref[k].sum())
        if seed == 57:
            for cut in range(60, len(D), 37):
                g2 = L57._shapes(D.iloc[:cut], dict(n=20))
                for k in hits:
                    np.testing.assert_array_equal(g2[k].values, got[k].values[:cut])
    assert all(hits.values()), hits


def test_v57_entries_are_next_day_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L57.CANDIDATES):
        coin = _V57_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 12, 22):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v58 as L58
L58.register()

_V58_COIN = {'s58_rsi_bear_divergence_4h': lambda: _ms(_mk(2600, '4h', 56)),
             'l58_rsi_bull_divergence_4h': lambda: _ms(_mk(2600, '4h', 54))}


@pytest.mark.parametrize('key', sorted(L58.CANDIDATES))
def test_v58_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V58_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (se if key.startswith('s58') else le).sum() and not (le if key.startswith('s58') else se).sum()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v58_ref(D, bear, n=20, gap=5, pts=5.0):
    days = list(D.index); C = list(D.c); R = list(S.rsi(D.c, 14)); m = len(C)
    isext = lambda i: i >= n and (C[i] > max(C[i - n:i]) if bear else C[i] < min(C[i - n:i]))
    res = []
    for j in range(m):
        if j < 2 * n or days[j] - days[j - 2 * n] != pd.Timedelta(days=2 * n) or not isext(j):
            res.append(False); continue
        w = list(range(j - n, j - gap + 1))
        i = (max if bear else min)(w, key=lambda k: (C[k], -k) if bear else (C[k], k))
        if not isext(i) or np.isnan(R[i]) or np.isnan(R[j]):
            res.append(False); continue
        res.append(R[j] <= R[i] - pts if bear else R[j] >= R[i] + pts)
    return np.array(res, bool)


def test_v58_divergence_matches_an_independent_loop_and_prefixes():
    hits = {True: 0, False: 0}
    for seed in range(50, 66):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for bear in (True, False):
            v = L58._div_v(D, L58.DV, bear).values
            np.testing.assert_array_equal(v, _v58_ref(D, bear), err_msg=str((seed, bear)))
            hits[bear] += int(v.sum())
            if seed in (54, 56):
                for cut in range(60, len(D), 31):
                    np.testing.assert_array_equal(L58._div_v(D.iloc[:cut], L58.DV, bear).values, v[:cut])
    assert all(hits.values())


def test_v58_rsi_gap_threshold_is_respected():
    D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', 56))))
    loose = L58._div_v(D, dict(L58.DV, pts=0.0), True); strict = L58._div_v(D, L58.DV, True)
    assert (strict <= loose).all() and loose.sum() > strict.sum() > 0


def test_v58_entries_are_next_day_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L58.CANDIDATES):
        coin = _V58_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 30):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v59 as L59
L59.register()

_V59_COIN = {'r59_weekly_engulfing_4h': lambda: _ms(_mk(2600, '4h', 57)),
             'l59_range_top_persist_4h': lambda: _ms(_mk(2600, '4h', 56)),
             's59_range_low_persist_4h': lambda: _ms(_mk(2600, '4h', 54))}


@pytest.mark.parametrize('key', sorted(L59.CANDIDATES))
def test_v59_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V59_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'r59': le.sum() and se.sum(), 'l59': le.sum(), 's59': se.sum()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    if key.startswith('r59'):
        assert (pd.to_datetime(coin.t[le | se]).dt.dayofweek == 0).all()            # Mondays only
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v59_week_ref(D, bull):
    days = list(D.index); O, C = list(D.o), list(D.c)
    pos = {dd: i for i, dd in enumerate(days)}
    res = np.zeros(len(days), bool)
    for i, dd in enumerate(days):
        if dd.dayofweek != 6:
            continue
        wk = [dd - pd.Timedelta(days=k) for k in range(6, -1, -1)]
        pw = [w - pd.Timedelta(days=7) for w in wk]
        if not all(w in pos for w in wk + pw):
            continue
        o1, c1 = O[pos[pw[0]]], C[pos[pw[-1]]]; o2, c2 = O[pos[wk[0]]], C[pos[wk[-1]]]
        res[i] = (c1 < o1 and c2 > o2 and o2 <= c1 and c2 > o1) if bull else (c1 > o1 and c2 < o2 and o2 >= c1 and c2 < o1)
    return res


def _v59_persist_ref(D, top, n=50, k=10, q=0.25):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); m = len(days)
    zone = []
    for j in range(m):
        if j < n - 1:
            zone.append(False); continue
        hi, lo = max(H[j - n + 1:j + 1]), min(Lo[j - n + 1:j + 1])
        p = (C[j] - lo) / (hi - lo) if hi > lo else np.nan
        zone.append(bool(p >= 1 - q) if top else bool(p <= q))
    res = np.zeros(m, bool)
    for j in range(n + k, m):
        if days[j] - days[j - n - k] != pd.Timedelta(days=n + k):
            continue
        res[j] = all(zone[j - k + 1:j + 1]) and not zone[j - k]
    return res


def test_v59_flags_match_an_independent_loop_and_prefixes():
    hits = dict(wb=0, ws=0, pt=0, pl=0)
    for seed in range(50, 64):
        c = S.indicators(_ms(_mk(2600, '4h', seed)))
        Do, Dv = L17._days(c), L27._days_v(c)
        for name, v, ref in (('wb', L59._weekly_v(Do, True), _v59_week_ref(Do, True)),
                             ('ws', L59._weekly_v(Do, False), _v59_week_ref(Do, False)),
                             ('pt', L59._persist_v(Dv, L59.PP, True), _v59_persist_ref(Dv, True)),
                             ('pl', L59._persist_v(Dv, L59.PP, False), _v59_persist_ref(Dv, False))):
            np.testing.assert_array_equal(v.values, ref, err_msg=str((seed, name)))
            hits[name] += int(ref.sum())
        if seed in (54, 57):
            for cut in range(80, len(Do), 29):
                np.testing.assert_array_equal(L59._weekly_v(Do.iloc[:cut], True).values,
                                              L59._weekly_v(Do, True).values[:cut])
                np.testing.assert_array_equal(L59._persist_v(Dv.iloc[:cut], L59.PP, False).values,
                                              L59._persist_v(Dv, L59.PP, False).values[:cut])
    assert all(hits.values()), hits


def test_v59_entries_are_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L59.CANDIDATES):
        coin = _V59_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in ((3, 10) if key.startswith('r59') else (2, 30)):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


def test_v59_weekly_engulfing_synthetic_open_and_week_gap_rules():
    def weeks(spec):                                                       # spec: list of (monday, open, close)
        rows = []
        for mon, o, c in spec:
            for k in range(7):
                dd = pd.Timestamp(mon) + pd.Timedelta(days=k)
                rows.append((dd, o if k == 0 else (o + c) / 2, max(o, c) + 1, min(o, c) - 1, c if k == 6 else (o + c) / 2))
        D = pd.DataFrame(rows, columns=['day', 'o', 'h', 'l', 'c']).set_index('day')
        return D
    sun = pd.Timestamp('2024-01-14')
    D = weeks([('2024-01-01', 110, 100), ('2024-01-08', 100, 115)])       # red then engulfing green
    assert L59._weekly_v(D, True).loc[sun]
    D = weeks([('2024-01-01', 110, 100), ('2024-01-08', 103, 115)])       # opened above prior close
    assert not L59._weekly_v(D, True).loc[sun]
    D = weeks([('2024-01-01', 100, 110), ('2024-01-08', 110, 95)])        # green then engulfing red
    assert L59._weekly_v(D, False).loc[sun]
    D = weeks([('2024-01-01', 100, 110), ('2024-01-08', 107, 95)])        # opened below prior close
    assert not L59._weekly_v(D, False).loc[sun]
    D = weeks([('2023-12-25', 110, 100), ('2024-01-08', 100, 115)])       # a missing week between them
    assert not L59._weekly_v(D, True).any()


import longscout_v60 as L60
L60.register()

_V60_COIN = {'l60_inside_week_break_4h': lambda: _v36_coin(1),
             's60_inside_week_break_4h': lambda: _v36_coin(2, drift=-0.001)}


@pytest.mark.parametrize('key', sorted(L60.CANDIDATES))
def test_v60_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V60_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (le if key.startswith('l60') else se).sum()
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v60_ref(D, up):
    days = list(D.index); pos = {dd: i for i, dd in enumerate(days)}
    H, Lo, C = list(D.h), list(D.l), list(D.c)
    res = np.zeros(len(days), bool)
    def wk(mon):
        ds = [mon + pd.Timedelta(days=k) for k in range(7)]
        if not all(x in pos for x in ds):
            return None
        return max(H[pos[x]] for x in ds), min(Lo[pos[x]] for x in ds)
    done = set()
    for i, dd in enumerate(days):
        mon = dd - pd.Timedelta(days=dd.dayofweek)
        w, p = wk(mon - pd.Timedelta(days=7)), wk(mon - pd.Timedelta(days=14))
        if w is None or p is None or not (w[0] < p[0] and w[1] > p[1]) or mon in done:
            continue
        if (C[i] > w[0]) if up else (C[i] < w[1]):
            res[i] = True; done.add(mon)
    return res


def test_v60_flags_match_an_independent_loop_and_prefixes():
    hits = {True: 0, False: 0}
    for seed in list(range(50, 66)) + ['v1', 'v2']:
        coin = _v36_coin(1) if seed == 'v1' else _v36_coin(2, drift=-0.001) if seed == 'v2' else _ms(_mk(2600, '4h', seed))
        D = L17._days(S.indicators(coin))
        for up in (True, False):
            v = L60._inside_v(D, up).values
            np.testing.assert_array_equal(v, _v60_ref(D, up), err_msg=str((seed, up)))
            hits[up] += int(v.sum())
            if seed in ('v1', 'v2'):
                for cut in range(30, len(D), 23):
                    np.testing.assert_array_equal(L60._inside_v(D.iloc[:cut], up).values, v[:cut])
    assert all(hits.values()), hits


def test_v60_only_the_first_close_beyond_the_level_fires_and_gaps_block():
    idx = pd.date_range('2024-01-01', periods=21, freq='D')               # Mon 1 Jan .. Sun 21 Jan
    h = np.r_[np.full(7, 120.0), np.full(7, 110.0), np.full(7, 100.0)]
    l = np.r_[np.full(7, 80.0), np.full(7, 90.0), np.full(7, 95.0)]
    c = np.r_[np.full(14, 100.0), [100, 112, 113, 105, 114, 100, 100]]
    D = pd.DataFrame({'o': c, 'h': np.maximum(h, c + 1), 'l': l, 'c': c}, index=idx)
    f = L60._inside_v(D, True)
    assert list(f[f].index) == [idx[15]]                                  # Tue of week 3, not Wed or Fri
    assert not L60._inside_v(D.drop(idx[3]), True).any()                  # an incomplete outer week voids the pattern
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L60.CANDIDATES):
        coin = _V60_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            mon = t.iloc[i] - pd.Timedelta(days=1); mon = mon - pd.Timedelta(days=mon.dayofweek)
            cut = coin[t != mon - pd.Timedelta(days=4)].reset_index(drop=True)   # break the inside week
            part = _sig(key, cut, btc)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            fired += 1
            assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired


def test_v60_outer_week_must_be_the_week_right_before():
    idx = pd.date_range('2024-01-01', periods=7, freq='D').append(pd.date_range('2024-01-15', periods=14, freq='D'))
    h = np.r_[np.full(7, 120.0), np.full(7, 110.0), np.full(7, 100.0)]
    l = np.r_[np.full(7, 80.0), np.full(7, 90.0), np.full(7, 95.0)]
    c = np.r_[np.full(14, 100.0), [100, 112, 100, 100, 100, 100, 100]]
    D = pd.DataFrame({'o': c, 'h': np.maximum(h, c + 1), 'l': l, 'c': c}, index=idx)
    assert not L60._inside_v(D, True).any()                               # week of 8 Jan is missing entirely


import longscout_v61 as L61
L61.register()

_V61_COIN = {'s61_bear_engulf_high_4h': lambda: _ms(_mk(2600, '4h', 55)),
             'l61_bull_engulf_low_4h': lambda: _ms(_mk(2600, '4h', 58)),
             's61_evening_star_4h': lambda: _ms(_mk(2600, '4h', 54))}


@pytest.mark.parametrize('key', sorted(L61.CANDIDATES))
def test_v61_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V61_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (le if key.startswith('l61') else se).sum()
    cr = L.coin_regime(S.indicators(coin))
    assert np.isin(cr[le], (0, 1)).all() and np.isin(cr[se], (0, -1)).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v61_ref(D, kind, n=20):
    days = list(D.index); O, H, Lo, C = list(D.o), list(D.h), list(D.l), list(D.c); m = len(days)
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        hi20, lo20 = max(H[j - n + 1:j + 1]), min(Lo[j - n + 1:j + 1])
        if kind == 'bear':
            res[j] = C[j - 1] > O[j - 1] and C[j] < O[j] and O[j] >= C[j - 1] and C[j] < O[j - 1] and max(H[j - 1], H[j]) >= hi20
        elif kind == 'bull':
            res[j] = C[j - 1] < O[j - 1] and C[j] > O[j] and O[j] <= C[j - 1] and C[j] > O[j - 1] and min(Lo[j - 1], Lo[j]) <= lo20
        else:
            b2, r2, b1 = C[j - 2] - O[j - 2], H[j - 2] - Lo[j - 2], abs(C[j - 1] - O[j - 1])
            res[j] = (b2 > 0 and b2 >= 0.6 * r2 and b1 <= 0.3 * b2 and C[j] < O[j] and C[j] < (O[j - 2] + C[j - 2]) / 2
                      and max(H[j - 2:j + 1]) >= hi20)
    return res


def test_v61_flags_match_an_independent_loop_and_prefixes():
    hits = dict(bear=0, bull=0, star=0)
    for seed in range(50, 70):
        D = L17._days(S.indicators(_ms(_mk(2600, '4h', seed))))
        for kind, v in (('bear', L61._engulf_v(D, dict(n=20), True)), ('bull', L61._engulf_v(D, dict(n=20), False)),
                        ('star', L61._star_v(D, dict(n=20)))):
            np.testing.assert_array_equal(v.values, _v61_ref(D, kind), err_msg=str((seed, kind)))
            hits[kind] += int(v.sum())
            if seed in (54, 55):
                for cut in range(40, len(D), 31):
                    f = (lambda x: L61._star_v(x, dict(n=20))) if kind == 'star' else \
                        (lambda x: L61._engulf_v(x, dict(n=20), kind == 'bear'))
                    np.testing.assert_array_equal(f(D.iloc[:cut]).values, v.values[:cut])
    assert all(hits.values()), hits


def test_v61_synthetic_open_and_star_thresholds():
    idx = pd.date_range('2024-01-01', periods=25, freq='D')
    def frame(rows):
        base = [(100, 101, 99, 100)] * (25 - len(rows)) + rows
        return pd.DataFrame(base, columns=['o', 'h', 'l', 'c'], index=idx)
    D = frame([(100, 111, 99, 110), (110, 112, 97, 98)])                 # green then engulfing red at the high
    assert L61._engulf_v(D, dict(n=20), True).iloc[-1]
    D = frame([(100, 111, 99, 110), (108, 112, 97, 98)])                 # opened below the prior close
    assert not L61._engulf_v(D, dict(n=20), True).iloc[-1]
    D = frame([(110, 111, 99, 100), (102, 113, 99, 112)])                # bullish engulfing but opened above prior close
    assert not L61._engulf_v(D.assign(l=D.l - 5), dict(n=20), False).iloc[-1]
    D = frame([(100, 111, 99, 110), (110, 112, 109, 111), (111, 111.5, 103, 104)])
    assert L61._star_v(D, dict(n=20)).iloc[-1]
    D = frame([(100, 111, 99, 110), (110, 114, 109, 113.5), (111, 111.5, 103, 104)])   # middle body too big
    assert not L61._star_v(D, dict(n=20)).iloc[-1]
    D = frame([(100, 120, 90, 110), (110, 112, 109, 111), (111, 111.5, 103, 104)])     # first body < 60% of range
    assert not L61._star_v(D, dict(n=20)).iloc[-1]
    D = frame([(100, 111, 99, 110), (110, 112, 109, 111), (111, 111.5, 105, 106)])     # closes above the midpoint
    assert not L61._star_v(D, dict(n=20)).iloc[-1]


def test_v61_entries_are_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L61.CANDIDATES):
        coin = _V61_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 15):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v62 as L62
L62.register()


def _v62_coin(seed=58):
    c = _mk(2600, '4h', seed); day = np.arange(len(c)) // 6
    return _ms(c.assign(v=c.v * np.where(day % 5 == 0, 8.0, 1.0)))       # every 5th day trades 8x volume


@pytest.mark.parametrize('key', sorted(L62.CANDIDATES))
def test_v62_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _v62_coin(), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (le if key.startswith('l62') else se).sum()
    cr = L.coin_regime(S.indicators(coin))
    assert np.isin(cr[le], (0, 1)).all() and np.isin(cr[se], (0, -1)).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v62_ref(D, top, n=20, k=3.0):
    days = list(D.index); H, Lo, C, V = list(D.h), list(D.l), list(D.c), list(D.v); res = np.zeros(len(days), bool)
    for j in range(n, len(days)):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        rng = H[j] - Lo[j]; pos = (C[j] - Lo[j]) / rng if rng > 0 else np.nan
        vol = V[j] > k * np.mean(V[j - n:j])
        if top:
            res[j] = H[j] > max(H[j - n:j]) and vol and pos < 0.5
        else:
            res[j] = Lo[j] < min(Lo[j - n:j]) and vol and pos > 0.5
    return res


def test_v62_flags_match_an_independent_loop_and_prefixes():
    hits = {True: 0, False: 0}
    for seed in range(50, 66):
        D = L27._days_v(S.indicators(_v62_coin(seed)))
        for top in (True, False):
            v = L62._climax_v(D, L62.CP, top).values
            np.testing.assert_array_equal(v, _v62_ref(D, top), err_msg=str((seed, top)))
            hits[top] += int(v.sum())
            if seed == 58:
                for cut in range(40, len(D), 23):
                    np.testing.assert_array_equal(L62._climax_v(D.iloc[:cut], L62.CP, top).values, v[:cut])
    assert all(hits.values())
    D = L27._days_v(S.indicators(_v62_coin(58)))
    loose = L62._climax_v(D, dict(L62.CP, k=0.0), True)
    assert loose.sum() > L62._climax_v(D, L62.CP, True).sum()               # the volume rule removes days


def test_v62_entries_are_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L62.CANDIDATES):
        coin = _v62_coin(); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 15):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v63 as L63
L63.register()

_V63_COIN = {'l63_vcp_breakout_4h': lambda: _v36_coin(1), 's63_vcp_breakdown_4h': lambda: _v36_coin(2, drift=-0.001)}


@pytest.mark.parametrize('key', sorted(L63.CANDIDATES))
def test_v63_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V63_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (le if key.startswith('l63') else se).sum()
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v63_ref(D, up, w=10, ratio=0.5):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); res = np.zeros(len(days), bool)
    rng = lambda a, b: (max(H[a:b]) - min(Lo[a:b])) / max(H[a:b])
    for j in range(3 * w, len(days)):
        if days[j] - days[j - 3 * w] != pd.Timedelta(days=3 * w):
            continue
        A, B, Cc = rng(j - 30, j - 20), rng(j - 20, j - 10), rng(j - 10, j)
        tight = A > B > Cc and Cc <= ratio * A
        brk = C[j] > max(H[j - 30:j]) if up else C[j] < min(Lo[j - 30:j])
        res[j] = tight and brk
    return res


def test_v63_flags_match_an_independent_loop_and_prefixes():
    hits = {True: 0, False: 0}
    for seed in list(range(50, 66)) + ['v1', 'v2']:
        coin = _v36_coin(1) if seed == 'v1' else _v36_coin(2, drift=-0.001) if seed == 'v2' else _ms(_mk(2600, '4h', seed))
        D = L27._days_v(S.indicators(coin))
        for up in (True, False):
            v = L63._vcp_v(D, L63.VP, up).values
            np.testing.assert_array_equal(v, _v63_ref(D, up), err_msg=str((seed, up)))
            hits[up] += int(v.sum())
            if seed in ('v1', 'v2'):
                for cut in range(40, len(D), 23):
                    np.testing.assert_array_equal(L63._vcp_v(D.iloc[:cut], L63.VP, up).values, v[:cut])
    assert all(hits.values())


def test_v63_contraction_rule_is_needed_and_gaps_block():
    D = L27._days_v(S.indicators(_v36_coin(1)))
    loose = L63._vcp_v(D, dict(L63.VP, ratio=10.0), True); strict = L63._vcp_v(D, L63.VP, True)
    assert (strict <= loose).all() and loose.sum() > strict.sum()
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L63.CANDIDATES):
        coin = _V63_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 25):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v64 as L64
L64.register()

_V64_COIN = {'l64_up_day_ratio_4h': lambda: _ms(_mk(2600, '4h', 56)), 's64_down_day_ratio_4h': lambda: _ms(_mk(2600, '4h', 54))}


@pytest.mark.parametrize('key', sorted(L64.CANDIDATES))
def test_v64_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V64_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (le if key.startswith('l64') else se).sum()
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v64_ref(D, up, n=20, k=14):
    days = list(D.index); C = list(D.c); res = np.zeros(len(days), bool)
    cnt = lambda j: sum(1 for i in range(j - n + 1, j + 1) if (C[i] > C[i - 1] if up else C[i] < C[i - 1]))
    for j in range(n + 1, len(days)):
        if days[j] - days[j - n - 1] != pd.Timedelta(days=n + 1):
            continue
        res[j] = cnt(j) >= k and cnt(j - 1) < k
    return res


def test_v64_flags_match_an_independent_loop_and_prefixes():
    hits = {True: 0, False: 0}
    for seed in range(50, 66):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L64._ratio_v(D, L64.RP, up).values
            np.testing.assert_array_equal(v, _v64_ref(D, up), err_msg=str((seed, up)))
            hits[up] += int(v.sum())
            if seed in (54, 56):
                for cut in range(30, len(D), 23):
                    np.testing.assert_array_equal(L64._ratio_v(D.iloc[:cut], L64.RP, up).values, v[:cut])
    assert all(hits.values())


def test_v64_entries_are_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L64.CANDIDATES):
        coin = _V64_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 15):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


def test_v64_flat_days_count_as_neither_up_nor_down():
    idx = pd.date_range('2024-01-01', periods=30, freq='D')
    c = np.r_[np.full(9, 100.0), 100 - np.arange(1, 14), np.full(8, 87.0)]      # 13 down days then flat days
    D = pd.DataFrame({'h': c + 1, 'l': c - 1, 'c': c, 'v': 1.0}, index=idx)
    assert not L64._ratio_v(D, L64.RP, False).any()                      # flat days must not reach 14 "down"
    c2 = np.r_[np.full(9, 100.0), 100 - np.arange(1, 15), np.full(7, 86.0)]
    D2 = pd.DataFrame({'h': c2 + 1, 'l': c2 - 1, 'c': c2, 'v': 1.0}, index=idx)
    assert L64._ratio_v(D2, L64.RP, False).sum() == 1


import longscout_v65 as L65
L65.register()


@pytest.mark.parametrize('key', sorted(L65.CANDIDATES))
def test_v65_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _ms(_mk(2600, '4h', 59)), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert (le if key.startswith('l65') else se).sum()
    cr = L.coin_regime(S.indicators(coin))
    assert np.isin(cr[le], (0, 1)).all() and np.isin(cr[se], (0, -1)).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v65_ref(D, high, n=180, age=5):
    days = list(D.index); H, Lo, C, V = list(D.h), list(D.l), list(D.c), list(D.v)
    tp = [(H[i] + Lo[i] + C[i]) / 3 for i in range(len(C))]
    res = np.zeros(len(C), bool)
    vw = lambda a, t: sum(tp[i] * V[i] for i in range(a, t + 1)) / sum(V[i] for i in range(a, t + 1))
    for j in range(n, len(C)):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        w = range(j - n, j)
        a = max(w, key=lambda k: (H[k], -k)) if high else min(w, key=lambda k: (Lo[k], k))
        if a > j - age:
            continue
        now, prev = vw(a, j), vw(a, j - 1)
        res[j] = (C[j] < now and C[j - 1] >= prev) if high else (C[j] > now and C[j - 1] <= prev)
    return res


def test_v65_avwap_matches_an_independent_loop_and_prefixes():
    hits = {True: 0, False: 0}
    for seed in (55, 57, 59, 61):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for high in (True, False):
            v = L65._avwap_v(D, L65.AP, high).values
            np.testing.assert_array_equal(v, _v65_ref(D, high), err_msg=str((seed, high)))
            hits[high] += int(v.sum())
            if seed == 59:
                for cut in range(190, len(D), 29):
                    np.testing.assert_array_equal(L65._avwap_v(D.iloc[:cut], L65.AP, high).values, v[:cut])
    assert all(hits.values())


def test_v65_young_anchor_is_refused_and_gaps_block():
    D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', 59))))
    loose = L65._avwap_v(D, dict(L65.AP, age=0), True)
    assert loose.sum() > L65._avwap_v(D, L65.AP, True).sum()
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L65.CANDIDATES):
        coin = _ms(_mk(2600, '4h', 59)); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se'])):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 100):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v66 as L66
L66.register()

_V66_COIN = {'r66_ma_compression_break_4h': lambda: _ms(_mk(2600, '4h', 59)),
             'l66_pocket_pivot_4h': lambda: _ms(_mk(2600, '4h', 58)),
             's66_pocket_pivot_short_4h': lambda: _ms(_mk(2600, '4h', 54))}


@pytest.mark.parametrize('key', sorted(L66.CANDIDATES))
def test_v66_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V66_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'r66': le.sum() and se.sum(), 'l66': le.sum(), 's66': se.sum()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    if key.startswith('r66'):
        assert np.isin(cr[le], (0, 1)).all() and np.isin(cr[se], (0, -1)).all()
    else:
        assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v66_ref(D, kind, up):
    days = list(D.index); C, V = list(D.c), list(D.v); m = len(C)
    def ema(n):
        out, a = [], 2 / (n + 1)
        for x in C:
            out.append(x if not out else a * x + (1 - a) * out[-1])
        return out
    e10, e20, e50, e100 = ema(10), ema(20), ema(50), ema(100)
    res = np.zeros(m, bool)
    for j in range(2, m):
        if kind == 'comp':
            if days[j] - days[j - 2] != pd.Timedelta(days=2):
                continue
            tri = lambda k: (e20[k], e50[k], e100[k])
            mean = sum(tri(j - 1)) / 3
            tight = max(tri(j - 1)) / mean - 1 <= 0.03 and 1 - min(tri(j - 1)) / mean <= 0.03
            side = (lambda k: C[k] > max(tri(k))) if up else (lambda k: C[k] < min(tri(k)))
            res[j] = tight and side(j) and not side(j - 1)
        else:
            if j < 11 or days[j] - days[j - 11] != pd.Timedelta(days=11):
                continue
            opp = [V[i] for i in range(j - 10, j) if (C[i] < C[i - 1] if up else C[i] > C[i - 1])]
            vmax = max(opp) if opp else 0.0
            dirn = C[j] > C[j - 1] if up else C[j] < C[j - 1]
            res[j] = dirn and (C[j] > e10[j] if up else C[j] < e10[j]) and V[j] > vmax and vmax > 0
    return res


def test_v66_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in range(53, 62):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for kind, fn, p in (('comp', L66._comp_v, dict(band=0.03)), ('piv', L66._pivot_v, dict(n=10))):
            for up in (True, False):
                v = fn(D, p, up).values
                np.testing.assert_array_equal(v, _v66_ref(D, kind, up), err_msg=str((seed, kind, up)))
                hits[(kind, up)] = hits.get((kind, up), 0) + int(v.sum())
                if seed == 58:
                    for cut in range(30, len(D), 29):
                        np.testing.assert_array_equal(fn(D.iloc[:cut], p, up).values, v[:cut])
    assert all(hits.values()), hits


def test_v66_entries_are_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L66.CANDIDATES):
        coin = _V66_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))[:25]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            backs = (2,) if key.startswith('r66') else (2, 8)
            for back in backs:
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v67 as L67
L67.register()

_V67_COIN = {'r67_regression_channel_break_4h': lambda: _ms(_mk(2600, '4h', 57)),
             'l67_52w_high_4h': lambda: _ms(_mk(2600, '4h', 56)),
             's67_52w_low_4h': lambda: _ms(_mk(2600, '4h', 54))}


@pytest.mark.parametrize('key', sorted(L67.CANDIDATES))
def test_v67_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V67_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'r67': le.sum() and se.sum(), 'l67': le.sum(), 's67': se.sum()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v67_chan_ref(D, up, n=50, k=2.0):
    days = list(D.index); C = np.asarray(D.c); m = len(C)
    P, Sd = np.full(m, np.nan), np.full(m, np.nan)
    for j in range(n, m):
        y = C[j - n:j]; b, a = np.polyfit(np.arange(n), y, 1)
        r = y - (a + b * np.arange(n)); P[j] = a + b * n; Sd[j] = np.sqrt((r ** 2).sum() / (n - 2))
    res = np.zeros(m, bool)
    for j in range(n + 1, m):
        if days[j] - days[j - n - 1] != pd.Timedelta(days=n + 1):
            continue
        if up:
            res[j] = C[j] > P[j] + k * Sd[j] and C[j - 1] <= P[j - 1] + k * Sd[j - 1]
        else:
            res[j] = C[j] < P[j] - k * Sd[j] and C[j - 1] >= P[j - 1] - k * Sd[j - 1]
    return res


def _v67_year_ref(D, up, n=365, quiet=90):
    days = list(D.index); C = list(D.c); m = len(C)
    new = [j >= n and (C[j] > max(C[j - n:j]) if up else C[j] < min(C[j - n:j])) for j in range(m)]
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        res[j] = new[j] and j - quiet >= 0 and not any(new[j - quiet:j])
    return res


def test_v67_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (53, 54, 56, 57, 58):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for kind, fn, p, ref in (('ch', L67._chan_v, dict(n=50, k=2.0), _v67_chan_ref),
                                 ('yr', L67._year_v, L67.YP, _v67_year_ref)):
            for up in (True, False):
                v = fn(D, p, up).values
                np.testing.assert_array_equal(v, ref(D, up), err_msg=str((seed, kind, up)))
                hits[(kind, up)] = hits.get((kind, up), 0) + int(v.sum())
                if seed == 57:
                    for cut in range(60, len(D), 37):
                        np.testing.assert_array_equal(fn(D.iloc[:cut], p, up).values, v[:cut])
    assert all(hits.values()), hits


def test_v67_entries_are_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L67.CANDIDATES):
        coin = _V67_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))[:20]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 40):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


import longscout_v68 as L68
L68.register()

_V68_COIN = {'l68_dormant_high_4h': lambda: _ms(_mk(2600, '4h', 59)),
             's68_dormant_low_4h': lambda: _ms(_mk(2600, '4h', 48)),
             'l68_breakout_retest_4h': lambda: _ms(_mk(2600, '4h', 56))}


@pytest.mark.parametrize('key', sorted(L68.CANDIDATES))
def test_v68_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V68_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l68': le.sum() and not se.any(), 's68': se.sum() and not le.any()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    ok = {'l68_dormant_high_4h': (0, 1), 's68_dormant_low_4h': (0, -1), 'l68_breakout_retest_4h': (1,)}[key]
    assert np.isin(cr[le | se], ok).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v68_dormant_ref(D, up, n=20, quiet=60):
    days = list(D.index); C = list(D.c); m = len(C)
    new = [j >= n and (C[j] > max(C[j - n:j]) if up else C[j] < min(C[j - n:j])) for j in range(m)]
    res = np.zeros(m, bool)
    for j in range(n + quiet, m):
        if days[j] - days[j - n - quiet] != pd.Timedelta(days=n + quiet):
            continue
        res[j] = new[j] and not any(new[j - quiet:j])
    return res


def _v68_retest_ref(D, n=30, w=10, tol=0.01):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(n + w, m):
        if days[j] - days[j - n - w] != pd.Timedelta(days=n + w):
            continue
        bs = [b for b in range(j - w, j) if C[b] > max(H[b - n:b])]
        if not bs:
            continue
        b = bs[-1]; lv = max(H[b - n:b])
        hit = [k for k in range(b + 1, j + 1) if Lo[k] <= (1 + tol) * lv and C[k] > lv]
        res[j] = hit[:1] == [j]
    return res


def test_v68_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (48, 53, 56, 57, 59):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for kind, fn, p, ref in (('dh', L68._dormant_v, L68.DP, lambda D: _v68_dormant_ref(D, True)),
                                 ('dl', L68._dormant_v, L68.DP, lambda D: _v68_dormant_ref(D, False)),
                                 ('rt', L68._retest_v, dict(n=30, w=10, tol=0.01), _v68_retest_ref)):
            call = (lambda X: fn(X, p, kind == 'dh')) if kind != 'rt' else (lambda X: fn(X, p))
            v = call(D).values
            np.testing.assert_array_equal(v, ref(D), err_msg=str((seed, kind)))
            hits[kind] = hits.get(kind, 0) + int(v.sum())
            if seed == 56:
                for cut in range(45, len(D), 37):
                    np.testing.assert_array_equal(call(D.iloc[:cut]).values, v[:cut])
    assert all(hits.values()), hits


def _v68_days(rows, start=0):
    idx = pd.date_range('2022-01-01', periods=len(rows), freq='D')[start:]
    h, l, c = zip(*rows)
    return pd.DataFrame(dict(h=h, l=l, c=c, v=1.0), index=idx[:len(rows)] if not start else idx)


def test_v68_retest_synthetic_level_tolerance_and_first_touch():
    base = [(100.0, 98.0, 99.0)] * 40                                   # level L = 100 (highest high)
    p = dict(n=30, w=10, tol=0.01)
    def run(tail):
        return L68._retest_v(_v68_days(base + tail), p).values[40:]
    brk = (106.0, 99.0, 105.0)                                           # day 40 closes above L
    assert run([brk, (107.0, 104.0, 106.0), (103.0, 100.9, 102.0), (103.0, 100.5, 102.0)]).tolist() == [0, 0, 1, 0]
    assert not run([brk, (103.0, 101.01, 102.0)]).any()                  # low 1.0101 L: no touch
    assert run([brk, (103.0, 101.0, 102.0)]).tolist() == [0, 1]           # low exactly 1.01 L: touch
    assert not run([brk, (103.0, 99.0, 100.0)]).any()                     # close == L does not hold
    assert run([brk, (103.0, 99.0, 99.5), (103.0, 100.2, 101.0)]).tolist() == [0, 0, 1]   # failed touch is not a retest
    assert not run([(100.0, 98.0, 100.0), (103.0, 100.2, 101.0)]).any()   # close == L is no breakout
    late = [brk] + [(104.0, 102.0, 103.0)] * 10 + [(103.0, 100.2, 101.0)]
    assert not run(late).any()                                            # breakout 11 days back is outside w=10
    late = [brk] + [(104.0, 102.0, 103.0)] * 9 + [(103.0, 100.2, 101.0)]
    assert run(late)[-1]
    gap = L68._retest_v(_v68_days(base + [brk, (103.0, 100.5, 102.0)]).drop(pd.Timestamp('2022-01-05')), p).values
    assert not gap.any()


def test_v68_dormant_synthetic_quiet_window():
    p = dict(n=20, quiet=60)
    def run(c):
        return L68._dormant_v(_v68_days([(x + 1, x - 1, x) for x in c]), p, True).values
    c = [100.0] * 100 + [101.0]
    assert run(c)[-1]                                                     # first new high after a flat spell
    c2 = [100.0] * 40 + [100.5] + [100.0] * 59 + [101.0]                   # a new 20-day high 60 days back: inside quiet
    assert not run(c2)[-1]
    c3 = [100.0] * 39 + [100.5] + [100.0] * 60 + [101.0]                   # 61 days back: outside quiet
    assert run(c3)[-1]
    c4 = [100.0] * 100 + [100.0]                                          # equal close is not a new high
    assert not run(c4)[-1]


def test_v68_entries_are_hour_zero_and_gaps_block():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0
    for key in sorted(L68.CANDIDATES):
        coin = _V68_COIN[key](); t = pd.to_datetime(coin.t).dt.normalize()
        full = _sig(key, coin, btc)
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))[:20]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            for back in (2, 40):
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired


def test_v68_regime_gate_admits_sideways_only_where_frozen():
    btc = _ms(_mk(2600, '4h', 52)); seen = {}
    for key, seed, fn, ok in (('l68_dormant_high_4h', 44, lambda d: L68._dormant(d, L68.DP, True), (0, 1)),
                              ('s68_dormant_low_4h', 48, lambda d: L68._dormant(d, L68.DP, False), (0, -1)),
                              ('l68_breakout_retest_4h', 42, lambda d: L68._retest(d, dict(n=30, w=10, tol=0.01)), (1,))):
        coin = _ms(_mk(2600, '4h', seed)); d = S.indicators(coin)
        cr = L.coin_regime(d); fl = np.asarray(fn(d))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, fl & np.isin(cr, ok), err_msg=key)
        seen[key] = int((fl & (cr == 0)).sum())
    assert all(seen.values()), seen


import longscout_v69 as L69
L69.register()

_V69_COIN = {'l69_morning_star_4h': lambda: _ms(_mk(2600, '4h', 58)),
             's69_dead_cat_fail_4h': lambda: _ms(_mk(2600, '4h', 54))}


@pytest.mark.parametrize('key', sorted(L69.CANDIDATES))
def test_v69_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V69_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l69': le.sum() and not se.any(), 's69': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v69_mstar_ref(D, n=20):
    days = list(D.index); O, H, Lo, C = list(D.o), list(D.h), list(D.l), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        b2 = O[j - 2] - C[j - 2]
        res[j] = (b2 > 0 and b2 >= 0.6 * (H[j - 2] - Lo[j - 2]) and abs(C[j - 1] - O[j - 1]) <= 0.3 * b2
                  and C[j] > O[j] and C[j] > (O[j - 2] + C[j - 2]) / 2 and min(Lo[j - 2:j + 1]) <= min(Lo[j - n + 1:j + 1]))
    return res


def _v69_dcat_ref(D, n=30, k=0.10):
    days = list(D.index); C = list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        lo = min(C[j - n:j])
        if C[j] >= lo:
            continue
        a = max(i for i in range(j - n, j) if C[i] == lo)
        res[j] = any(C[i] >= (1 + k) * lo for i in range(a + 1, j))
    return res


def test_v69_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (40, 49, 54, 58, 63):
        d = S.indicators(_ms(_mk(2600, '4h', seed)))
        for kind, D, call, ref in (('ms', L17._days(d), lambda X: L69._mstar_v(X, dict(n=20)), _v69_mstar_ref),
                                   ('dc', L27._days_v(d), lambda X: L69._dcat_v(X, dict(n=30, bounce=0.10)), _v69_dcat_ref)):
            v = call(D).values
            np.testing.assert_array_equal(v, ref(D), err_msg=str((seed, kind)))
            hits[kind] = hits.get(kind, 0) + int(v.sum())
            if seed == 54:
                for cut in range(35, len(D), 37):
                    np.testing.assert_array_equal(call(D.iloc[:cut]).values, v[:cut])
    assert all(hits.values()), hits


def test_v69_dead_cat_synthetic_bounce_size_and_latest_low():
    p = dict(n=30, bounce=0.10)
    def run(tail, head=[100.0] * 28):
        c = head + tail
        D = pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
        return L69._dcat_v(D, p).values
    assert run([90.0, 99.01, 95.0, 89.0])[-1]                             # bounce just over +10%, then new low
    assert not run([90.0, 98.9, 95.0, 89.0])[-1]                          # bounce +9.9%: not enough
    assert not run([90.0, 99.01, 95.0, 90.0])[-1]                          # equal close is no new low
    assert not run([90.0, 99.01, 90.0, 89.0])[-1]                          # latest low has no bounce after it
    c = [100.0] * 28 + [90.0, 99.01, 95.0, 89.0]
    D = pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
    assert not L69._dcat_v(D.drop(D.index[5]), p).values[-1]             # missing day inside the window blocks


def test_v69_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); seen = {}; fired = 0
    for key, ok, fl in (('l69_morning_star_4h', (0, 1), lambda d: L27._next_day(L69._mstar_v(L17._days(d), dict(n=20)), L17._days(d), d)),
                        ('s69_dead_cat_fail_4h', (-1,), lambda d: L27._next_day(L69._dcat_v(L27._days_v(d), dict(n=30, bounce=0.10)), L27._days_v(d), d))):
        for seed in (40, {'l': 58, 's': 54}[key[0]]):
            coin = _ms(_mk(2600, '4h', seed)); d = S.indicators(coin); cr = L.coin_regime(d); f = np.asarray(fl(d))
            full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
            np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
            seen[(key, 0)] = seen.get((key, 0), 0) + int((f & (cr == 0)).sum())
            seen[(key, -1)] = seen.get((key, -1), 0) + int((f & (cr == -1)).sum())
            t = pd.to_datetime(coin.t).dt.normalize()
            for i in np.flatnonzero(got)[:10]:
                assert pd.Timestamp(coin.t.iloc[i]).hour == 0
                for back in (2, 15):
                    cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
                    part = _sig(key, cut, btc)
                    k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                    fired += 1
                    assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert all(seen.values()) and fired, seen


def test_v69_morning_star_synthetic_body_rules():
    flat = [(100.0, 101.0, 99.0, 100.0)] * 25                            # o, h, l, c
    def run(tail):
        rows = flat + tail
        o, h, l, c = (list(x) for x in zip(*rows))
        D = pd.DataFrame(dict(o=o, h=h, l=l, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(rows), freq='D'))
        return L69._mstar_v(D, dict(n=20)).values[-1]
    red = (100.0, 100.5, 89.5, 90.0)                                     # body 10 of range 11, new 20-day low
    assert run([red, (89.0, 89.5, 88.0, 89.5), (90.0, 96.0, 89.5, 95.5)])
    assert not run([red, (89.0, 89.5, 88.0, 89.5), (95.5, 96.0, 89.5, 95.5)])   # doji day j (c == o) is not green
    assert not run([red, (89.0, 89.5, 88.0, 89.5), (90.0, 96.0, 89.5, 95.0)])   # close at the midpoint does not count
    assert not run([red, (89.0, 89.5, 88.0, 92.5), (90.0, 96.0, 89.5, 95.5)])   # middle body 3.5 > 30% of 10
    assert not run([(100.0, 108.0, 89.5, 90.0), (89.0, 89.5, 88.0, 89.5), (90.0, 96.0, 89.5, 95.5)])  # body < 60% of range


import longscout_v70 as L70
L70.register()


def _v70_dry_coin(seed):
    d = _mk(2600, '4h', seed); day = np.arange(len(d)) // 6
    d['v'] = d.v * np.where(day % 15 >= 11, 0.2, 1.0)                    # quiet 4-day blocks every 15 days
    return _ms(d)


_V70_COIN = {'r70_month_open_cross_4h': lambda: _ms(_mk(2600, '4h', 44)),
             's70_volume_dryup_near_low_4h': lambda: _v70_dry_coin(64)}


@pytest.mark.parametrize('key', sorted(L70.CANDIDATES))
def test_v70_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V70_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'r70': le.sum() and se.sum(), 's70': se.sum() and not le.any()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v70_mopen_ref(D, up):
    days = list(D.index); O, C = list(D.o), list(D.c); pos = {t: i for i, t in enumerate(days)}
    res = np.zeros(len(C), bool)
    for j, t in enumerate(days):
        first = t.replace(day=1)
        if t == first or first not in pos or pos[first] != j - (t - first).days:
            continue
        mo = O[pos[first]]
        res[j] = (C[j] > mo and C[j - 1] <= mo) if up else (C[j] < mo and C[j - 1] >= mo)
    return res


def _v70_dry_ref(D, base=30, days_=3, q=0.5, near=0.05, n=20):
    days = list(D.index); V, C = list(D.v), list(D.c); m = len(C)
    quiet = [t >= base and V[t] < q * float(np.median(V[t - base:t])) for t in range(m)]
    res = np.zeros(m, bool)
    for j in range(base + days_ - 1, m):
        if days[j] - days[j - base - days_ + 1] != pd.Timedelta(days=base + days_ - 1):
            continue
        res[j] = all(quiet[j - days_ + 1:j + 1]) and C[j] <= (1 + near) * min(C[j - n + 1:j + 1])
    return res


def test_v70_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (42, 44, 51, 63, 67):
        dm, dd = S.indicators(_ms(_mk(2600, '4h', seed))), S.indicators(_v70_dry_coin(seed))
        cases = [('mu', L17._days(dm), lambda X: L70._mopen_v(X, True), lambda X: _v70_mopen_ref(X, True)),
                 ('md', L17._days(dm), lambda X: L70._mopen_v(X, False), lambda X: _v70_mopen_ref(X, False)),
                 ('dry', L27._days_v(dd), lambda X: L70._dry_v(X, L70.CANDIDATES['s70_volume_dryup_near_low_4h']['params']),
                  _v70_dry_ref)]
        for kind, D, call, ref in cases:
            v = call(D).values
            np.testing.assert_array_equal(v, ref(D), err_msg=str((seed, kind)))
            hits[kind] = hits.get(kind, 0) + int(v.sum())
            if seed == 63:
                for cut in range(35, len(D), 37):
                    np.testing.assert_array_equal(call(D.iloc[:cut]).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(call(gap).values, ref(gap), err_msg=str(('gap', kind)))
    assert all(hits.values()), hits


def test_v70_month_open_synthetic_boundaries():
    idx = pd.date_range('2022-01-29', '2022-02-06', freq='D')              # Jan 29..31, Feb 1..6
    o = [50.0, 50.0, 50.0, 100.0, 99.0, 99.0, 99.0, 101.0, 101.0]
    c = [50.0, 50.0, 101.0, 99.0, 99.0, 100.0, 101.0, 101.0, 99.5]
    D = pd.DataFrame(dict(o=o, h=c, l=c, c=c, v=1.0), index=idx)
    up, dn = L70._mopen_v(D, True).values, L70._mopen_v(D, False).values
    assert up.tolist() == [0, 0, 0, 0, 0, 0, 1, 0, 0]                       # Feb 1 itself never fires; 100 == MO is not above
    assert dn.tolist() == [0, 0, 0, 0, 0, 0, 0, 0, 1]
    assert not L70._mopen_v(D.drop(pd.Timestamp('2022-02-02')), True).any()   # a missing day since the 1st blocks
    assert not L70._mopen_v(D.drop(pd.Timestamp('2022-02-01')), True).any()   # no first-of-month row: no anchor


def test_v70_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0; seen = {}
    for key in sorted(L70.CANDIDATES):
        coin = _V70_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        if key.startswith('r70'):
            D = L17._days(d); fu = np.asarray(L27._next_day(L70._mopen_v(D, True), D, d))
            fd = np.asarray(L27._next_day(L70._mopen_v(D, False), D, d))
        else:
            D = L27._days_v(d); fu = np.zeros(len(d), bool)
            fd = np.asarray(L27._next_day(L70._dry_v(D, L70.CANDIDATES[key]['params']), D, d))
        full = _sig(key, coin, btc)
        np.testing.assert_array_equal(np.asarray(full['le']), fu & (cr == 1))
        np.testing.assert_array_equal(np.asarray(full['se']), fd & (cr == -1))
        seen[key] = int(((fu | fd) & (cr == 0)).sum())
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))[:20]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            back = 1 if key.startswith('r70') else 15
            if key.startswith('r70') and pd.Timestamp(coin.t.iloc[i]).day <= 2:
                continue
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
            part = _sig(key, cut, btc)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            fired += 1
            assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), (key, back)
    assert fired and all(seen.values()), seen


def test_v70_dryup_synthetic_volume_and_near_low_edges():
    p = L70.CANDIDATES['s70_volume_dryup_near_low_4h']['params']
    def run(vtail=(49.0, 49.0, 49.0), cj=None, c20=100.0):
        c = [110.0] * 15 + [c20] + [100.0] * 19 + [(1 + 0.05) * 100.0 if cj is None else cj]
        v = [100.0] * 33 + list(vtail)
        D = pd.DataFrame(dict(h=c, l=c, c=c, v=v), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
        return L70._dry_v(D, p).values[-1]
    assert run()                                                           # close exactly 5% above the 20-day low counts
    assert not run(cj=105.01)
    assert not run(vtail=(49.0, 50.0, 49.0))                               # exactly half the median is not quiet
    assert not run(vtail=(49.0, 49.0, 50.0))
    assert run(c20=90.0)                                                   # a lower close 20 days back is outside j-19..j


import longscout_v71 as L71
L71.register()

_V71_COIN = {'l71_rs_leads_high_4h': lambda: _ms(_mk(2600, '4h', 44)),
             's71_rs_leads_low_4h': lambda: _ms(_mk(2600, '4h', 42))}


@pytest.mark.parametrize('key', sorted(L71.CANDIDATES))
def test_v71_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V71_COIN[key](), _ms(_mk(2600, '4h', 53))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l71': le.sum() and not se.any(), 's71': se.sum() and not le.any()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v71_ref(D, J, up, n=50):
    days = list(D.index); C, B = list(J.c), list(J.b); m = len(C)
    R = [C[i] / B[i] for i in range(m)]
    raw = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n) or any(np.isnan(B[j - n:j + 1])):
            continue
        if up:
            raw[j] = R[j] > max(R[j - n:j]) and C[j] < max(C[j - n:j])
        else:
            raw[j] = R[j] < min(R[j - n:j]) and C[j] > min(C[j - n:j])
    return raw & ~np.r_[False, raw[:-1]]


def test_v71_flags_match_an_independent_loop_prefixes_and_btc_gaps():
    hits = {}
    for seed, bs in ((40, 52), (42, 53), (44, 53), (45, 53), (41, 52)):
        btc = _ms(_mk(2600, '4h', bs))
        for drop in (None, 1500):
            b = btc if drop is None else btc.drop(range(drop, drop + 6)).reset_index(drop=True)   # remove one BTC day
            L.set_btc(b)
            d = S.indicators(_ms(_mk(2600, '4h', seed))); D, J = L33._joint(d)
            for up in (True, False):
                v = L71._lead_v(D, J, dict(n=50), up).values
                np.testing.assert_array_equal(v, _v71_ref(D, J, up), err_msg=str((seed, drop, up)))
                hits[(up, drop)] = hits.get((up, drop), 0) + int(v.sum())
                if seed == 44 and drop is None:
                    for cut in range(55, len(D), 37):
                        np.testing.assert_array_equal(L71._lead_v(D.iloc[:cut], J.iloc[:cut], dict(n=50), up).values, v[:cut])
    assert all(hits.values()), hits


def test_v71_first_day_and_strictness_synthetic():
    idx = pd.date_range('2022-01-01', periods=56, freq='D')
    c = np.full(56, 100.0); c[3] = 120.0                                  # coin's 50-day high (day 3) stays above price
    def run(tail, cc=c):
        b = np.full(56, 100.0); b[3] = 130.0; b[51:] = tail                # keeps the ratio of day 3 low
        D = pd.DataFrame(dict(h=cc, l=cc, c=cc, v=1.0), index=idx); J = pd.DataFrame(dict(c=cc, b=b), index=idx)
        return L71._lead_v(D, J, dict(n=50), True).values[51:].tolist()
    assert run([90.0, 90.0, 89.0, 100.0, 100.0]) == [True, False, True, False, False]   # equal ratio is no new high
    assert run([90.0, 89.0, 88.0, 100.0, 100.0]) == [True, False, False, False, False]  # only the first day of a run
    c2 = c.copy(); c2[3] = 100.0
    assert run([90.0, 90.0, 89.0, 100.0, 100.0], c2) == [False] * 5                   # price at its own high: no lead


def test_v71_regime_gate_and_hour_zero_with_coin_gaps():
    btc = _ms(_mk(2600, '4h', 53)); fired = 0; seen = {}
    for key in sorted(L71.CANDIDATES):
        up = key.startswith('l71')
        for seed in (40, 41, 42, 44, 45):
            coin = _ms(_mk(2600, '4h', seed)); d = S.indicators(coin); cr = L.coin_regime(d)
            full = _sig(key, coin, btc)
            f = np.asarray(L71._lead(d, dict(n=50), up))
            np.testing.assert_array_equal(np.asarray(full['le' if up else 'se']), f & (cr == (1 if up else -1)))
            seen[key] = seen.get(key, 0) + int((f & (cr == 0)).sum())
            t = pd.to_datetime(coin.t).dt.normalize()
            for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))[:5]:
                assert pd.Timestamp(coin.t.iloc[i]).hour == 0
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=30)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired and all(seen.values()), seen


def test_v71_short_side_strictness_synthetic():
    idx = pd.date_range('2022-01-01', periods=56, freq='D')
    c = np.full(56, 100.0); c[3] = 80.0                                   # coin's 50-day low (day 3) stays below price
    def run(tail, cc=c):
        b = np.full(56, 100.0); b[3] = 60.0; b[51:] = tail                # keeps the ratio of day 3 high
        D = pd.DataFrame(dict(h=cc, l=cc, c=cc, v=1.0), index=idx); J = pd.DataFrame(dict(c=cc, b=b), index=idx)
        return L71._lead_v(D, J, dict(n=50), False).values[51:].tolist()
    assert run([110.0, 110.0, 111.0, 100.0, 100.0]) == [True, False, True, False, False]
    c2 = c.copy(); c2[3] = 100.0
    assert run([110.0, 110.0, 111.0, 100.0, 100.0], c2) == [False] * 5    # price at its own low: no lead


import longscout_v72 as L72
L72.register()

_V72_COIN = {'l72_follow_through_4h': lambda: _ms(_mk(2600, '4h', 51)),
             's72_distribution_4h': lambda: _ms(_mk(2600, '4h', 44))}
_V72_FN = {'l72_follow_through_4h': (lambda D: L72._ftd_v(D, L72.CANDIDATES['l72_follow_through_4h']['params']), (0, 1)),
           's72_distribution_4h': (lambda D: L72._dist_v(D, L72.CANDIDATES['s72_distribution_4h']['params']), (-1,))}


@pytest.mark.parametrize('key', sorted(L72.CANDIDATES))
def test_v72_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V72_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l72': le.sum() and not se.any(), 's72': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v72_ftd_ref(D, n=30, a0=4, a1=10, up=0.03):
    days = list(D.index); C, V = list(D.c), list(D.v); m = len(C)
    hit = [i > 0 and C[i] >= (1 + up) * C[i - 1] and V[i] > V[i - 1] for i in range(m)]
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n) or not hit[j]:
            continue
        lo = min(C[j - n:j + 1]); a = max(i for i in range(j - n, j + 1) if C[i] == lo)
        res[j] = j - a1 <= a <= j - a0 and not any(hit[a + 1:j])
    return res


def _v72_dist_ref(D, n=25, k=6, dn=0.01):
    days = list(D.index); C, V = list(D.c), list(D.v); m = len(C)
    dd = [i > 0 and C[i] <= (1 - dn) * C[i - 1] and V[i] > V[i - 1] for i in range(m)]
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        res[j] = dd[j] and sum(dd[j - n + 1:j + 1]) >= k and sum(dd[j - n:j]) < k
    return res


def test_v72_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (43, 44, 47, 51, 53):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for key, ref in (('l72_follow_through_4h', _v72_ftd_ref), ('s72_distribution_4h', _v72_dist_ref)):
            call = _V72_FN[key][0]
            v = call(D).values
            np.testing.assert_array_equal(v, ref(D), err_msg=str((seed, key)))
            hits[key] = hits.get(key, 0) + int(v.sum())
            if seed == 51:
                for cut in range(35, len(D), 37):
                    np.testing.assert_array_equal(call(D.iloc[:cut]).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(call(gap).values, ref(gap), err_msg=str(('gap', key)))
    assert all(hits.values()), hits


def _v72_days(c, v):
    return pd.DataFrame(dict(h=c, l=c, c=c, v=v), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))


def test_v72_follow_through_synthetic_age_size_and_first():
    p = L72.CANDIDATES['l72_follow_through_4h']['params']
    def run(tail, vt=None):
        c = [100.0] * 26 + [80.0] + tail
        v = [10.0] * 27 + (vt or [10.0 + i for i in range(1, len(tail) + 1)])
        return L72._ftd_v(_v72_days(c, v), p).values[27:].tolist()
    assert run([81.0, 81.0, 81.0, 81.0 * 1.03]) == [0, 0, 0, 1]                       # 4 days after the low, +3% exactly
    assert run([81.0, 81.0, 81.0, 81.0 * 1.0299]) == [0, 0, 0, 0]
    assert run([81.0, 81.0, 81.0, 83.43], [11.0, 12.0, 13.0, 13.0]) == [0, 0, 0, 0]  # volume not above prior day
    assert run([81.0, 81.0, 83.43]) == [0, 0, 0]                                      # 3 days after the low: too soon
    assert run([81.0, 83.43, 81.0, 81.0, 83.43])[-1] == 0                            # an earlier follow-through came first
    assert run([81.0] * 9 + [83.43])[-1] == 1                                         # 10 days after the low
    assert run([81.0] * 10 + [83.43])[-1] == 0                                        # 11 days: too late


def test_v72_distribution_synthetic_count_and_first():
    p = L72.CANDIDATES['s72_distribution_4h']['params']
    c, v = [100.0] * 30, [10.0] * 30
    for i in (24, 26, 28, 30, 32):
        c += [c[-1] * 0.99, c[-1] * 0.99]; v += [11.0 + i, 5.0]                    # a -1% day on rising volume, then flat
    f = L72._dist_v(_v72_days(c, v), p).values
    assert f.sum() == 0                                                               # five distribution days only
    c2, v2 = c + [c[-1] * 0.99], v + [100.0]
    assert L72._dist_v(_v72_days(c2, v2), p).values[-1]                               # sixth one fires
    c3, v3 = c + [c[-1] * 0.9901], v + [100.0]
    assert not L72._dist_v(_v72_days(c3, v3), p).values[-1]                           # -0.99% is not a distribution day
    c4, v4 = c2 + [c2[-1] * 0.99], v2 + [200.0]
    assert not L72._dist_v(_v72_days(c4, v4), p).values[-1]                           # seventh is not a first day


def test_v72_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0; seen = {}
    for key in sorted(L72.CANDIDATES):
        fn, ok = _V72_FN[key]
        coin = _V72_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d); D = L27._days_v(d)
        f = np.asarray(L27._next_day(fn(D), D, d))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        seen[key] = (int((f & (cr == 0)).sum()), int((f & (cr == 1)).sum()))
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:20]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=20)].reset_index(drop=True)
            part = _sig(key, cut, btc)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            fired += 1
            assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired and all(all(x) for x in seen.values()), seen


def test_v72_tied_low_uses_latest_and_equal_volume_is_not_distribution():
    p = L72.CANDIDATES['l72_follow_through_4h']['params']
    c = [100.0] * 20 + [80.0] + [90.0] * 10 + [80.0, 81.0, 81.0, 81.0, 83.43]           # tied lows on days 20 and 31
    v = [10.0] * 32 + [11.0, 12.0, 13.0, 14.0]
    assert L72._ftd_v(_v72_days(c, v), p).values[-1]                                  # age counted from the latest low (4)
    q = L72.CANDIDATES['s72_distribution_4h']['params']
    c, v = [100.0] * 30, [10.0] * 30
    for i in range(6):
        c += [c[-1] * 0.99, c[-1] * 0.99]; v += [20.0 + i, 5.0]
    assert L72._dist_v(_v72_days(c, v), q).values[-2]
    v[-2] = v[-3]                                                                     # sixth day on equal volume
    assert not L72._dist_v(_v72_days(c, v), q).values[-2]


import longscout_v73 as L73
L73.register()

_V73_COIN = {'l73_obv_bull_div_4h': lambda: _ms(_mk(2600, '4h', 43)),
             's73_obv_bear_div_4h': lambda: _ms(_mk(2600, '4h', 43))}


@pytest.mark.parametrize('key', sorted(L73.CANDIDATES))
def test_v73_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V73_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l73': le.sum() and not se.any(), 's73': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v73_ref(D, bull, n=20):
    days = list(D.index); C, V = list(D.c), list(D.v); m = len(C)
    obv = [0.0]
    for i in range(1, m):
        obv.append(obv[-1] + (V[i] if C[i] > C[i - 1] else -V[i] if C[i] < C[i - 1] else 0.0))
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        ext = min(C[j - n:j]) if bull else max(C[j - n:j])
        if not (C[j] < ext if bull else C[j] > ext):
            continue
        a = max(i for i in range(j - n, j) if C[i] == ext)
        res[j] = obv[j] > obv[a] if bull else obv[j] < obv[a]
    return res


def test_v73_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (41, 43, 46, 47, 49):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for bull in (True, False):
            v = L73._div_v(D, dict(n=20), bull).values
            np.testing.assert_array_equal(v, _v73_ref(D, bull), err_msg=str((seed, bull)))
            hits[bull] = hits.get(bull, 0) + int(v.sum())
            if seed == 43:
                for cut in range(25, len(D), 37):
                    np.testing.assert_array_equal(L73._div_v(D.iloc[:cut], dict(n=20), bull).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L73._div_v(gap, dict(n=20), bull).values, _v73_ref(gap, bull))
    assert all(hits.values()), hits


def test_v73_synthetic_divergence_edges():
    def run(c, v, bull=True):
        D = pd.DataFrame(dict(h=c, l=c, c=c, v=v), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
        return L73._div_v(D, dict(n=20), bull).values[-1]
    base = [100.0] * 15
    # lower low on day 24 after a low on day 18; OBV between: day 18 -10, day 19..23 up +5 each, day 24 down -v24
    c = base + [100.0, 100.0, 100.0, 90.0, 91.0, 92.0, 93.0, 94.0, 95.0, 89.0]
    v = [1.0] * 18 + [10.0, 5.0, 5.0, 5.0, 5.0, 5.0]
    assert run(c, v + [24.0])                                             # OBV_j = OBV_a + 25 - 24 > OBV_a
    assert not run(c, v + [25.0])                                         # equal OBV is no divergence
    assert not run(c[:-1] + [90.0], v + [1.0])                            # equal close is no lower low
    c2 = base + [100.0, 100.0, 100.0, 90.0, 95.0, 95.0, 90.0, 95.0, 95.0, 89.0]   # tied lows on days 18 and 21
    v2 = [1.0] * 18 + [10.0, 5.0, 5.0, 20.0, 5.0, 5.0]
    assert run(c2, v2 + [4.0]) and not run(c2, v2 + [5.0])               # OBV compared with the latest low (day 21)
    cs = [200.0 - x for x in c]                                           # bearish mirror
    assert run(cs, v + [24.0], False) and not run(cs, v + [25.0], False)
    assert not run(cs[:-1] + [110.0], v + [1.0], False)                   # equal close is no higher high


def test_v73_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0; seen = {}
    for key in sorted(L73.CANDIDATES):
        bull = key.startswith('l73'); ok = (0, 1) if bull else (0, -1)
        coin = _V73_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L73._div(d, dict(n=20), bull))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        seen[key] = (int((f & (cr == 0)).sum()), int((f & (cr == ok[1])).sum()), int((f & (cr == -ok[1])).sum()))
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:20]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=12)].reset_index(drop=True)
            part = _sig(key, cut, btc)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            fired += 1
            assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired and all(all(x) for x in seen.values()), seen


import longscout_v74 as L74
L74.register()

_V74_COIN = {'l74_fractal_break_4h': lambda: _ms(_mk(2600, '4h', 46)),
             'r74_prior_month_break_4h': lambda: _ms(_mk(2600, '4h', 40))}


@pytest.mark.parametrize('key', sorted(L74.CANDIDATES))
def test_v74_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V74_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l74': le.sum() and not se.any(), 'r74': le.sum() and se.sum()}[key[:3]]
    cr = L.coin_regime(S.indicators(coin))
    assert (cr[le] == 1).all() and (cr[se] == -1).all()
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v74_frac_ref(D, n=20):
    days = list(D.index); H, C = list(D.h), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(n + 2, m):
        if days[j] - days[j - n - 2] != pd.Timedelta(days=n + 2):
            continue
        fr = [k for k in range(j - n, j - 2) if all(H[k] > H[i] for i in (k - 2, k - 1, k + 1, k + 2))]
        if fr:
            lv = H[fr[-1]]
            res[j] = C[j] > lv and C[j - 1] <= lv
    return res


def _v74_pm_ref(D, up):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); have = set(days)
    res = np.zeros(len(C), bool)
    for j, t in enumerate(days):
        first = pd.Timestamp(t.year, t.month, 1)
        prev = pd.Timestamp(t.year - (t.month == 1), 12 if t.month == 1 else t.month - 1, 1)
        need = pd.date_range(prev, t, freq='D')
        if not all(x in have for x in need):
            continue
        pm = [i for i, x in enumerate(days) if prev <= x < first]
        cm = [i for i, x in enumerate(days) if first <= x < t]
        if up:
            lv = max(H[i] for i in pm); res[j] = C[j] > lv and not any(C[i] > lv for i in cm)
        else:
            lv = min(Lo[i] for i in pm); res[j] = C[j] < lv and not any(C[i] < lv for i in cm)
    return res


def test_v74_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (40, 44, 46, 49, 51):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for kind, call, ref in (('fr', lambda X: L74._frac_v(X, dict(n=20)), _v74_frac_ref),
                                ('pu', lambda X: L74._pm_v(X, True), lambda X: _v74_pm_ref(X, True)),
                                ('pd', lambda X: L74._pm_v(X, False), lambda X: _v74_pm_ref(X, False))):
            v = call(D).values
            np.testing.assert_array_equal(v, ref(D), err_msg=str((seed, kind)))
            hits[kind] = hits.get(kind, 0) + int(v.sum())
            if seed == 46:
                for cut in range(30, len(D), 37):
                    np.testing.assert_array_equal(call(D.iloc[:cut]).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(call(gap).values, ref(gap), err_msg=str(('gap', kind)))
    assert all(hits.values()), hits


def test_v74_synthetic_fractal_and_month_edges():
    def days(h, c, start='2022-01-01'):
        return pd.DataFrame(dict(h=h, l=[x - 5 for x in c], c=c, v=1.0), index=pd.date_range(start, periods=len(c), freq='D'))
    h = [100.0] * 23; c = [99.0] * 23
    h[18] = 110.0                                                          # fractal on day 18, known from day 20
    f = lambda hh, cc: L74._frac_v(days(hh, cc), dict(n=20)).values
    assert f(h + [100.0], c + [111.0])[-1]                                 # day 23 (k = j-5) breaks it
    assert not f(h + [100.0], c + [110.0])[-1]                             # close equal to the fractal high
    h2 = list(h); h2[19] = 110.0                                           # equal neighbour high: no fractal
    assert not f(h2 + [100.0], c + [111.0])[-1]
    h3 = [100.0] * 21; c3 = [99.0] * 21; h3[18] = 110.0
    assert not f(h3, c3[:-1] + [111.0])[-1]                                # k = j-2 is not yet confirmed
    c4 = list(c); c4[22] = 110.5
    assert not f(h + [100.0], c4 + [111.0])[-1]                            # day j-1 already closed above
    # month: Jan 1..Feb 5, Jan high 100, Feb closes 99, 99, 101, 102, 101
    hj = [100.0] * 31 + [99.0, 99.0, 101.0, 102.0, 101.0]; cj = [95.0] * 31 + [99.0, 99.0, 101.0, 102.0, 101.0]
    up = L74._pm_v(days(hj, cj), True).values
    assert up[31:].tolist() == [0, 0, 1, 0, 0]                              # only the first close above this month
    assert not L74._pm_v(days(hj, cj).drop(pd.Timestamp('2022-01-10')), True).any()   # incomplete prior month
    cj2 = cj[:33] + [100.0] + cj[34:]
    assert L74._pm_v(days(hj, cj2), True).values[31:].tolist() == [0, 0, 0, 1, 0]   # close equal to the high is not above


def test_v74_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0; seen = {}
    for key in sorted(L74.CANDIDATES):
        coin = _V74_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d); D = L27._days_v(d)
        if key.startswith('l74'):
            fu = np.asarray(L27._next_day(L74._frac_v(D, dict(n=20)), D, d)); fd = np.zeros(len(d), bool)
        else:
            fu = np.asarray(L27._next_day(L74._pm_v(D, True), D, d)); fd = np.asarray(L27._next_day(L74._pm_v(D, False), D, d))
        full = _sig(key, coin, btc)
        np.testing.assert_array_equal(np.asarray(full['le']), fu & (cr == 1))
        np.testing.assert_array_equal(np.asarray(full['se']), fd & (cr == -1))
        seen[key] = int(((fu | fd) & (cr == 0)).sum())
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(np.asarray(full['le']) | np.asarray(full['se']))[:20]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            back = 21 if key.startswith('l74') else pd.Timestamp(coin.t.iloc[i]).day + 3
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=back)].reset_index(drop=True)
            part = _sig(key, cut, btc)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            fired += 1
            assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired and all(seen.values()), seen


def test_v74_fractal_window_needs_days_back_to_k_minus_two():
    h = [100.0] * 24; c = [99.0] * 24; h[3] = 110.0; c[23] = 111.0          # fractal at k = j-20
    D = pd.DataFrame(dict(h=h, l=[95.0] * 24, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=24, freq='D'))
    assert L74._frac_v(D, dict(n=20)).values[-1]
    assert not L74._frac_v(D.drop(D.index[1]), dict(n=20)).values[-1]       # day j-22 (k-2) missing blocks


import longscout_v75 as L75
L75.register()

_V75_COIN = {'l75_ascending_triangle_4h': lambda: _ms(_mk(2600, '4h', 46)),
             's75_descending_triangle_4h': lambda: _ms(_mk(2600, '4h', 44))}


@pytest.mark.parametrize('key', sorted(L75.CANDIDATES))
def test_v75_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V75_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l75': le.sum() and not se.any(), 's75': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v75_ref(D, asc, n=30, tol=0.02, gap=5, rec=10, slope=0.05):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        if asc:
            T = max(H[j - n:j]); t = [i for i in range(j - n, j) if H[i] >= (1 - tol) * T]
            ok = min(Lo[j - rec:j]) >= (1 + slope) * min(Lo[j - n:j - n + rec]) and C[j] > T
        else:
            T = min(Lo[j - n:j]); t = [i for i in range(j - n, j) if Lo[i] <= (1 + tol) * T]
            ok = max(H[j - rec:j]) <= (1 - slope) * max(H[j - n:j - n + rec]) and C[j] < T
        res[j] = ok and t[-1] - t[0] >= gap
    return res


def test_v75_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (40, 42, 44, 46, 48):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for asc in (True, False):
            v = L75._tri_v(D, L75.TP, asc).values
            np.testing.assert_array_equal(v, _v75_ref(D, asc), err_msg=str((seed, asc)))
            hits[asc] = hits.get(asc, 0) + int(v.sum())
            if seed == 46:
                for cut in range(35, len(D), 37):
                    np.testing.assert_array_equal(L75._tri_v(D.iloc[:cut], L75.TP, asc).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L75._tri_v(gap, L75.TP, asc).values, _v75_ref(gap, asc))
    assert all(hits.values()), hits


def test_v75_synthetic_triangle_edges():
    def run(h, l, cj, asc=True):
        c = [(a + b) / 2 for a, b in zip(h, l)] + [cj]
        hh, ll = h + [max(cj, h[-1])], l + [min(cj, l[-1])]
        D = pd.DataFrame(dict(h=hh, l=ll, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
        return L75._tri_v(D, L75.TP, asc).values[-1]
    h = [95.0] * 30; h[2] = 100.0; h[7] = 98.0                                # touches 5 days apart (98 = 0.98 T)
    l = [80.0] * 10 + [82.0] * 10 + [84.0] * 10                               # recent lows 84 = 1.05 x 80
    assert run(h, l, 100.5)
    assert not run(h, l, 100.0)                                               # close equal to T
    h2 = list(h); h2[7] = 97.99
    assert not run(h2, l, 100.5)                                              # second touch below 0.98 T
    h3 = list(h); h3[7] = 95.0; h3[6] = 98.0
    assert not run(h3, l, 100.5)                                              # touches only 4 days apart
    l2 = list(l); l2[25] = 83.99
    assert not run(h, l2, 100.5)                                              # lows rose less than 5%
    hd = [120.0] * 10 + [116.0] * 10 + [114.0] * 10                           # 114 = 0.95 x 120
    ld = [105.0] * 30; ld[2] = 100.0; ld[7] = 102.0                           # 102 = 1.02 B
    assert run(hd, ld, 99.5, False) and not run(hd, ld, 100.0, False)
    hd2 = list(hd); hd2[25] = 114.01
    assert not run(hd2, ld, 99.5, False)
    hd3 = [120.0] + [115.0] * 9 + hd[10:]                                       # the early high sits on day j-30
    assert run(hd3, ld, 99.5, False)


def test_v75_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0; seen = {}
    for key, seeds in (('l75_ascending_triangle_4h', (46, 42)), ('s75_descending_triangle_4h', (44,))):
        asc = key.startswith('l75'); ok = (0, 1) if asc else (0, -1)
        for seed in seeds:
            coin = _ms(_mk(2600, '4h', seed)); d = S.indicators(coin); cr = L.coin_regime(d)
            f = np.asarray(L75._tri(d, L75.TP, asc))
            full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
            np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
            for r in (0, 1, -1):
                seen[(key, r)] = seen.get((key, r), 0) + int((f & (cr == r)).sum())
            t = pd.to_datetime(coin.t).dt.normalize()
            for i in np.flatnonzero(got)[:20]:
                assert pd.Timestamp(coin.t.iloc[i]).hour == 0
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=25)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired and all(seen.values()), seen


import longscout_v76 as L76
L76.register()

_V76_COIN = {'l76_downtrend_line_break_4h': lambda: _ms(_mk(2600, '4h', 43)),
             's76_uptrend_line_break_4h': lambda: _ms(_mk(2600, '4h', 43))}


@pytest.mark.parametrize('key', sorted(L76.CANDIDATES))
def test_v76_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V76_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l76': le.sum() and not se.any(), 's76': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v76_ref(D, down, n=60, mid=20, last=3):
    days = list(D.index); X = list(D.h if down else D.l); C = list(D.c); m = len(C)
    res = np.zeros(m, bool)
    pick = max if down else min
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        a = list(range(j - n, j - mid)); b = list(range(j - mid, j - last + 1))
        p1 = next(i for i in a if X[i] == pick(X[i] for i in a)); p2 = next(i for i in b if X[i] == pick(X[i] for i in b))
        if not ((X[p2] < X[p1]) if down else (X[p2] > X[p1])):
            continue
        line = lambda t: X[p1] + (X[p2] - X[p1]) * (t - p1) / (p2 - p1)
        res[j] = (C[j] > line(j) and C[j - 1] <= line(j - 1)) if down else (C[j] < line(j) and C[j - 1] >= line(j - 1))
    return res


def test_v76_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (41, 43, 46, 48, 49):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for down in (True, False):
            v = L76._line_v(D, L76.LP, down).values
            r = _v76_ref(D, down)
            np.testing.assert_array_equal(v, r, err_msg=str((seed, down)))
            hits[down] = hits.get(down, 0) + int(v.sum())
            if seed == 43:
                for cut in range(65, len(D), 37):
                    np.testing.assert_array_equal(L76._line_v(D.iloc[:cut], L76.LP, down).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L76._line_v(gap, L76.LP, down).values, _v76_ref(gap, down))
    assert all(hits.values()), hits


def test_v76_synthetic_trendline_edges():
    def run(x, cj, cprev=None, down=True):
        x = list(x); c = [v - 10 for v in x] if down else [v + 10 for v in x]
        if cprev is not None:
            c[-1] = cprev
        c = c + [cj]
        if down:
            h = x + [max(cj, x[-1])]; lo = [v - 30 for v in h]
        else:
            lo = x + [min(cj, x[-1])]; h = [v + 30 for v in lo]
        D = pd.DataFrame(dict(h=h, l=lo, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
        return L76._line_v(D, L76.LP, down).values[-1]
    h = [100.0] * 60; h[10] = 150.0; h[50] = 130.0                            # p1 = 10, p2 = 50 (j = 60): slope -0.5
    assert run(h, 125.01)                                                     # line(60) = 125, line(59) = 125.5
    assert not run(h, 125.0)                                                  # close equal to the line
    assert not run(h, 126.0, cprev=125.6)                                     # day j-1 already above its line value
    assert run(h, 126.0, cprev=125.5)                                         # day j-1 exactly on the line counts as below
    h2 = list(h); h2[50] = 150.0
    assert not run(h2, 160.0)                                                 # p2 not lower than p1: no downtrend line
    h3 = list(h); h3[58] = 140.0
    assert run(h3, 125.01)                                                    # a high at j-2 is outside the p2 window
    h4 = list(h); h4[57] = 140.0; h4[50] = 100.0                              # p2 = 57 = j-3: slope -10/47
    assert run(h4, 140.0 - 10.0 * 3 / 47 + 0.01, cprev=120.0)
    h5 = list(h); h5[40] = 150.0                                              # p1 window ends at j-21 = 39: day 40 is p2's
    assert not run(h5, 125.01)
    h6 = list(h); h6[40] = 160.0
    assert not run(h6, 125.01)                                                # day j-20 belongs to the p2 window only
    h7 = list(h); h7[20] = 150.0
    assert not run(h7, 124.0) and run(h7, 125.01)                             # tied p1 highs: the earliest day anchors
    lo2 = [200.0 - v for v in h2]
    assert not run(lo2, 40.0, down=False)                                     # mirror: p2 not higher than p1
    lo = [200.0 - v for v in h]                                               # mirror: line(60) = 75
    assert run(lo, 74.99, down=False) and not run(lo, 75.0, down=False)


def test_v76_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0; seen = {}
    for key in sorted(L76.CANDIDATES):
        down = key.startswith('l76'); ok = (0, 1) if down else (0, -1)
        coin = _V76_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L76._line(d, L76.LP, down))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        seen[key] = tuple(int((f & (cr == r)).sum()) for r in (0, 1, -1))
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:20]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=45)].reset_index(drop=True)
            part = _sig(key, cut, btc)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            fired += 1
            assert not (np.asarray(part['le'])[k0] or np.asarray(part['se'])[k0]), key
    assert fired and all(all(x) for x in seen.values()), seen


import longscout_v77 as L77
L77.register()

_V77_COIN = {'s77_bear_flag_4h': lambda: _ms(_mk(2600, '4h', 41)),
             's77_failed_bull_flag_4h': lambda: _ms(_mk(2600, '4h', 44))}
_V77_FN = {'s77_bear_flag_4h': (L77._bear_v, L77._bear, dict(pole=0.20, flag=0.12), (-1,)),
           's77_failed_bull_flag_4h': (L77._fail_v, L77._fail, dict(pole=0.20), (0, -1))}


@pytest.mark.parametrize('key', sorted(L77.CANDIDATES))
def test_v77_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V77_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert se.sum() and not le.any()
    ent = np.flatnonzero(se)
    bars = sorted(set(ent[ent >= 300][:60]) | set(ent[ent >= 300][:60] - 1) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v77_bear_ref(D, pole=0.20, flag=0.12):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(20, m):
        if days[j] - days[j - 20] != pd.Timedelta(days=20):
            continue
        cand = list(range(j - 10, j - 3)); q = min(cand, key=lambda i: (C[i], i))
        ok = C[q] <= (1 - pole) * max(H[q - 5:q + 1])
        ok = ok and all(Lo[i] >= Lo[q] and H[i] <= (1 + flag) * C[q] for i in range(q + 1, j))
        res[j] = ok and C[j] < Lo[q] and C[j - 1] >= Lo[q]
    return res


def _v77_fail_ref(D, pole=0.20):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(20, m):
        if days[j] - days[j - 20] != pd.Timedelta(days=20):
            continue
        cand = list(range(j - 10, j - 3)); q = min(cand, key=lambda i: (-C[i], i))
        if not (C[q] >= (1 + pole) * min(Lo[q - 5:q + 1]) and all(H[i] <= H[q] for i in range(q + 1, j)) and j - 1 > q + 1):
            continue
        res[j] = C[j] < min(Lo[q + 1:j]) and not C[j - 1] < min(Lo[q + 1:j - 1])
    return res


def test_v77_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (40, 41, 44, 49, 51):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for key, ref in (('s77_bear_flag_4h', _v77_bear_ref), ('s77_failed_bull_flag_4h', _v77_fail_ref)):
            fv, _, p, _ = _V77_FN[key]
            v = fv(D, p).values
            np.testing.assert_array_equal(v, ref(D), err_msg=str((seed, key)))
            hits[key] = hits.get(key, 0) + int(v.sum())
            if seed == 44:
                for cut in range(25, len(D), 37):
                    np.testing.assert_array_equal(fv(D.iloc[:cut], p).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(fv(gap, p).values, ref(gap), err_msg=str(('gap', key)))
    assert all(hits.values()), hits


def test_v77_synthetic_flag_edges():
    def mk(rows):
        h, l, c = (list(x) for x in zip(*rows))
        return pd.DataFrame(dict(h=h, l=l, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(rows), freq='D'))
    pb = dict(pole=0.20, flag=0.12)
    # bear: high 100 on day 9, pole bottom day 12 close 80 (low 79), flag days 13..18 in [79, 89.6], break on day 19 (j = 19 + 1 pad)
    pre = [(100.0, 95.0, 97.0)] * 10 + [(98.0, 88.0, 90.0), (90.0, 82.0, 85.0), (85.0, 79.0, 80.0)]
    flag = [(84.0, 80.0, 82.0)] * 6
    rows = [(100.0, 95.0, 97.0)] * 1 + pre + flag
    assert len(rows) == 20
    b = lambda r: L77._bear_v(mk(r), pb).values[-1]
    assert b(rows + [(82.0, 77.0, 78.9)])
    assert not b(rows + [(82.0, 77.0, 79.0)])                                 # close equal to the pole day's low
    assert not b(rows[:-1] + [(89.61, 80.0, 82.0)] + [(82.0, 77.0, 78.9)])     # flag above 112% of the pole close
    assert b(rows[:-1] + [(89.6, 80.0, 82.0)] + [(82.0, 77.0, 78.9)])
    assert not b(rows[:-1] + [(84.0, 78.99, 82.0)] + [(82.0, 77.0, 78.9)])     # flag undercut the pole low
    assert b(rows[:-1] + [((1 + 0.12) * 80.0, 80.0, 82.0)] + [(82.0, 77.0, 78.9)])   # flag high exactly 112% counts
    assert b(rows[:-1] + [(84.0, 79.0, 82.0)] + [(82.0, 77.0, 78.9)])         # flag low equal to the pole low counts
    late = rows[:17] + [(84.0, 80.0, 79.5)] + rows[18:]
    assert b(late + [(82.0, 77.0, 78.9)])                                     # a lower close at j-3 is not the pole bottom
    shallow = [(r[0], r[1], 80.01) if i == 13 else r for i, r in enumerate(rows)]
    assert not b(shallow + [(82.0, 77.0, 78.9)])                              # pole fell less than 20%
    # failed bull flag: pole day 12 close 120 from low 100; flag lows 110..; breakdown below 110
    pf = dict(pole=0.20)
    pre = [(101.0, 100.0, 100.5)] * 11 + [(112.0, 104.0, 110.0), (121.0, 109.0, 120.0)]
    rows = [(101.0, 100.0, 100.5)] + pre + [(118.0, 111.0, 115.0), (117.0, 110.0, 114.0)] + [(116.0, 112.0, 113.0)] * 4
    fb = lambda r: L77._fail_v(mk(r), pf).values[-1]
    assert fb(rows + [(114.0, 108.0, 109.9)])
    assert not fb(rows + [(114.0, 108.0, 110.0)])                             # close equal to the flag low
    assert not fb(rows[:-1] + [(121.01, 112.0, 113.0)] + [(114.0, 108.0, 109.9)])   # flag above the pole high
    assert not fb(rows[:-1] + [(116.0, 109.0, 109.5)] + [(114.0, 108.0, 107.0)])    # day j-1 already broke down
    assert fb(rows[:-1] + [(121.0, 112.0, 113.0)] + [(114.0, 108.0, 109.9)])   # flag high equal to the pole high counts
    weak = [(r[0], r[1], 119.99) if i == 13 else r for i, r in enumerate(rows)]
    assert not fb(weak + [(114.0, 108.0, 109.9)])                             # pole close below 1.20 x 100


def test_v77_regime_gate_and_hour_zero_with_gaps():
    btc = _ms(_mk(2600, '4h', 52)); fired = 0; seen = {}
    for key, seeds in (('s77_bear_flag_4h', (41, 40)), ('s77_failed_bull_flag_4h', (40, 44))):
        _, fn, p, ok = _V77_FN[key]
        for seed in seeds:
            coin = _ms(_mk(2600, '4h', seed)); d = S.indicators(coin); cr = L.coin_regime(d)
            f = np.asarray(fn(d, p))
            full = _sig(key, coin, btc); got = np.asarray(full['se'])
            np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
            for r in (0, 1, -1):
                seen[(key, r)] = seen.get((key, r), 0) + int((f & (cr == r)).sum())
            t = pd.to_datetime(coin.t).dt.normalize()
            for i in np.flatnonzero(got)[:20]:
                assert pd.Timestamp(coin.t.iloc[i]).hour == 0
                cut = coin[t != t.iloc[i] - pd.Timedelta(days=18)].reset_index(drop=True)
                part = _sig(key, cut, btc)
                k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
                fired += 1
                assert not np.asarray(part['se'])[k0], key
    assert fired and seen[('s77_bear_flag_4h', 0)] and seen[('s77_bear_flag_4h', -1)], seen
    assert seen[('s77_failed_bull_flag_4h', 1)] and seen[('s77_failed_bull_flag_4h', 0)], seen


import longscout_v78 as L78
L78.register()

_V78_COIN = {'l78_cup_handle_4h': lambda: _ms(_mk(2600, '4h', 44)),
             's78_inverted_cup_4h': lambda: _ms(_mk(2600, '4h', 41))}


@pytest.mark.parametrize('key', sorted(L78.CANDIDATES))
def test_v78_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V78_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l78': le.sum() and not se.any(), 's78': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v78_ref(D, up, n=60, ln=41, r0=15, r1=5, lip=0.05, depth=0.15, hd=0.12):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        X = H if up else Lo
        best = (lambda idx: min(idx, key=lambda i: (-X[i], i))) if up else (lambda idx: min(idx, key=lambda i: (X[i], i)))
        a = best(range(j - n, j - ln + 1)); r = best(range(j - r0, j - r1 + 1))
        if up:
            ok = (H[r] >= (1 - lip) * H[a] and min(Lo[a + 1:r]) <= (1 - depth) * H[a]
                  and max(H[r + 1:j]) <= H[r] and min(Lo[r + 1:j]) >= (1 - hd) * H[r])
            lv = max(H[a], H[r]); res[j] = ok and C[j] > lv and C[j - 1] <= lv
        else:
            ok = (Lo[r] <= (1 + lip) * Lo[a] and max(H[a + 1:r]) >= (1 + depth) * Lo[a]
                  and min(Lo[r + 1:j]) >= Lo[r] and max(H[r + 1:j]) <= (1 + hd) * Lo[r])
            lv = min(Lo[a], Lo[r]); res[j] = ok and C[j] < lv and C[j - 1] >= lv
    return res


def test_v78_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (41, 42, 44, 45, 118):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L78._cup_v(D, L78.CP, up).values
            np.testing.assert_array_equal(v, _v78_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 44:
                for cut in range(65, len(D), 37):
                    np.testing.assert_array_equal(L78._cup_v(D.iloc[:cut], L78.CP, up).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L78._cup_v(gap, L78.CP, up).values, _v78_ref(gap, up))
    assert all(hits.values()), hits


def _v78_days(h, l, c):
    return pd.DataFrame(dict(h=h, l=l, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))


def _v78_cup(cj=101.0, lip_h=100.0, bottom=85.0, handle_low=88.0, handle_high=93.0, left_extra=None, prev=None, bump=None):
    h = [90.0] * 60; l = [86.0] * 60; c = [88.0] * 60
    h[5] = 100.0                                                              # left lip (day j-55)
    l[30] = bottom                                                            # cup bottom
    h[48] = lip_h                                                             # right lip (day j-12)
    for i in range(49, 60):
        h[i], l[i], c[i] = handle_high, handle_low, (handle_high + handle_low) / 2
    if left_extra is not None:
        h[left_extra[0]] = left_extra[1]
    if prev is not None:
        c[59] = prev
    if bump is not None:
        h[bump[0]] = bump[1]
    return _v78_days(h + [max(cj, 99.0)], l + [90.0], c + [cj])


def test_v78_synthetic_cup_edges():
    f = lambda D: L78._cup_v(D, L78.CP, True).values[-1]
    assert f(_v78_cup())
    assert not f(_v78_cup(cj=100.0))                                          # close equal to the lip
    assert f(_v78_cup(lip_h=95.0)) and not f(_v78_cup(lip_h=94.99))           # right lip >= 0.95 H1 (handle highs 93)
    assert f(_v78_cup(bottom=85.0)) and not f(_v78_cup(bottom=85.01))         # depth >= 15%
    assert f(_v78_cup(handle_low=88.0)) and not f(_v78_cup(handle_low=87.99))   # handle >= 0.88 R
    assert f(_v78_cup(bump=(58, 100.0))) and not f(_v78_cup(bump=(58, 100.01)))   # handle (j-2) never above R
    assert not f(_v78_cup(prev=100.5))                                        # day j-1 already above
    assert f(_v78_cup(left_extra=(19, 101.0), cj=101.5))                      # day j-41 is in the left-lip window
    assert not f(_v78_cup(left_extra=(19, 101.0), cj=101.0))
    assert f(_v78_cup(left_extra=(20, 101.0), cj=101.0))                      # day j-40 is outside it
    assert f(_v78_cup(left_extra=(45, 99.0)))                                 # j-15 high below R does not move r
    D = _v78_cup(); h = D.h.values.copy(); h[45] = 100.0; D2 = D.assign(h=h)
    assert not f(D2)                                                          # tie: earliest right-lip day (j-15) anchors, its handle dips to 86
    inv = lambda D: L78._cup_v(D.assign(h=200 - D.l, l=200 - D.h, c=200 - D.c), L78.CP, False).values[-1]
    assert inv(_v78_cup()) and not inv(_v78_cup(cj=100.0))
    assert inv(_v78_cup(lip_h=95.0)) and not inv(_v78_cup(lip_h=94.99))     # mirror: right low <= 1.05 x left low


def test_v78_regime_gate_including_excluded_side(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, seed, up, ok in (('l78_cup_handle_4h', 42, True, (0, 1)), ('l78_cup_handle_4h', 44, True, (0, 1)),
                              ('s78_inverted_cup_4h', 41, False, (0, -1)), ('s78_inverted_cup_4h', 118, False, (0, -1))):
        coin = _ms(_mk(2600, '4h', seed)); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L78._cup(d, L78.CP, up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        assert f.any()
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L78.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc); gg = np.asarray(g['le']) | np.asarray(g['se'])
            assert gg.any() == (forced in ok), (key, forced)
            monkeypatch.undo()
    t = pd.to_datetime(_V78_COIN['l78_cup_handle_4h']().t).dt.normalize()
    coin = _V78_COIN['l78_cup_handle_4h'](); full = _sig('l78_cup_handle_4h', coin, btc)
    for i in np.flatnonzero(np.asarray(full['le'])):
        assert pd.Timestamp(coin.t.iloc[i]).hour == 0
        cut = coin[t != t.iloc[i] - pd.Timedelta(days=50)].reset_index(drop=True)
        k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
        assert not np.asarray(_sig('l78_cup_handle_4h', cut, btc)['le'])[k0]


import longscout_v79 as L79
L79.register()

_V79_COIN = {'l79_weekly_turn_up_4h': lambda: _ms(_mk(2600, '4h', 51)),
             's79_weekly_turn_down_4h': lambda: _ms(_mk(2600, '4h', 51))}


@pytest.mark.parametrize('key', sorted(L79.CANDIDATES))
def test_v79_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V79_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l79': le.sum() and not se.any(), 's79': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | set(ent + 6) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v79_ref(D, up, k=4):
    days = list(D.index); have = set(days); H, Lo, C = list(D.h), list(D.l), list(D.c)
    res = np.zeros(len(C), bool); fired = set()
    for j, t in enumerate(days):
        w = t - pd.Timedelta(days=t.dayofweek)
        if w in fired:
            continue
        weeks = [w - pd.Timedelta(days=7 * i) for i in range(k + 1, 0, -1)]   # w-5 .. w-1
        if not all(all(x + pd.Timedelta(days=dd) in have for dd in range(7)) for x in weeks):
            continue
        hi = [max(H[days.index(x + pd.Timedelta(days=dd))] for dd in range(7)) for x in weeks]
        lo = [min(Lo[days.index(x + pd.Timedelta(days=dd))] for dd in range(7)) for x in weeks]
        if up and all(hi[i] < hi[i - 1] for i in range(1, k + 1)) and C[j] > hi[-1]:
            res[j] = True; fired.add(w)
        if not up and all(lo[i] > lo[i - 1] for i in range(1, k + 1)) and C[j] < lo[-1]:
            res[j] = True; fired.add(w)
    return res


def test_v79_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (43, 44, 46, 51):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L79._turn_v(D, dict(k=4), up).values
            np.testing.assert_array_equal(v, _v79_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 51:
                for cut in range(40, len(D), 23):
                    np.testing.assert_array_equal(L79._turn_v(D.iloc[:cut], dict(k=4), up).values, v[:cut])
                for drop in (len(D) // 2, len(D) // 3):
                    gap = D.drop(D.index[drop])
                    np.testing.assert_array_equal(L79._turn_v(gap, dict(k=4), up).values, _v79_ref(gap, up))
    assert all(hits.values()), hits


def test_v79_synthetic_weekly_turn_edges():
    start = pd.Timestamp('2022-01-03')                                        # a Monday
    def run(week_highs, week_c, up=True, drop=None):
        h, l, c = [], [], []
        for wh in week_highs:
            h += [wh] * 7; l += [wh - 10] * 7; c += [wh - 5] * 7
        h += [x + 1 for x in week_c]; l += [x - 1 for x in week_c]; c += list(week_c)
        D = pd.DataFrame(dict(h=h, l=l, c=c, v=1.0), index=pd.date_range(start, periods=len(c), freq='D'))
        if drop is not None:
            D = D.drop(D.index[drop])
        if not up:
            D = D.assign(h=200 - D.l, l=200 - D.h, c=200 - D.c)
        return L79._turn_v(D, dict(k=4), up).values[-len(week_c):].tolist()
    hs = [110.0, 108.0, 106.0, 104.0, 102.0]                                  # five complete weeks, four lower highs
    assert run(hs, [101.0, 103.0, 104.0]) == [0, 1, 0]                        # first close above 102 only
    assert run(hs, [101.0, 102.0]) == [0, 0]                                  # equal close is not above
    assert run([110.0, 108.0, 108.0, 104.0, 102.0], [103.0]) == [0]           # an equal weekly high breaks the run
    assert run([100.0] + hs[1:], [103.0]) == [0]                              # week w-5 lower than w-4: only three
    assert run(hs, [103.0], drop=10) == [0]                                   # incomplete week w-4
    assert run(hs, [103.0], up=False) == [1]                                  # mirror (lows rise, close below)
    assert run(hs, [102.0], up=False) == [0]                                  # mirror: equal close is not below
    assert run([110.0, 108.0, 108.0, 104.0, 102.0], [103.0], up=False) == [0]   # mirror: equal weekly low breaks the run
    assert run([112.0, 110.0, 108.0, 106.0, 104.0, 102.0], [103.0], drop=17) == [0]   # a missing week inside the run
    assert run([112.0, 110.0, 108.0, 106.0, 104.0, 102.0], [103.0]) == [1]


def test_v79_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l79_weekly_turn_up_4h', True, (0, 1)), ('s79_weekly_turn_down_4h', False, (0, -1))):
        coin = _V79_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L79._turn(d, dict(k=4), up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        for i in np.flatnonzero(got):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L79.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import longscout_v80 as L80
L80.register()

_V80_COIN = {'s80_triple_top_4h': lambda: _ms(_mk(2600, '4h', 83)),
             'l80_triple_bottom_4h': lambda: _ms(_mk(2600, '4h', 131))}


@pytest.mark.parametrize('key', sorted(L80.CANDIDATES))
def test_v80_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V80_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l80': le.sum() and not se.any(), 's80': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v80_ref(D, top, tol=0.03, depth=0.05):
    days = list(D.index); H, Lo, C = list(D.h), list(D.l), list(D.c); m = len(C)
    res = np.zeros(m, bool)
    for j in range(60, m):
        if days[j] - days[j - 60] != pd.Timedelta(days=60):
            continue
        X = H if top else Lo
        key = (lambda i: (-X[i], i)) if top else (lambda i: (X[i], i))
        ix = [min(range(j - a, j - b + 1), key=key) for a, b in ((60, 41), (40, 21), (20, 3))]
        v = [X[i] for i in ix]
        if max(v) > (1 + tol) * min(v):
            continue
        if top:
            neck = min(Lo[ix[0]:ix[2] + 1]); res[j] = neck <= (1 - depth) * min(v) and C[j] < neck and C[j - 1] >= neck
        else:
            neck = max(H[ix[0]:ix[2] + 1]); res[j] = neck >= (1 + depth) * max(v) and C[j] > neck and C[j - 1] <= neck
    return res


def test_v80_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (55, 83, 101, 131):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for top in (True, False):
            v = L80._triple_v(D, L80.TP, top).values
            np.testing.assert_array_equal(v, _v80_ref(D, top), err_msg=str((seed, top)))
            hits[top] = hits.get(top, 0) + int(v.sum())
            if seed == 83:
                for cut in range(65, len(D), 37):
                    np.testing.assert_array_equal(L80._triple_v(D.iloc[:cut], L80.TP, top).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L80._triple_v(gap, L80.TP, top).values, _v80_ref(gap, top))
    assert all(hits.values()), hits


def test_v80_synthetic_triple_edges():
    def run(peaks=(103.0, 100.0, 101.0), at=(5, 30, 50), neck=95.0, cj=94.9, prev=None, top=True, extra=None):
        h = [97.0] * 60; l = [96.0] * 60; c = [96.5] * 60
        for i, pk in zip(at, peaks):
            h[i] = pk
        l[40] = neck
        if prev is not None:
            c[59] = prev
        if extra is not None:
            h[extra[0]] = extra[1]
        h, l, c = h + [97.0], l + [min(cj, 96.0)], c + [cj]
        D = pd.DataFrame(dict(h=h, l=l, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=61, freq='D'))
        if not top:
            D = D.assign(h=200 - D.l, l=200 - D.h, c=200 - D.c)
        return L80._triple_v(D, L80.TP, top).values[-1]
    assert run()                                                              # peaks 103/100/101, neck 95 = 0.95 x 100
    assert not run(cj=95.0)                                                   # close equal to the neckline
    assert not run(peaks=(103.01, 100.0, 101.0))                              # spread above 3%
    assert not run(neck=95.01)                                                # neckline less than 5% below the lowest peak
    assert not run(prev=94.99)                                                # day j-1 already below
    assert run(at=(0, 20, 57))                                                # window starts j-60, j-40; peak 3 may sit at j-3
    assert not run(at=(0, 20, 58), extra=(57, 97.5))                          # j-2 is outside the third window
    assert not run(extra=(19, 104.1)) and not run(extra=(20, 104.1))          # j-41 and j-40 are inside the peak windows
    assert not run(extra=(57, 104.1)) and run(extra=(58, 104.1))              # j-3 is inside; j-2 is not
    mp = (102.0, 100.0, 101.0)                                                # mirror troughs 98/100/99
    assert run(mp, top=False) and not run(mp, top=False, cj=95.0)             # mirror: neckline 105, close 105.1 vs 105


def test_v80_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, top, ok in (('s80_triple_top_4h', True, (0, -1)), ('l80_triple_bottom_4h', False, (0, 1))):
        coin = _V80_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L80._triple(d, L80.TP, top))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got):
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=50)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L80.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import longscout_v81 as L81
L81.register()

_V81_COIN = {'r81_variance_ratio_4h': lambda: _ms(_mk(2600, '4h', 44)),
             's81_unconfirmed_rally_4h': lambda: _ms(_mk(2600, '4h', 45))}
_V81_P = {k: v['params'] for k, v in L81.CANDIDATES.items()}


@pytest.mark.parametrize('key', sorted(L81.CANDIDATES))
def test_v81_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V81_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'r81': le.sum() and se.sum(), 's81': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v81_vr_ref(D, n=60, k=5, lvl=1.3):
    days = list(D.index); C = list(D.c); m = len(C)
    vr = [None] * m
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        r = [np.log(C[t] / C[t - 1]) for t in range(j - n + 1, j + 1)]
        mu = sum(r) / n; v1 = sum((x - mu) ** 2 for x in r) / n
        s = [sum(r[t:t + k]) for t in range(n - k + 1)]
        ms = sum(s) / len(s); vk = sum((x - ms) ** 2 for x in s) / len(s)
        vr[j] = vk / (k * v1)
    return np.array([j > 0 and vr[j] is not None and vr[j - 1] is not None and vr[j] >= lvl and vr[j - 1] < lvl
                     for j in range(m)])


def _v81_rally_ref(D, n=20, up=0.15, q=0.7):
    days = list(D.index); C, V = list(D.c), list(D.v); m = len(C)
    raw = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        raw[j] = C[j] >= (1 + up) * C[j - n] and np.mean(V[j - 9:j + 1]) <= q * np.mean(V[j - 19:j - 9])
    return raw & ~np.r_[False, raw[:-1]]


def test_v81_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (43, 44, 45, 47, 50):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for key, fn, ref in (('r81_variance_ratio_4h', L81._vr_v, _v81_vr_ref), ('s81_unconfirmed_rally_4h', L81._rally_v, _v81_rally_ref)):
            v = fn(D, _V81_P[key]).values
            r = ref(D)
            np.testing.assert_array_equal(v, r, err_msg=str((seed, key)))
            hits[key] = hits.get(key, 0) + int(v.sum())
            if seed == 44:
                for cut in range(65, len(D), 37):
                    np.testing.assert_array_equal(fn(D.iloc[:cut], _V81_P[key]).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(fn(gap, _V81_P[key]).values, ref(gap), err_msg=str(('gap', key)))
    assert all(hits.values()), hits


def test_v81_synthetic_edges():
    def days(c, v=None):
        v = [1.0] * len(c) if v is None else v
        return pd.DataFrame(dict(h=c, l=c, c=c, v=v), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
    p = _V81_P['s81_unconfirmed_rally_4h']
    c = [100.0] * 21 + [115.0]; v = [10.0] * 12 + [7.0] * 10                  # j = 21: +15% vs day 1; vol 7 vs 10
    assert L81._rally_v(days(c, v), p).values[-1]
    assert not L81._rally_v(days(c[:-1] + [114.99], v), p).values[-1]        # rise just under 15%
    assert not L81._rally_v(days(c, v[:-1] + [7.01]), p).values[-1]          # volume ratio just above 0.7
    c2 = [100.0] * 21 + [115.0, 116.0]; v2 = v + [7.0]
    assert L81._rally_v(days(c2, v2), p).values.tolist()[-2:] == [True, False]   # first day only
    runs = list(100.0 * np.exp(np.r_[0.0, np.cumsum(np.tile([0.01] * 10 + [-0.01] * 10, 4))]))
    vr = L81._vr_series(days(runs), _V81_P['r81_variance_ratio_4h']).values
    assert np.isfinite(vr[60:]).all() and (vr[60:] > 1.3).all()             # 10-day runs give a high variance ratio
    alt = list(100.0 * np.exp(np.r_[0.0, np.cumsum(np.tile([0.01, -0.01], 40))]))
    vr2 = L81._vr_series(days(alt), _V81_P['r81_variance_ratio_4h']).values
    assert (vr2[60:] < 0.5).all()                                            # alternating returns mean-revert
    flat = L81._vr_series(days([100.0] * 70), _V81_P['r81_variance_ratio_4h']).values
    assert np.isnan(flat).all()                                              # zero variance gives no value
    D = days(runs)
    assert np.isnan(L81._vr_series(D.drop(D.index[len(D) - 41]), _V81_P['r81_variance_ratio_4h']).values[-1])   # gap 40 days back
    c3 = [100.0] * 21 + [(1 + 0.15) * 100.0]
    assert L81._rally_v(days(c3, v), p).values[-1]                            # exactly +15% counts
    assert not L81._rally_v(days(c3, v).drop(pd.Timestamp('2022-01-06')), p).values[-1]   # a missing day inside j-20..j


def test_v81_variance_ratio_threshold_is_inclusive_first_cross(monkeypatch):
    D = pd.DataFrame(dict(h=1.0, l=1.0, c=1.0, v=1.0), index=pd.date_range('2022-01-01', periods=5, freq='D'))
    monkeypatch.setattr(L81, '_vr_series', lambda D, p: pd.Series([np.nan, 1.0, 1.3, 1.4, 1.2], index=D.index))
    assert L81._vr_v(D, _V81_P['r81_variance_ratio_4h']).tolist() == [False, False, True, False, False]


def test_v81_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, ok in (('r81_variance_ratio_4h', None), ('s81_unconfirmed_rally_4h', (0, -1))):
        coin = _V81_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray((L81._vr if ok is None else L81._rally)(d, _V81_P[key]))
        full = _sig(key, coin, btc); le, se = np.asarray(full['le']), np.asarray(full['se'])
        if ok is None:
            np.testing.assert_array_equal(le, f & (cr == 1)); np.testing.assert_array_equal(se, f & (cr == -1))
        else:
            np.testing.assert_array_equal(se, f & np.isin(cr, ok)); assert not le.any()
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(le | se)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=15)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L81.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc); gl, gs = np.asarray(g['le']).any(), np.asarray(g['se']).any()
            if ok is None:
                assert (gl, gs) == (forced == 1.0, forced == -1.0), (key, forced)
            else:
                assert gs == (forced in ok) and not gl, (key, forced)
            monkeypatch.undo()


import longscout_v82 as L82
L82.register()

_V82_COIN = {'l82_liquidity_improves_4h': lambda: _ms(_mk(2600, '4h', 47)),
             's82_liquidity_dries_4h': lambda: _ms(_mk(2600, '4h', 40))}


@pytest.mark.parametrize('key', sorted(L82.CANDIDATES))
def test_v82_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V82_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l82': le.sum() and not se.any(), 's82': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v82_ref(D, low, m=20, n=120):
    days = list(D.index); C, V = list(D.c), list(D.v); L_ = len(C)
    il = [None] + [abs(np.log(C[t] / C[t - 1])) / (C[t] * V[t]) for t in range(1, L_)]
    A = [None] * L_
    for t in range(m, L_):
        A[t] = sum(il[t - m + 1:t + 1]) / m
    raw = np.zeros(L_, bool)
    for j in range(n + m, L_):
        if days[j] - days[j - n - m] != pd.Timedelta(days=n + m):
            continue
        prev = A[j - n:j]
        raw[j] = (A[j] < min(prev)) if low else (A[j] > max(prev))
    return raw & ~np.r_[False, raw[:-1]]


def test_v82_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (40, 43, 47, 50):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for low in (True, False):
            v = L82._illiq_v(D, L82.IP, low).values
            np.testing.assert_allclose(v, _v82_ref(D, low), err_msg=str((seed, low)))
            hits[low] = hits.get(low, 0) + int(v.sum())
            if seed == 47:
                for cut in range(145, len(D), 37):
                    np.testing.assert_array_equal(L82._illiq_v(D.iloc[:cut], L82.IP, low).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L82._illiq_v(gap, L82.IP, low).values, _v82_ref(gap, low))
    assert all(hits.values()), hits


def test_v82_synthetic_edges():
    def days(c, v):
        return pd.DataFrame(dict(h=c, l=c, c=c, v=v), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
    rng = np.random.default_rng(3)
    c = list(100 * np.exp(np.cumsum(rng.choice([-0.01, 0.01], 160)))); v = [1000.0] * 160
    v = [1e5 / x for x in c]                                                 # constant dollar volume and |return|
    v2 = v[:-1] + [v[-1] * 100]                                              # a huge-volume last day lowers A
    assert L82._illiq_v(days(c, v2), L82.IP, True).values[-1]
    assert not L82._illiq_v(days(c, v2), L82.IP, False).values[-1]
    v3 = v[:-1] + [v[-1] / 100]                                              # a tiny-volume last day raises A
    assert L82._illiq_v(days(c, v3), L82.IP, False).values[-1]
    v4 = v[:-2] + [v[-2] * 100, v[-1] * 1000]                                # second consecutive low is not a first day
    flat = days([100.0] * 160, [1000.0] * 160)                               # A is constant: equal values are no new extreme
    assert not L82._illiq_v(flat, L82.IP, True).any() and not L82._illiq_v(flat, L82.IP, False).any()
    assert L82._illiq_v(days(c, v4), L82.IP, True).values[-2:].tolist() == [True, False]
    assert not L82._illiq_v(days(c, v2).drop(days(c, v2).index[30]), L82.IP, True).values[-1]   # a missing day inside j-140..j


def test_v82_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, low, ok in (('l82_liquidity_improves_4h', True, (1,)), ('s82_liquidity_dries_4h', False, (-1,))):
        coin = _V82_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L82._illiq(d, L82.IP, low))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=100)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L82.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import longscout_v83 as L83
L83.register()

_V83_COIN = {'l83_convex_turn_up_4h': lambda: _ms(_mk(2600, '4h', 40)),
             's83_concave_turn_down_4h': lambda: _ms(_mk(2600, '4h', 40))}


@pytest.mark.parametrize('key', sorted(L83.CANDIDATES))
def test_v83_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V83_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l83': le.sum() and not se.any(), 's83': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v83_ref(D, up, n=30):
    days = list(D.index); y = np.log(np.asarray(D.c, float)); m = len(y)
    s = [None] * m; cv = [None] * m
    for j in range(n - 1, m):
        c2, b, a = np.polyfit(np.arange(n), y[j - n + 1:j + 1], 2)
        s[j] = b + 2 * c2 * (n - 1); cv[j] = c2
    res = np.zeros(m, bool)
    for j in range(n, m):
        if days[j] - days[j - n] != pd.Timedelta(days=n):
            continue
        res[j] = (cv[j] > 0 and s[j] > 0 and s[j - 1] <= 0) if up else (cv[j] < 0 and s[j] < 0 and s[j - 1] >= 0)
    return res


def test_v83_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (40, 43, 46, 49):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L83._turn_v(D, dict(n=30), up).values
            np.testing.assert_array_equal(v, _v83_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 40:
                for cut in range(35, len(D), 37):
                    np.testing.assert_array_equal(L83._turn_v(D.iloc[:cut], dict(n=30), up).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L83._turn_v(gap, dict(n=30), up).values, _v83_ref(gap, up))
    assert all(hits.values()), hits


def test_v83_synthetic_parabola():
    def days(y):
        c = list(np.exp(y))
        return pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))
    x = np.arange(80.0)
    up = L83._turn_v(days(0.001 * (x - 50.5) ** 2), dict(n=30), True).values
    # slope at day j of the fit through j-29..j equals the true slope 0.002 (j - 50.5): first positive at j = 51
    assert up.tolist().index(True) == 51 and up.sum() == 1
    dn = L83._turn_v(days(-0.001 * (x - 50.5) ** 2), dict(n=30), False).values
    assert dn.tolist().index(True) == 51 and dn.sum() == 1
    assert not L83._turn_v(days(-0.001 * (x - 50.5) ** 2), dict(n=30), True).any()   # concave curve never gives a long
    lin = L83._turn_v(days(np.r_[-0.01 * x[:50], -0.5 + 0.01 * (x[50:] - 49)]), dict(n=30), True).values
    assert lin.any()                                                         # V-shape: convex fit turning up
    g = days(0.001 * (x - 50.5) ** 2)
    assert not L83._turn_v(g.drop(g.index[30]), dict(n=30), True).any()      # a day inside the j = 51 fit window missing
    assert not L83._turn_v(g.drop(g.index[21]), dict(n=30), True).any()      # day j-30 (start of the j-1 fit) missing


def test_v83_zero_boundaries(monkeypatch):
    D = pd.DataFrame(dict(h=1.0, l=1.0, c=1.0, v=1.0), index=pd.date_range('2022-01-01', periods=34, freq='D'))
    def run(s, cv, up):
        monkeypatch.setattr(L83, '_fit', lambda D, n: (np.r_[np.full(31, np.nan), s], np.r_[np.full(31, np.nan), cv]))
        out = L83._turn_v(D, dict(n=30), up).values[31:].tolist()
        monkeypatch.undo()
        return out
    assert run([-1.0, 1.0, 2.0], [1.0, 1.0, 1.0], True) == [False, True, False]
    assert run([0.0, 1.0, 2.0], [1.0, 1.0, 1.0], True) == [False, True, False]      # previous slope exactly 0 counts
    assert run([-1.0, 1.0, 2.0], [1.0, 0.0, 1.0], True) == [False, False, False]    # zero curvature is not convex
    assert run([1.0, -1.0, -2.0], [-1.0, -1.0, -1.0], False) == [False, True, False]
    assert run([1.0, 0.0, -2.0], [-1.0, -1.0, -1.0], False) == [False, False, True]  # s=0 does not fire; sp=0 counts as turn
    assert run([0.0, -1.0, -2.0], [-1.0, -1.0, -1.0], False) == [False, True, False]
    assert run([-1.0, 0.0, 2.0], [1.0, 1.0, 1.0], True) == [False, False, True]      # s exactly 0 is not rising
    assert run([1.0, -1.0, -2.0], [-1.0, 0.0, -1.0], False) == [False, False, False]  # zero curvature is not concave


def test_v83_needs_day_j_minus_30(monkeypatch):
    idx = pd.date_range('2022-01-01', periods=35, freq='D').delete(2)       # day 2 missing; 34 rows
    D = pd.DataFrame(dict(h=1.0, l=1.0, c=1.0, v=1.0), index=idx)
    s = np.r_[np.full(30, np.nan), -1.0, 1.0, -1.0, 1.0]
    monkeypatch.setattr(L83, '_fit', lambda D, n: (s, np.ones(len(D))))
    # row 31 is day 32: days 3..32 present but day j-30 = 2 missing -> no signal; row 33 has days 4..34 complete
    assert L83._turn_v(D, dict(n=30), True).values[30:].tolist() == [False, False, False, True]


def test_v83_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l83_convex_turn_up_4h', True, (0, 1)), ('s83_concave_turn_down_4h', False, (0, -1))):
        coin = _V83_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L83._turn(d, dict(n=30), up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=20)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L83.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import math
import longscout_v84 as L84
L84.register()

_V84_COIN = {'l84_downside_variance_flush_4h': lambda: _ms(_mk(2600, '4h', 44)),
             's84_upside_variance_spike_4h': lambda: _ms(_mk(2600, '4h', 44))}


@pytest.mark.parametrize('key', sorted(L84.CANDIDATES))
def test_v84_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V84_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l84': le.sum() and not se.any(), 's84': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v84_ref(D, up, n=30, lvl=0.5):
    days = list(D.index); c = [float(x) for x in D.c]; m = len(c)

    def sj(j):
        p = q = 0.0
        for t in range(j - n + 1, j + 1):
            r = math.log(c[t] / c[t - 1])
            if r > 0:
                p += r * r
            elif r < 0:
                q += r * r
        return (p - q) / (p + q) if p + q > 0 else None

    res = np.zeros(m, bool)
    for j in range(n + 1, m):
        if days[j] - days[j - n - 1] != pd.Timedelta(days=n + 1):
            continue
        a, b = sj(j), sj(j - 1)
        if a is None or b is None:
            continue
        res[j] = (a > lvl and b <= lvl) if up else (a < -lvl and b >= -lvl)
    return res


def test_v84_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (44, 41, 47, 50):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L84._cross_v(D, L84.IP, up).values
            np.testing.assert_array_equal(v, _v84_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 44:
                for cut in range(35, len(D), 37):
                    np.testing.assert_array_equal(L84._cross_v(D.iloc[:cut], L84.IP, up).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L84._cross_v(gap, L84.IP, up).values, _v84_ref(gap, up))
    assert all(hits.values()), hits


def _v84_days(r):
    c = np.exp(np.r_[0.0, np.cumsum(r)])
    return pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))


def test_v84_synthetic_semivariance():
    # 40 alternating +-1% days (SJ = 0), then one +8% day: SJ = (64 +- 1) / (64 + 29) > 0.5 (units of 1e-4)
    r = np.r_[np.tile([0.01, -0.01], 20), 0.08, 0.0, 0.0]
    D = _v84_days(r)
    sj = L84._sj(D, 30)
    assert abs(sj.iloc[40]) < 1e-12 and sj.iloc[41] > 0.5
    up = L84._cross_v(D, L84.IP, True).values
    assert up.tolist().index(True) == 41 and up.sum() == 1
    assert not L84._cross_v(D, L84.IP, False).any()
    dn = L84._cross_v(_v84_days(np.r_[np.tile([0.01, -0.01], 20), -0.08, 0.0]), L84.IP, False).values
    assert dn.tolist().index(True) == 41 and dn.sum() == 1
    flat = _v84_days(np.zeros(45))                                         # zero variance: SJ undefined, never fires
    assert not L84._cross_v(flat, L84.IP, True).any() and not L84._cross_v(flat, L84.IP, False).any()
    z = L84._cross_v(_v84_days(np.r_[np.zeros(40), 0.08, 0.0]), L84.IP, True).values
    assert not z.any()                                                     # previous SJ undefined (0/0) is not a cross
    g = _v84_days(r)
    assert not L84._cross_v(g.drop(g.index[10]), L84.IP, True).any()       # day j-31 of the shifted fire day missing
    assert not L84._cross_v(g.drop(g.index[11]), L84.IP, True).any()       # day j-30 missing


def test_v84_level_boundaries(monkeypatch):
    D = pd.DataFrame(dict(h=1.0, l=1.0, c=1.0, v=1.0), index=pd.date_range('2022-01-01', periods=36, freq='D'))
    def run(v, up):
        monkeypatch.setattr(L84, '_sj', lambda D, n: pd.Series(np.r_[np.full(32, np.nan), v], index=D.index))
        out = L84._cross_v(D, L84.IP, up).values[32:].tolist()
        monkeypatch.undo()
        return out
    assert run([0.4, 0.6, 0.7, 0.4], True) == [False, True, False, False]
    assert run([0.5, 0.6, 0.5, 0.6], True) == [False, True, False, True]    # previous exactly 0.5 counts
    assert run([0.4, 0.5, 0.6, 0.4], True) == [False, False, True, False]   # exactly 0.5 is not above
    assert run([-0.4, -0.6, -0.7, -0.4], False) == [False, True, False, False]
    assert run([-0.5, -0.6, -0.5, -0.6], False) == [False, True, False, True]
    assert run([-0.4, -0.5, -0.6, -0.4], False) == [False, False, True, False]
    idx = pd.date_range('2022-01-01', periods=37, freq='D').delete(1)      # day 1 missing; 36 rows
    G = pd.DataFrame(dict(h=1.0, l=1.0, c=1.0, v=1.0), index=idx)
    monkeypatch.setattr(L84, '_sj', lambda D, n: pd.Series(np.r_[np.full(30, np.nan), 0.4, 0.6, 0.4, 0.6, 0.6, 0.6], index=D.index))
    # row 31 is day 32: days 2..32 present but day j-31 = 1 missing -> no signal; row 33 (day 34) has days 3..34 complete
    assert L84._cross_v(G, L84.IP, True).values[30:].tolist() == [False, False, False, True, False, False]


def test_v84_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l84_downside_variance_flush_4h', False, (0, 1)), ('s84_upside_variance_spike_4h', True, (0, -1))):
        coin = _V84_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L84._cross(d, L84.IP, up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        assert got.any(), key
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=20)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L84.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import longscout_v85 as L85
L85.register()

_V85_COIN = {'l85_volume_confirms_rally_4h': lambda: _ms(_mk(2600, '4h', 46)),
             's85_volume_confirms_selloff_4h': lambda: _ms(_mk(2600, '4h', 40))}


@pytest.mark.parametrize('key', sorted(L85.CANDIDATES))
def test_v85_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V85_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l85': le.sum() and not se.any(), 's85': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v85_ref(D, up, n=30, lvl=0.4):
    days = list(D.index); c = [float(x) for x in D.c]; v = [float(x) for x in D.v]; m = len(c)

    def rho(j):
        xs = [math.log(c[t] / c[t - 1]) for t in range(j - n + 1, j + 1)]
        ys = [math.log(v[t]) for t in range(j - n + 1, j + 1)]
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        sxx = sum((a - mx) ** 2 for a in xs); syy = sum((b - my) ** 2 for b in ys)
        return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None

    res = np.zeros(m, bool)
    for j in range(n + 1, m):
        if days[j] - days[j - n - 1] != pd.Timedelta(days=n + 1):
            continue
        a, b = rho(j), rho(j - 1)
        if a is None or b is None:
            continue
        R = math.log(c[j] / c[j - n])
        res[j] = (a > lvl and b <= lvl and R > 0) if up else (a < -lvl and b >= -lvl and R < 0)
    return res


def test_v85_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (46, 40, 48, 50):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L85._pv_v(D, L85.IP, up).values
            np.testing.assert_array_equal(v, _v85_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 40:
                for cut in range(35, len(D), 37):
                    np.testing.assert_array_equal(L85._pv_v(D.iloc[:cut], L85.IP, up).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L85._pv_v(gap, L85.IP, up).values, _v85_ref(gap, up))
    assert all(hits.values()), hits


def _v85_days(r, v):
    c = np.exp(np.r_[0.0, np.cumsum(r)])
    return pd.DataFrame(dict(h=c, l=c, c=c, v=np.r_[1.0, v]), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))


def test_v85_synthetic_price_volume():
    # 40 days: volume uncorrelated with the +-1% alternating returns, then up days on heavy volume
    base_r = np.tile([0.01, -0.01], 20); base_v = np.tile([1.0, 1.0, 2.0, 2.0], 10)
    r = np.r_[base_r, [0.03] * 8, [0.0] * 3]; v = np.r_[base_v, [8.0] * 8, [1.0] * 3]
    D = _v85_days(r, v)
    up = L85._pv_v(D, L85.IP, True).values
    rho = L85._rho(D, 30)
    k = up.tolist().index(True)
    assert rho.iloc[k] > 0.4 >= rho.iloc[k - 1] and up.sum() == 1
    assert not L85._pv_v(D, L85.IP, False).any()
    Dn = _v85_days(-r, v)
    dn = L85._pv_v(Dn, L85.IP, False).values
    assert dn.tolist().index(True) == k and dn.sum() == 1
    assert not L85._pv_v(Dn, L85.IP, True).any()
    # heavy volume on down days inside a 30-day net gain: correlation negative but R > 0 -> no short
    r2 = np.r_[base_r, [0.06] * 5 + [-0.02] * 8]; v2 = np.r_[base_v, [1.0] * 5 + [8.0] * 8]
    D2 = _v85_days(r2, v2)
    assert (L85._rho(D2, 30) < -0.4).any() and not L85._pv_v(D2, L85.IP, False).any()
    g = _v85_days(r, v)
    assert not L85._pv_v(g.drop(g.index[k - 31]), L85.IP, True).any()      # day j-31 missing
    assert not L85._pv_v(g.drop(g.index[k - 30]), L85.IP, True).any()      # day j-30 (base of R) missing


def test_v85_level_and_return_boundaries(monkeypatch):
    n = 36
    def run(rh, R, up):
        c = np.exp(np.r_[np.zeros(32), R])
        D = pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=n, freq='D'))
        monkeypatch.setattr(L85, '_rho', lambda D, k: pd.Series(np.r_[np.full(32, np.nan), rh], index=D.index))
        out = L85._pv_v(D, L85.IP, up).values[32:].tolist()
        monkeypatch.undo()
        return out
    pos = [0.1, 0.1, 0.1, 0.1]
    assert run([0.3, 0.5, 0.6, 0.3], pos, True) == [False, True, False, False]
    assert run([0.4, 0.5, 0.4, 0.5], pos, True) == [False, True, False, True]     # previous exactly 0.4 counts
    assert run([0.3, 0.4, 0.5, 0.3], pos, True) == [False, False, True, False]    # exactly 0.4 is not above
    assert run([0.3, 0.5, 0.3, 0.5], [0.1, 0.0, 0.1, 0.1], True) == [False, False, False, True]   # R exactly 0 is not a gain
    neg = [-0.1, -0.1, -0.1, -0.1]
    assert run([-0.3, -0.5, -0.6, -0.3], neg, False) == [False, True, False, False]
    assert run([-0.4, -0.5, -0.4, -0.5], neg, False) == [False, True, False, True]
    assert run([-0.3, -0.4, -0.5, -0.3], neg, False) == [False, False, True, False]
    assert run([-0.3, -0.5, -0.3, -0.5], [-0.1, 0.0, -0.1, -0.1], False) == [False, False, False, True]


def test_v85_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l85_volume_confirms_rally_4h', True, (1,)), ('s85_volume_confirms_selloff_4h', False, (-1,))):
        coin = _V85_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L85._pv(d, L85.IP, up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        assert got.any(), key
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=20)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L85.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


def test_v85_return_uses_day_j_minus_30(monkeypatch):
    c = np.ones(36); c[2] = 2.0; c[32] = 1.5                                # R_32 = log(1.5 / c[2]) < 0, though c[32] > c[3]
    D = pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=36, freq='D'))
    monkeypatch.setattr(L85, '_rho', lambda D, k: pd.Series(np.r_[np.full(31, np.nan), 0.3, 0.5, 0.3, 0.3, 0.3], index=D.index))
    assert not L85._pv_v(D, L85.IP, True).any()
    c2 = np.ones(36); c2[2] = 0.5; c2[32] = 0.8
    D2 = pd.DataFrame(dict(h=c2, l=c2, c=c2, v=1.0), index=D.index)
    monkeypatch.setattr(L85, '_rho', lambda D, k: pd.Series(np.r_[np.full(31, np.nan), -0.3, -0.5, -0.3, -0.3, -0.3], index=D.index))
    assert not L85._pv_v(D2, L85.IP, False).any()


import longscout_v86 as L86
L86.register()

_V86_COIN = {'l86_smooth_uptrend_4h': lambda: _ms(_mk(2600, '4h', 40)),
             's86_smooth_downtrend_4h': lambda: _ms(_mk(2600, '4h', 40))}


@pytest.mark.parametrize('key', sorted(L86.CANDIDATES))
def test_v86_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V86_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l86': le.sum() and not se.any(), 's86': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v86_ref(D, up, k=14, m=30, n=90):
    days = list(D.index); c = [float(x) for x in D.c]; L = len(c)
    dd = [None] * L
    for t in range(k - 1, L):
        w = c[t - k + 1:t + 1]
        dd[t] = c[t] / (max(w) if up else min(w)) - 1
    U = [None] * L
    for t in range(k + m - 2, L):
        U[t] = math.sqrt(sum(dd[s] ** 2 for s in range(t - m + 1, t + 1)) / m)

    def low(t):
        return U[t] < min(U[t - n:t])

    res = np.zeros(L, bool)
    for j in range(k + m + n - 1, L):
        if days[j] - days[j - (k + m + n - 1)] != pd.Timedelta(days=k + m + n - 1):
            continue
        w = c[j - k + 1:j + 1]
        ext = c[j] >= max(w) if up else c[j] <= min(w)
        tr = c[j] > c[j - m] if up else c[j] < c[j - m]
        res[j] = low(j) and not low(j - 1) and ext and tr
    return res


def test_v86_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (40, 42, 44, 48):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L86._smooth_v(D, L86.IP, up).values
            np.testing.assert_array_equal(v, _v86_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 40:
                for cut in range(140, len(D), 37):
                    np.testing.assert_array_equal(L86._smooth_v(D.iloc[:cut], L86.IP, up).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L86._smooth_v(gap, L86.IP, up).values, _v86_ref(gap, up))
    assert all(hits.values()), hits


def _v86_days(c):
    c = np.asarray(c, float)
    return pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=len(c), freq='D'))


def test_v86_synthetic_choppy_then_smooth():
    # 150 flat choppy days (3-day cycle +3%, -3%, back), then +5% and a steady +1% climb: no pullbacks, UI drops to a new
    # 90-day low on the first climb day (151), which is also a 14-day closing high above the close 30 days earlier
    chop = np.cumprod(np.tile([1.03, 0.97, 1 / (1.03 * 0.97)], 50))
    c = np.r_[chop, chop[-1] * 1.05 * np.cumprod(np.full(40, 1.01))]
    up = L86._smooth_v(_v86_days(c), L86.IP, True).values
    U = L86._ulcer(_v86_days(c), 14, 30, True)
    assert np.flatnonzero(up)[0] == 151 and not up[:151].any()
    assert U.iloc[151] < U.iloc[61:151].min() and not U.iloc[150] < U.iloc[60:150].min()
    assert not up[152]                                                        # day 152 is a low again but not the first day
    assert U.iloc[152] < U.iloc[62:152].min()
    assert not L86._smooth_v(_v86_days(c), L86.IP, False).any()
    dn = L86._smooth_v(_v86_days(1.0 / c), L86.IP, False).values
    assert np.flatnonzero(dn)[0] == 151 and not dn[:151].any()
    assert not L86._smooth_v(_v86_days(1.0 / c), L86.IP, True).any()
    g = _v86_days(c)
    assert not L86._smooth_v(g.drop(g.index[151 - 133]), L86.IP, True).values[:151].any()   # day j-133 missing
    c2 = c.copy(); c2[151] = c2[149] * 0.999                                   # close not at the 14-day high
    assert not L86._smooth_v(_v86_days(c2), L86.IP, True).values[151]


def test_v86_boundaries(monkeypatch):
    L = 140
    def run(Uv, c, up):
        D = _v86_days(c)
        monkeypatch.setattr(L86, '_ulcer', lambda D, k, m, u: pd.Series(Uv, index=D.index))
        out = L86._smooth_v(D, L86.IP, up).values[134:].tolist()
        monkeypatch.undo()
        return out
    U = np.r_[np.full(43, np.nan), np.full(91, 1.0), [0.9, 0.8, 0.8, 1.0, 1.0, 0.7]]   # rows 134..139
    rise = np.r_[np.ones(104), np.linspace(1.0, 2.0, 36)]
    assert run(U, rise, True) == [True, False, False, False, False, True]
    U2 = U.copy(); U2[134] = 1.0                                                 # equal to the 90-day low is not lower
    assert run(U2, rise, True) == [False, True, False, False, False, True]
    flat = rise.copy(); flat[134] = flat[104]                                    # c_j == c_{j-30}: no 30-day gain
    flat[121:134] = np.minimum(flat[121:134], flat[134])
    assert run(U, flat, True)[0] is False
    tie = rise.copy(); tie[134] = tie[133]                                       # close equal to the 14-day high still counts
    assert run(U, tie, True)[0] is True
    base = rise.copy(); base[105] = 3.0                                           # day j-29 above c_j; only day j-30 is the base
    assert run(U, base, True)[0] is True
    fall = 1.0 / rise
    fb = fall.copy(); fb[105] = 0.1
    assert run(U, fb, False)[0] is True
    assert run(U, fall, False) == [True, False, False, False, False, True]
    tie2 = fall.copy(); tie2[134] = tie2[133]
    assert run(U, tie2, False)[0] is True
    f2 = fall.copy(); f2[134] = f2[104]; f2[121:134] = np.maximum(f2[121:134], f2[134])
    assert run(U, f2, False)[0] is False


def test_v86_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l86_smooth_uptrend_4h', True, (1,)), ('s86_smooth_downtrend_4h', False, (-1,))):
        coin = _V86_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L86._smooth(d, L86.IP, up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        assert got.any(), key
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=100)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L86.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import longscout_v87 as L87
L87.register()

_V87_COIN = {'l87_thin_tail_uptrend_4h': lambda: _ms(_mk(2600, '4h', 41)),
             's87_thin_tail_downtrend_4h': lambda: _ms(_mk(2600, '4h', 41))}


@pytest.mark.parametrize('key', sorted(L87.CANDIDATES))
def test_v87_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V87_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l87': le.sum() and not se.any(), 's87': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v87_kurt(x):
    n = len(x); mu = sum(x) / n
    m2 = sum((a - mu) ** 2 for a in x); m4 = sum((a - mu) ** 4 for a in x)
    return n * (n + 1) * (n - 1) * m4 / ((n - 2) * (n - 3) * m2 ** 2) - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))


def _v87_ref(D, up, m=60, n=120):
    days = list(D.index); c = [float(x) for x in D.c]; L = len(c)
    r = [None] + [math.log(c[t] / c[t - 1]) for t in range(1, L)]
    K = [None] * L
    for t in range(m, L):
        K[t] = _v87_kurt(r[t - m + 1:t + 1])

    def low(t):
        return K[t] < min(K[t - n:t])

    res = np.zeros(L, bool)
    for j in range(m + n + 1, L):
        if days[j] - days[j - (m + n + 1)] != pd.Timedelta(days=m + n + 1):
            continue
        R = math.log(c[j] / c[j - m])
        res[j] = low(j) and not low(j - 1) and (R > 0 if up else R < 0)
    return res


def test_v87_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (41, 40, 44, 51):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L87._thin_v(D, L87.IP, up).values
            np.testing.assert_array_equal(v, _v87_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 41:
                for cut in range(190, len(D), 37):
                    np.testing.assert_array_equal(L87._thin_v(D.iloc[:cut], L87.IP, up).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L87._thin_v(gap, L87.IP, up).values, _v87_ref(gap, up))
    assert all(hits.values()), hits


def test_v87_boundaries(monkeypatch):
    L = 190
    def run(Kv, c, up):
        D = pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=pd.date_range('2022-01-01', periods=L, freq='D'))
        monkeypatch.setattr(L87, '_kurt', lambda D, m: pd.Series(Kv, index=D.index))
        out = L87._thin_v(D, L87.IP, up).values[182:].tolist()
        monkeypatch.undo()
        return out
    K = np.r_[np.full(60, np.nan), np.full(122, 1.0), [0.9, 0.8, 0.8, 1.0, 1.0, 0.7, 2.0, 2.0]]   # rows 182..189
    rise = np.exp(np.linspace(0, 1, L)); fall = 1 / rise
    assert run(K, rise, True) == [True, False, False, False, False, True, False, False]
    assert run(K, fall, False) == [True, False, False, False, False, True, False, False]
    assert not any(run(K, rise, False)) and not any(run(K, fall, True))
    K2 = K.copy(); K2[182] = 1.0                                               # equal to the 120-day low is not lower
    assert run(K2, rise, True)[:2] == [False, True]
    eq = rise.copy(); eq[182] = eq[122]                                         # R exactly 0
    assert run(K, eq, True)[0] is False and run(K, eq, False)[0] is False
    base = rise.copy(); base[123] = 5.0                                         # only day j-60 is the base of R
    assert run(K, base, True)[0] is True
    fb = fall.copy(); fb[123] = 0.01
    assert run(K, fb, False)[0] is True
    gap = pd.date_range('2022-01-01', periods=L + 1, freq='D').delete(1)       # day 1 missing: row 182 = day 183, j-181 = 2
    D = pd.DataFrame(dict(h=rise, l=rise, c=rise, v=1.0), index=gap)
    monkeypatch.setattr(L87, '_kurt', lambda D, m: pd.Series(np.r_[np.full(60, np.nan), np.full(121, 1.0), [0.9, 1.0, 0.8],
                                                                    np.full(6, 1.0)], index=D.index))
    # row 181 is day 182: j-181 = day 1 missing -> no signal; row 183 (day 184) is complete
    assert L87._thin_v(D, L87.IP, True).values[181:184].tolist() == [False, False, True]


def test_v87_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l87_thin_tail_uptrend_4h', True, (1,)), ('s87_thin_tail_downtrend_4h', False, (-1,))):
        coin = _V87_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L87._thin(d, L87.IP, up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        assert got.any(), key
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=150)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L87.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import longscout_v88 as L88
L88.register()

_V88_COIN = {'l88_low_idio_vol_uptrend_4h': lambda: _ms(_mk(2600, '4h', 45)),
             's88_high_idio_vol_downtrend_4h': lambda: _ms(_mk(2600, '4h', 45))}


@pytest.mark.parametrize('key', sorted(L88.CANDIDATES))
def test_v88_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V88_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l88': le.sum() and not se.any(), 's88': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v88_ref(D, J, up, m=60, n=120):
    days = list(D.index); c = [float(x) for x in D.c]; b = [float(x) for x in J.b]; L = len(c)
    rc = [None] * L; rb = [None] * L
    for t in range(1, L):
        if days[t] - days[t - 1] == pd.Timedelta(days=1) and not (math.isnan(b[t]) or math.isnan(b[t - 1])):
            rc[t] = c[t] / c[t - 1] - 1; rb[t] = b[t] / b[t - 1] - 1
    IV = [None] * L
    for t in range(m, L):
        x = rc[t - m + 1:t + 1]; y = rb[t - m + 1:t + 1]
        if None in x or None in y:
            continue
        mx, my = sum(x) / m, sum(y) / m
        vx = sum((a - mx) ** 2 for a in x) / (m - 1); vy = sum((a - my) ** 2 for a in y) / (m - 1)
        cxy = sum((a - mx) * (q - my) for a, q in zip(x, y)) / (m - 1)
        IV[t] = math.sqrt(max(0.0, vx - cxy ** 2 / vy))

    def ext(t):
        w = IV[t - n:t]
        if IV[t] is None or None in w:
            return False
        return IV[t] < min(w) if up else IV[t] > max(w)

    res = np.zeros(L, bool)
    for j in range(m + n + 1, L):
        if days[j] - days[j - (m + n + 1)] != pd.Timedelta(days=m + n + 1):
            continue
        R = math.log(c[j] / c[j - m])
        res[j] = ext(j) and not ext(j - 1) and (R > 0 if up else R < 0)
    return res


def test_v88_flags_match_an_independent_loop_and_prefixes():
    btc = _ms(_mk(2600, '4h', 52)); L.set_btc(btc)
    hits = {}
    for seed in (45, 44, 41, 46):
        coin = _ms(_mk(2600, '4h', seed)); d = S.indicators(coin)
        D, J = L33._joint(d)
        for up in (True, False):
            v = L88._idio_v(D, J, L88.IP, up).values
            np.testing.assert_array_equal(v, _v88_ref(D, J, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 45:
                for cut in range(190, len(D), 37):
                    np.testing.assert_array_equal(L88._idio_v(D.iloc[:cut], J.iloc[:cut], L88.IP, up).values, v[:cut])
                t = pd.to_datetime(coin.t).dt.normalize()
                gc = coin[t != t.iloc[len(coin) // 2]].reset_index(drop=True)
                Dg, Jg = L33._joint(S.indicators(gc))
                assert len(Dg) == len(D) - 1
                np.testing.assert_array_equal(L88._idio_v(Dg, Jg, L88.IP, up).values, _v88_ref(Dg, Jg, up))
    assert all(hits.values()), hits


def test_v88_residual_vol_definition():
    idx = pd.date_range('2022-01-01', periods=200, freq='D')
    rng = np.random.default_rng(7)
    rb = rng.normal(0, 0.03, 200); e = rng.normal(0, 0.01, 200)
    J = pd.DataFrame(dict(rc=1.5 * rb + e, rb=rb), index=idx)
    iv = L88._iv(J, 60)
    k = 120; x = J.rc.values[k - 59:k + 1]; y = J.rb.values[k - 59:k + 1]
    X = np.vstack([np.ones(60), y]).T
    res = x - X @ np.linalg.lstsq(X, x, rcond=None)[0]
    assert abs(iv.iloc[k] - np.sqrt(res @ res / 59)) < 1e-12               # sd of the OLS residual (n - 1 divisor)
    J2 = pd.DataFrame(dict(rc=2.0 * rb, rb=rb), index=idx)                    # perfectly explained by BTC: zero, not NaN
    assert np.nanmax(L88._iv(J2, 60).values[59:]) < 1e-9 and not L88._iv(J2, 60).iloc[59:].isna().any()


def test_v88_boundaries(monkeypatch):
    L_ = 190
    idx = pd.date_range('2022-01-01', periods=L_, freq='D')
    def run(IV, c, up):
        D = pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=idx)
        monkeypatch.setattr(L88, '_iv', lambda J, m: pd.Series(IV, index=D.index))
        out = L88._idio_v(D, D, L88.IP, up).values[182:].tolist()
        monkeypatch.undo()
        return out
    lo = np.r_[np.full(60, np.nan), np.full(122, 1.0), [0.9, 0.8, 0.8, 1.0, 1.0, 0.7, 2.0, 2.0]]
    hi = np.r_[np.full(60, np.nan), np.full(122, 1.0), [1.1, 1.2, 1.2, 1.0, 1.0, 1.3, 0.5, 0.5]]
    rise = np.exp(np.linspace(0, 1, L_)); fall = 1 / rise
    assert run(lo, rise, True) == [True, False, False, False, False, True, False, False]
    assert run(hi, fall, False) == [True, False, False, False, False, True, False, False]
    assert not any(run(lo, fall, True)) and not any(run(hi, rise, False)) and not any(run(hi, fall, True))
    lo2 = lo.copy(); lo2[182] = 1.0; hi2 = hi.copy(); hi2[182] = 1.0          # equal to the extreme is not beyond it
    assert run(lo2, rise, True)[:2] == [False, True] and run(hi2, fall, False)[:2] == [False, True]
    eq = rise.copy(); eq[182] = eq[122]; eqf = fall.copy(); eqf[182] = eqf[122]
    assert run(lo, eq, True)[0] is False and run(hi, eqf, False)[0] is False  # R exactly 0
    base = rise.copy(); base[123] = 5.0; fb = fall.copy(); fb[123] = 0.01      # only day j-60 is the base of R
    assert run(lo, base, True)[0] is True and run(hi, fb, False)[0] is True
    gap = pd.date_range('2022-01-01', periods=L_ + 1, freq='D').delete(1)
    D = pd.DataFrame(dict(h=rise, l=rise, c=rise, v=1.0), index=gap)
    monkeypatch.setattr(L88, '_iv', lambda J, m: pd.Series(np.r_[np.full(60, np.nan), np.full(121, 1.0), [0.9, 1.0, 0.8],
                                                                 np.full(6, 1.0)], index=D.index))
    # row 181 is day 182: j-181 = day 1 missing -> no signal; row 183 (day 184) is complete
    assert L88._idio_v(D, D, L88.IP, True).values[181:184].tolist() == [False, False, True]


def test_v88_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l88_low_idio_vol_uptrend_4h', True, (1,)), ('s88_high_idio_vol_downtrend_4h', False, (-1,))):
        coin = _V88_COIN[key](); L.set_btc(btc); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L88._idio(d, L88.IP, up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        assert got.any(), key
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=150)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
            tb = pd.to_datetime(btc.t).dt.normalize()
            bcut = btc[tb != t.iloc[i] - pd.Timedelta(days=30)].reset_index(drop=True)
            g = _sig(key, coin, bcut)                                         # a missing BTC day inside the 60-day window
            assert not (np.asarray(g['le'])[i] or np.asarray(g['se'])[i]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L88.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()


import longscout_v89 as L89
L89.register()

_V89_COIN = {'l89_broad_volume_uptrend_4h': lambda: _ms(_mk(2600, '4h', 49)),
             's89_broad_volume_downtrend_4h': lambda: _ms(_mk(2600, '4h', 49))}


@pytest.mark.parametrize('key', sorted(L89.CANDIDATES))
def test_v89_cut_right_after_signals_and_prefixes_on_ms_timestamps(key):
    coin, btc = _V89_COIN[key](), _ms(_mk(2600, '4h', 52))
    full = _sig(key, coin, btc)
    le, se = np.asarray(full['le']), np.asarray(full['se'])
    assert {'l89': le.sum() and not se.any(), 's89': se.sum() and not le.any()}[key[:3]]
    ent = np.flatnonzero(le | se)
    bars = sorted(set(ent - 1) | set(ent) | {1300, 2563, 2599} | set(range(2500, 2600, 5)))
    for i in bars:
        part = _sig(key, coin.iloc[:i + 1].reset_index(drop=True), btc.iloc[:i + 1].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert (part[k] == full[k][:i + 1]).all(), (key, k, i)


def _v89_ref(D, up, m=30, n=120):
    days = list(D.index); c = [float(x) for x in D.c]; v = [float(x) for x in D.v]; L = len(c)
    H = [None] * L
    for t in range(m - 1, L):
        q = [c[s] * v[s] for s in range(t - m + 1, t + 1)]; T = sum(q)
        H[t] = sum((x / T) ** 2 for x in q)

    def low(t):
        return H[t] < min(H[t - n:t])

    res = np.zeros(L, bool)
    for j in range(m + n, L):
        if days[j] - days[j - (m + n)] != pd.Timedelta(days=m + n):
            continue
        R = math.log(c[j] / c[j - m])
        res[j] = low(j) and not low(j - 1) and (R > 0 if up else R < 0)
    return res


def test_v89_flags_match_an_independent_loop_and_prefixes():
    hits = {}
    for seed in (49, 45, 47, 51):
        D = L27._days_v(S.indicators(_ms(_mk(2600, '4h', seed))))
        for up in (True, False):
            v = L89._broad_v(D, L89.IP, up).values
            np.testing.assert_array_equal(v, _v89_ref(D, up), err_msg=str((seed, up)))
            hits[up] = hits.get(up, 0) + int(v.sum())
            if seed == 49:
                for cut in range(160, len(D), 37):
                    np.testing.assert_array_equal(L89._broad_v(D.iloc[:cut], L89.IP, up).values, v[:cut])
                gap = D.drop(D.index[len(D) // 2])
                np.testing.assert_array_equal(L89._broad_v(gap, L89.IP, up).values, _v89_ref(gap, up))
    assert all(hits.values()), hits


def test_v89_hhi_definition():
    idx = pd.date_range('2022-01-01', periods=40, freq='D')
    D = pd.DataFrame(dict(h=2.0, l=2.0, c=2.0, v=5.0), index=idx)
    assert np.allclose(L89._hhi(D, 30).values[29:], 1 / 30)                   # equal days: the minimum 1/30
    D2 = D.copy(); D2.loc[idx[35], 'v'] = 5.0 * 1e9                           # one day dominates: close to 1
    assert L89._hhi(D2, 30).iloc[35] > 0.99
    D3 = D.copy(); D3.loc[idx[35], 'c'] = 4.0                                 # dollar volume, not raw volume
    assert L89._hhi(D3, 30).iloc[35] > 1 / 30 + 1e-6


def test_v89_boundaries(monkeypatch):
    L_ = 158
    idx = pd.date_range('2022-01-01', periods=L_, freq='D')
    def run(H, c, up):
        D = pd.DataFrame(dict(h=c, l=c, c=c, v=1.0), index=idx)
        monkeypatch.setattr(L89, '_hhi', lambda D, m: pd.Series(H, index=D.index))
        out = L89._broad_v(D, L89.IP, up).values[150:].tolist()
        monkeypatch.undo()
        return out
    H = np.r_[np.full(29, np.nan), np.full(121, 1.0), [0.9, 0.8, 0.8, 1.0, 1.0, 0.7, 2.0, 2.0]]   # rows 150..157
    rise = np.exp(np.linspace(0, 1, L_)); fall = 1 / rise
    assert run(H, rise, True) == [True, False, False, False, False, True, False, False]
    assert run(H, fall, False) == [True, False, False, False, False, True, False, False]
    assert not any(run(H, rise, False)) and not any(run(H, fall, True))
    H2 = H.copy(); H2[150] = 1.0
    assert run(H2, rise, True)[:2] == [False, True]
    eq = rise.copy(); eq[150] = eq[120]; eqf = fall.copy(); eqf[150] = eqf[120]
    assert run(H, eq, True)[0] is False and run(H, eqf, False)[0] is False   # R exactly 0
    base = rise.copy(); base[121] = 5.0; fb = fall.copy(); fb[121] = 0.01      # only day j-30 is the base of R
    assert run(H, base, True)[0] is True and run(H, fb, False)[0] is True
    Hx = H.copy(); Hx[30] = 0.85                                              # day j-120 is still inside the 120-day window
    assert run(Hx, rise, True)[0] is False
    gap = pd.date_range('2022-01-01', periods=L_ + 1, freq='D').delete(2)
    D = pd.DataFrame(dict(h=rise, l=rise, c=rise, v=1.0), index=gap)
    monkeypatch.setattr(L89, '_hhi', lambda D, m: pd.Series(np.r_[np.full(29, np.nan), np.full(122, 1.0), [0.9, 1.0, 0.8],
                                                                  np.full(4, 1.0)], index=D.index))
    # row 151 is day 152: j-150 = day 2 missing -> no signal; row 153 (day 154) has days 4..154 complete
    assert L89._broad_v(D, L89.IP, True).values[151:154].tolist() == [False, False, True]


def test_v89_regime_gate_and_hour_zero(monkeypatch):
    btc = _ms(_mk(2600, '4h', 52))
    for key, up, ok in (('l89_broad_volume_uptrend_4h', True, (1,)), ('s89_broad_volume_downtrend_4h', False, (-1,))):
        coin = _V89_COIN[key](); d = S.indicators(coin); cr = L.coin_regime(d)
        f = np.asarray(L89._broad(d, L89.IP, up))
        full = _sig(key, coin, btc); got = np.asarray(full['le']) | np.asarray(full['se'])
        assert got.any(), key
        np.testing.assert_array_equal(got, f & np.isin(cr, ok), err_msg=key)
        t = pd.to_datetime(coin.t).dt.normalize()
        for i in np.flatnonzero(got)[:15]:
            assert pd.Timestamp(coin.t.iloc[i]).hour == 0
            cut = coin[t != t.iloc[i] - pd.Timedelta(days=100)].reset_index(drop=True)
            k0 = np.flatnonzero(pd.to_datetime(cut.t).values == coin.t.values[i])[0]
            g = _sig(key, cut, btc)
            assert not (np.asarray(g['le'])[k0] or np.asarray(g['se'])[k0]), key
        for forced in (1.0, 0.0, -1.0):
            monkeypatch.setattr(L89.L1, 'coin_regime', lambda d, v=forced: np.full(len(d), v))
            g = _sig(key, coin, btc)
            assert (np.asarray(g['le']) | np.asarray(g['se'])).any() == (forced in ok), (key, forced)
            monkeypatch.undo()
