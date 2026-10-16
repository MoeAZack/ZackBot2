"""tools/research/structure_regime_exit_replay.py - arm B (runner + declared regime exit) for exp_structure.

Synthetic only (the 15m tapes + temp git repo fixtures of test_breadth_candidate_runner); no real cache is read."""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure_regime_exit_replay as X                                                    # noqa: E402
import run_breadth_candidate as B                                                           # noqa: E402
import test_breadth_candidate_runner as BT_                                                 # noqa: E402
import trade_audit as TA                                                                    # noqa: E402
import backtest as BT                                                                       # noqa: E402

env = BT_.env
KEY = 'exp_structure'


def c15(env, tmp_path):
    return BT_.write15(str(tmp_path / 'c15'), {s: BT_.tape15(i + 7, 100.0 * (i + 1))
                                                for i, s in enumerate(['BTCUSDT'] + BT_.VT.SYMS)})


def args(env, cache, out, side='long'):
    return dict(cache=cache, regime_cache=env['cache'], window=BT_.VT.WINDOW, symbols=BT_.VT.SYMS, repo=env['repo'],
                key=KEY, side=side, out=str(env['tmp'] / out))


def test_disabled_reproduces_runner_exactly(env, tmp_path):
    cache = c15(env, tmp_path)
    for side in ('long', 'short'):
        a = B.run(**args(env, cache, f'a_{side}.json', side))
        b, car = X.run(regime_exit=False, **args(env, cache, f'b0_{side}.json', side))
        assert a['trades'] and a['trades'] == b['trades'] and a['stats'] == b['stats']
        assert car['regime_only_exits'] == 0
        assert BT.Book is not None and BT.Book.__name__ == 'Book'           # the swap is undone


def test_enabled_adds_regime_exits_at_the_closed_candle(env, tmp_path):
    cache = c15(env, tmp_path)
    for side in ('long', 'short'):
        a = B.run(**args(env, cache, f'a_{side}.json', side))
        b, car = X.run(regime_exit=True, **args(env, cache, f'b_{side}.json', side))
        assert car['regime_only_exits'] > 0 and b['trades'] != a['trades']
        rx = [t for t in car['trades'] if t['regime_only_exit']]
        assert all(t['why'] == 'signal' and t['trend_against_at_exit'] for t in rx)


def test_regime_trend_matches_trade_audit_snapshot():
    rng = np.random.default_rng(3)
    d = pd.DataFrame({k: rng.normal(100, 1, 400) for k in ('c', 'e20', 'e50', 'e200')})
    d.loc[5, 'e200'] = np.nan
    down, up = X.regime_trend(d)
    for i in range(len(d)):
        r = d.iloc[i]
        snap = TA.regime_snapshot('S', '15m', '2025-01-01T00:00:00+00:00', 900, r.c, r.e20, r.e50, r.e200)
        assert down[i] == (snap['trend'] == 'down') and up[i] == (snap['trend'] == 'up')


def test_exit_is_causal_decided_and_filled_at_the_triggering_close(env, tmp_path):
    """Every signal exit is at the FIRST candle j >= entry whose closed-candle state (own exit or trend against) says
    exit - never earlier (no look-ahead), never skipped; and a candle's trend uses only candles <= it."""
    cache = c15(env, tmp_path)
    kw = args(env, cache, 'b.json', 'long')
    b, car = X.run(regime_exit=True, **kw)
    book = X.LAST_BOOK
    idx = {B._utc(t): j for j, t in enumerate(B._ms(book.t))}
    n_rx = 0
    for tr, rec in zip(b['trades'], car['trades']):
        if tr['why'] != 'signal':
            continue
        i_in, i_out = idx[tr['t_in']], idx[tr['t_out']]
        down, _ = X.regime_trend(book.d[tr['symbol']])
        ex = book.own[(KEY, tr['symbol'], 'None')]['lx'] | down
        assert ex[i_out] and not ex[i_in:i_out].any()
        n_rx += rec['regime_only_exit']
    assert n_rx > 0
    import strategies as ST
    full = pd.read_csv(os.path.join(cache, 'ETHUSDT_15m.csv'))
    d1, u1 = X.regime_trend(ST.indicators(full))
    d2, u2 = X.regime_trend(ST.indicators(full.iloc[:len(full) // 2].reset_index(drop=True)))
    assert (d1[:len(d2)] == d2).all() and (u1[:len(u2)] == u2).all()
    with open(kw['out'] + '.regime_exit.json') as f:
        assert json.load(f)['script_sha256'] == car['script_sha256']
