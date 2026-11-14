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
