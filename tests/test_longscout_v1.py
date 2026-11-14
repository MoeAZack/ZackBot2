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
