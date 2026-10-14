"""Lean acceptance tests for the experimental confirmed-structure strategy."""
import numpy as np
import pandas as pd

import strategies as S


def candles(n=520, seed=7):
    rng = np.random.default_rng(seed)
    regime = np.r_[np.full(n // 2, .0015), np.full(n - n // 2, -.0015)]
    c = 100 * np.exp(np.cumsum(regime + .012 * np.sin(np.arange(n) / 3) + rng.normal(0, .003, n)))
    o = np.r_[c[0], c[:-1]]
    w = c * .003
    return pd.DataFrame(dict(t=pd.date_range('2025-01-01', periods=n, freq='15min'), o=o,
                             h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c, v=1000.0))


def signals(raw):
    d = S.indicators(raw)
    ctx = dict(btc_down=np.zeros(len(d), bool), rank=pd.Series(999., index=d.index),
               rank7=pd.Series(999., index=d.index), n_symbols=1)
    return d, S.signals('exp_structure', d, ctx, mask_sides=False)


def test_registry_is_bounded_testnet_candidate():
    st = S.STRATEGIES['exp_structure']
    assert st['sides'] == 'both' and st['tf'] == '15m' and S.EXP_TAG in st['desc']
    assert st['mgmt'] == dict(stop_atr=1.0, tp_r=2.0, max_bars=24)


def test_both_sides_no_contradiction_and_stop_contract():
    _, s = signals(candles())
    assert s['le'].sum() > 0 and s['se'].sum() > 0
    assert not (s['le'] & s['se']).any()
    assert not (s['le'] & s['lx']).any() and not (s['se'] & s['sx']).any()


def test_confirmed_pivot_rule_is_literal():
    d, s = signals(candles())
    ph = d.h.shift(1).where((d.h.shift(1) > d.h.shift(2)) & (d.h.shift(1) >= d.h)).ffill()
    pl = d.l.shift(1).where((d.l.shift(1) < d.l.shift(2)) & (d.l.shift(1) <= d.l)).ffill()
    expected_l = (d.e50 > d.e200) & (d.c > ph) & (d.c.shift() <= ph.shift())
    expected_s = (d.e50 < d.e200) & (d.c < pl) & (d.c.shift() >= pl.shift())
    assert (s['le'] == expected_l.fillna(False)).all()
    assert (s['se'] == expected_s.fillna(False)).all()


def test_future_candles_never_rewrite_prior_signals():
    raw = candles()
    _, full = signals(raw)
    for end in (240, 320, 410, 500):
        _, cut = signals(raw.iloc[:end].copy())
        for key in ('le', 'se', 'lx', 'sx'):
            assert (cut[key] == full[key][:end]).all(), (key, end)
