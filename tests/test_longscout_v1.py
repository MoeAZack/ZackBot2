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
