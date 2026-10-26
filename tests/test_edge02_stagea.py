"""Focused Stage-A tests: research isolation, literal frozen gates, causality, and re-entry lock."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))

import backtest as BT                                                               # noqa: E402
import edge02_signals as E2                                                        # noqa: E402
import strategies as S                                                            # noqa: E402


def candles(n=700, seed=23):
    rng = np.random.default_rng(seed)
    drift = np.r_[np.full(n // 2, 0.001), np.full(n - n // 2, -0.001)]
    c = 100 * np.exp(np.cumsum(drift + 0.012 * np.sin(np.arange(n) / 4) + rng.normal(0, 0.003, n)))
    o = np.r_[c[0], c[:-1]]
    w = c * (0.004 + rng.random(n) * 0.002)
    v = 1000 * (1 + rng.random(n))
    return pd.DataFrame(dict(t=pd.date_range('2025-01-01', periods=n, freq='15min'), o=o,
                             h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c, v=v, qv=c * v))


def context(d, btc_c=None):
    btc_c = np.asarray(d.c if btc_c is None else btc_c, float)
    return dict(btc_down=np.zeros(len(d), bool), rank=pd.Series(999.0, index=d.index), btc_c=btc_c,
                rank7=pd.Series(999.0, index=d.index), rank7_n=pd.Series(1.0, index=d.index), n_symbols=1)


def test_research_keys_never_enter_runtime_registry_and_thresholds_are_frozen():
    assert set(E2.CANDIDATES).isdisjoint(S.STRATEGIES)
    assert E2.RSI2_PRIMARY == 10.0 and E2.RSI2_NEIGHBOURS == (5.0, 15.0)
    assert E2.SQUEEZE_PRIMARY == 0.20 and E2.SQUEEZE_NEIGHBOURS == (0.15, 0.25)
    assert E2.COST_X == 4.0
    for key, st in E2.CANDIDATES.items():
        assert st['research_only'] is True and st['tf'] == '15m'
        expect = {'long': 'range', 'short': 'range'} if key == 'btc_residual_mr.v1' else {'long': 'bull', 'short': 'bear'}
        assert st['when_by_side'] == expect
    r = E2.CANDIDATES['btc_residual_mr.v1']
    assert r['params'] == dict(beta_n=1920, beta_min=1536, q_n=32, z_n=1920, z_min=1920,
                               entry_z=2.0, invalid_z=3.5, cost_x=5.0, stop_atr=1.5)
    assert r['reentry_lock'] == 8 and r['warmup_bars'] == 3520 and r['benchmark'] == 'BTCUSDT'
    assert r['mgmt'] == {'stop_atr': 1.5, 'max_bars': 16}
    assert E2.RESIDUAL_ENTRY_NEIGHBOURS == (1.5, 2.5)


def test_structure_cost_ablation_removes_only_cost_gate():
    d = S.indicators(candles())
    base = E2._structure(d)
    vol = E2._volume_ok(d, E2.STRUCTURE['volume_n'], E2.STRUCTURE['volume_x'])
    cost = E2._cost_ok(d, E2.STRUCTURE['stop_atr'], E2.STRUCTURE['cost_x'])
    full = E2.signals('structure_cf.v1', d, context(d))
    abl = E2.signals('structure_cf.cost_ablation.v1', d, context(d))
    assert np.array_equal(full['le'], (base['le'] & vol & cost).fillna(False).to_numpy(bool))
    assert np.array_equal(full['se'], (base['se'] & vol & cost).fillna(False).to_numpy(bool))
    assert np.array_equal(abl['le'], (base['le'] & vol).fillna(False).to_numpy(bool))
    assert np.array_equal(abl['se'], (base['se'] & vol).fillna(False).to_numpy(bool))
    assert np.array_equal(full['lx'], abl['lx']) and np.array_equal(full['sx'], abl['sx'])


def test_rsi2_and_squeeze_primary_rules_are_literal():
    d = S.indicators(candles())
    ctx = context(d)
    r = E2.signals('rsi2_reclaim.v1', d, ctx)
    rg = E2._cost_ok(d, E2.RSI2['stop_atr'], E2.RSI2['cost_x'])
    assert np.array_equal(r['le'], ((d.e50 > d.e200) & (d.c > d.e20) &
                                    (d.rsi2.shift() <= 10) & (d.rsi2 > 10) &
                                    (d.c > d.h.shift()) & rg)
                          .fillna(False).to_numpy(bool))
    assert np.array_equal(r['se'], ((d.e50 < d.e200) & (d.c < d.e20) &
                                    (d.rsi2.shift() >= 90) & (d.rsi2 < 90) &
                                    (d.c < d.l.shift()) & rg)
                          .fillna(False).to_numpy(bool))

    q = E2.signals('squeeze_release.v1', d, ctx)
    hi, lo = d.h.rolling(20).max().shift(), d.l.rolling(20).min().shift()
    qg = (E2._volume_ok(d, 20, 1.25) & E2._cost_ok(d, 1.5, 4.0))
    assert np.array_equal(q['le'], ((d.bbw_pct.shift() <= .20) & (d.e50 > d.e200) &
                                    (d.c > hi) & (d.c.shift() <= hi.shift()) & qg)
                          .fillna(False).to_numpy(bool))
    assert np.array_equal(q['se'], ((d.bbw_pct.shift() <= .20) & (d.e50 < d.e200) &
                                    (d.c < lo) & (d.c.shift() >= lo.shift()) & qg)
                          .fillna(False).to_numpy(bool))


def test_frozen_management_and_volume_thresholds_match_contract():
    assert E2.STRUCTURE['volume_x'] == 1.25
    assert E2.SQUEEZE['volume_x'] == 1.25
    assert E2.CANDIDATES['rsi2_reclaim.v1']['mgmt']['max_bars'] == 12
    assert E2.CANDIDATES['squeeze_release.v1']['mgmt']['max_bars'] == 16


def test_all_stagea_signals_are_prefix_causal():
    raw = candles()
    for key in E2.CANDIDATES:
        d = S.indicators(raw.copy())
        full = E2.signals(key, d, context(d))
        for end in (260, 410, 590):
            cut = S.indicators(raw.iloc[:end].copy())
            got = E2.signals(key, cut, context(cut))
            for col in ('le', 'se', 'lx', 'sx'):
                assert np.array_equal(got[col], full[col][:end]), (key, col, end)


def test_residual_state_uses_zero_intercept_shifted_samples_and_is_prefix_causal():
    n = 4200
    rng = np.random.default_rng(701)
    xb = rng.normal(0.0001, 0.004, n)
    noise = rng.normal(0, 0.002, n)
    btc = 30000 * np.exp(np.cumsum(xb))
    raw = candles(n=n, seed=702)
    raw['c'] = 100 * np.exp(np.cumsum(1.4 * xb + noise))
    raw['o'] = raw.c.shift(fill_value=raw.c.iloc[0])
    raw['h'] = np.maximum(raw.o, raw.c) * 1.01
    raw['l'] = np.minimum(raw.o, raw.c) * .99
    d = S.indicators(raw)
    st = E2.residual_state(d, E2.RESIDUAL, context(d, btc))

    x, y = pd.Series(np.log(btc)).diff(), np.log(d.c).diff()
    i = 3600
    xs, ys = x.iloc[i - 1920:i], y.iloc[i - 1920:i]
    expect_beta = float((xs * ys).sum() / xs.pow(2).sum())
    assert st.beta.iloc[i] == pytest.approx(expect_beta)
    assert st.residual.iloc[i] == pytest.approx(y.iloc[i] - expect_beta * x.iloc[i])

    end = 3900
    cut = S.indicators(raw.iloc[:end].copy())
    got = E2.residual_state(cut, E2.RESIDUAL, context(cut, btc[:end]))
    pd.testing.assert_frame_equal(got, st.iloc[:end])


def test_residual_future_btc_cannot_change_past_and_context_must_align():
    n = 4200
    raw = candles(n=n, seed=811)
    btc = 30000 * np.exp(np.cumsum(np.random.default_rng(812).normal(0, .003, n)))
    d = S.indicators(raw)
    base = E2.signals('btc_residual_mr.v1', d, context(d, btc))
    changed = btc.copy(); changed[3900:] *= np.linspace(1, 4, n - 3900)
    alt = E2.signals('btc_residual_mr.v1', d, context(d, changed))
    for col in ('le', 'se', 'lx', 'sx'):
        assert np.array_equal(base[col][:3900], alt[col][:3900])
    with pytest.raises(ValueError, match='synchronized'):
        E2.residual_state(d, E2.RESIDUAL, context(d, btc[:-1]))


def test_residual_entry_exit_invalidation_and_cost_floor_are_literal(monkeypatch):
    d = S.indicators(candles(260))
    z = pd.Series(np.nan, index=d.index)
    z.iloc[230:242] = [-2.2, -1.9, -0.1, 0.1, 2.2, 1.9, 0.1, -0.1, -3.6, -3.0, 0.5, 3.6]
    state = pd.DataFrame({'beta': 1.0, 'residual': 0.0, 'q': 0.0, 'z': z}, index=d.index)
    monkeypatch.setattr(E2, 'residual_state', lambda *_: state)
    s = E2.sig_btc_residual_mr(d, E2.RESIDUAL, context(d))
    gate = E2._cost_ok(d, 1.5, 5.0)
    assert s['le'].iloc[231] == gate.iloc[231] and s['se'].iloc[235] == gate.iloc[235]
    assert s['lx'].iloc[233] and s['sx'].iloc[237]                  # directional zero crossings
    assert s['lx'].iloc[238] and not s['sx'].iloc[238]             # long adverse only at z <= -3.5
    assert s['sx'].iloc[241] and not s['lx'].iloc[241]             # short adverse only at z >= +3.5


def test_opt_in_backtest_reentry_lock_blocks_same_decision_bar(monkeypatch):
    def sig(d, p, ctx):
        yes = pd.Series(True, index=d.index)
        no = pd.Series(False, index=d.index)
        return dict(le=yes, se=no, lx=yes, sx=no)

    key = '_edge02_reentry_probe'
    monkeypatch.setitem(S.STRATEGIES, key, dict(name='probe', style='balanced', fn=sig, sides='long',
                                                tf='15m', mgmt=dict(stop_atr=100.0)))
    raw = candles(280)
    book = BT.Book({'BTCUSDT': raw})

    def run(lock):
        sl = dict(key=key, id='S1', share=1.0, risk=.1, max_pos=1, sides='long', symbols=['BTCUSDT'],
                  mgmt={}, when='any', reentry_lock=lock)
        return BT.run(book, [sl], start=10_000, max_lev=1, daily_halt=1, warmup=220, maint_margin=0)[0]

    unlocked, locked = run(False), run(True)
    assert len(unlocked) > len(locked) > 0
    assert (locked.i_in.diff().dropna() >= 2).all()
    cooldown = run(8)
    assert len(locked) > len(cooldown) > 0
    assert (cooldown.i_in.diff().dropna() >= 10).all()
