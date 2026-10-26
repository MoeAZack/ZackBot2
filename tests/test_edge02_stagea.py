"""Focused Stage-A tests: research isolation, literal frozen gates, causality, and re-entry lock."""
import os
import sys

import numpy as np
import pandas as pd

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


def context(d):
    return dict(btc_down=np.zeros(len(d), bool), rank=pd.Series(999.0, index=d.index),
                rank7=pd.Series(999.0, index=d.index), rank7_n=pd.Series(1.0, index=d.index), n_symbols=1)


def test_research_keys_never_enter_runtime_registry_and_thresholds_are_frozen():
    assert set(E2.CANDIDATES).isdisjoint(S.STRATEGIES)
    assert E2.RSI2_PRIMARY == 10.0 and E2.RSI2_NEIGHBOURS == (5.0, 15.0)
    assert E2.SQUEEZE_PRIMARY == 0.20 and E2.SQUEEZE_NEIGHBOURS == (0.15, 0.25)
    assert E2.COST_X == 4.0
    for st in E2.CANDIDATES.values():
        assert st['research_only'] is True and st['tf'] == '15m'
        assert st['when_by_side'] == {'long': 'bull', 'short': 'bear'}


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
