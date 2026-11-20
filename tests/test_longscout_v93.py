"""Synthetic preregistration checks; no market data or economics."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v93 as M


def daily_frame(returns):
    prices = 100. * np.cumprod(1 + np.asarray(returns, dtype=float))
    rows = [(pd.Timestamp('2023-01-01') + pd.Timedelta(days=i, hours=h), c, c+1, c-1, c, 1.)
            for i, c in enumerate(prices) for h in range(0, 24, 4)]
    return pd.DataFrame(rows, columns=['t', 'o', 'h', 'l', 'c', 'v'])


def sample():
    rb = np.array([-.01 - (i % 4)*.002 if i % 3 == 0 else .006 for i in range(75)])
    rc = 1.5 * rb
    rb[65], rc[65] = -.03, -.06
    return daily_frame(rc), daily_frame(rb)


def reference_beta(J, p):
    result = []
    for i in range(len(J)):
        pairs = [(J.rb.iloc[j], J.rc.iloc[j]) for j in range(max(0, i-p['n']), i)
                 if pd.notna(J.rb.iloc[j]) and pd.notna(J.rc.iloc[j]) and J.rb.iloc[j] < 0]
        if i < p['n'] or len(pairs) < p['min_down']:
            result.append(np.nan)
            continue
        x, y = np.array(pairs).T
        denominator = sum((x - x.mean())**2)
        result.append(sum((x-x.mean())*(y-y.mean()))/denominator if denominator > 1e-20 else np.nan)
    return np.array(result)


def test_beta_independent_reference_boundaries():
    rng = np.random.default_rng(93)
    rb = rng.choice([-.03, -.02, -.01, .01], 80)
    rc = 1.6*rb + rng.normal(0, .002, len(rb))
    J = pd.DataFrame(dict(rb=rb, rc=rc))
    np.testing.assert_allclose(M.conditional_beta(J, M.IP).iloc[60:], reference_beta(J, M.IP)[60:], rtol=1e-10, atol=1e-10, equal_nan=True)
    J = pd.DataFrame(dict(rb=[-.01]*14 + [.01]*46 + [-.03], rc=[-.02]*14 + [.02]*46 + [-.06]))
    assert np.isnan(M.conditional_beta(J, M.IP).iloc[-1])  # 14 down days
    J.loc[14, ['rb', 'rc']] = [-.02, -.04]
    assert M.conditional_beta(J, M.IP).iloc[-1] == pytest.approx(2.)  # 15
    J.loc[:14, 'rb'] = -.01
    assert np.isnan(M.conditional_beta(J, M.IP).iloc[-1])  # zero variance


def test_complete_day_gaps_for_both_assets():
    coin, btc = sample()
    M.set_btc(btc)
    D, J = M.joint(coin)
    flags = M.downside_flags(D, J, M.IP)
    assert flags.loc[pd.Timestamp('2023-03-07')]
    for asset in ('coin', 'btc'):
        c, b = coin.copy(), btc.copy()
        if asset == 'coin':
            c = c.loc[~c.t.eq(pd.Timestamp('2023-02-10 12:00'))].reset_index(drop=True)
        else:
            b = b.loc[~b.t.eq(pd.Timestamp('2023-02-10 12:00'))].reset_index(drop=True)
        M.set_btc(b)
        D, J = M.joint(c)
        assert not M.downside_flags(D, J, M.IP).loc[pd.Timestamp('2023-03-07')], asset


def session_frame():
    d = daily_frame([0]*5)
    # Three sessions: unequivocal down/up and just below threshold.
    for day, move in [(1, -.031), (2, .031), (3, -.029)]:
        idx = d.index[d.t.eq(pd.Timestamp('2023-01-01') + pd.Timedelta(days=day, hours=8))][0]
        d.loc[idx, 'c'] = 100*np.exp(move)
    return d


def test_session_reference_directions_threshold_timing_and_gaps():
    d = session_frame()
    flags = M.session_flags(d, dict(move=.03))
    expected = {'long': [], 'short': []}
    for i, row in d.iterrows():
        valid = i >= 2 and row.t.hour == 8 and list(d.t.iloc[i-2:i+1]) == [row.t-pd.Timedelta(hours=8), row.t-pd.Timedelta(hours=4), row.t]
        move = np.log(row.c / d.o.iloc[i-2]) if valid else 0
        expected['long'].append(valid and move <= -.03)
        expected['short'].append(valid and move >= .03)
    for k in expected:
        np.testing.assert_array_equal(flags[k], expected[k])
    assert list(d.t[flags['long']]) == [pd.Timestamp('2023-01-02 08:00')]
    assert list(d.t[flags['short']]) == [pd.Timestamp('2023-01-03 08:00')]
    for missing in ['2023-01-02 00:00', '2023-01-02 04:00']:
        gap = d.loc[~d.t.eq(pd.Timestamp(missing))].reset_index(drop=True)
        assert not M.session_flags(gap, dict(move=.03))['long'].any()


@pytest.mark.parametrize('kind', ['downside', 'session'])
def test_full_signals_prefix_regime_and_exit(kind, monkeypatch):
    coin, btc = sample() if kind == 'downside' else (session_frame(), daily_frame([0]*5))
    fn, p = (M.sig_downside, M.IP) if kind == 'downside' else (M.sig_session, dict(move=.03))
    monkeypatch.setattr(M.L1, 'coin_regime', lambda d: np.zeros(len(d)))
    M.set_btc(btc)
    full = fn(coin, p, {})
    assert full['le'].any()
    if kind == 'downside':
        assert list(coin.t[full['le']]) == [pd.Timestamp('2023-03-08 00:00')]
    else:
        assert full['se'].any()
        for k in ('lx', 'sx'):
            np.testing.assert_array_equal(full[k], coin.t.dt.hour.eq(20))
        assert pd.Timestamp('2023-01-02 20:00') in set(coin.t[full['lx']])
    for end in ([390, 396, 397, 400, 430] if kind == 'downside' else [8, 9, 10, 15, 18, 22]):
        M.set_btc(btc.iloc[:end])
        part = fn(coin.iloc[:end], p, {})
        for k in full:
            np.testing.assert_array_equal(part[k], full[k].iloc[:end])
    M.set_btc(btc)
    for regime in (np.nan, -1., 1.):
        monkeypatch.setattr(M.L1, 'coin_regime', lambda d, r=regime: np.full(len(d), r))
        out = fn(coin, p, {})
        assert bool(out['le'].any()) == (kind == 'downside' and regime == 1.)
        assert not out['se'].any()


def test_default_off_opt_in_and_frozen_management(monkeypatch):
    assert 'longscout_v93' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for key in M.CANDIDATES:
        monkeypatch.delitem(M.S.STRATEGIES, key, raising=False)
    assert not any(k in M.S.STRATEGIES for k in M.CANDIDATES)
    M.register()
    for key, spec in M.CANDIDATES.items():
        assert M.S.STRATEGIES[key]['research_only'] is True
        assert spec['tf'] == '4h'
        monkeypatch.delitem(M.S.STRATEGIES, key)
    assert M.CANDIDATES['l93_downside_beta_rebound_4h']['mgmt'] == dict(stop_atr=2.5, tp_r=2., max_bars=30)
    assert M.CANDIDATES['r93_halfday_displacement_reversal_4h']['mgmt'] == dict(stop_atr=2., max_bars=6)



def test_duplicate_bar_cannot_replace_missing_coin_bar(monkeypatch):
    coin, btc = sample()
    gap = coin.t.eq(pd.Timestamp('2023-02-10 12:00'))
    coin.loc[gap, 't'] = pd.Timestamp('2023-02-10 08:00')
    M.set_btc(btc)
    monkeypatch.setattr(M.L1, 'coin_regime', lambda d: np.zeros(len(d)))
    signals = M.sig_downside(coin, M.IP, {})
    assert not signals['le'][coin.t.eq(pd.Timestamp('2023-03-08 00:00'))].iloc[0]


