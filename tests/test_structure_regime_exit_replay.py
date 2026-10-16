"""tools/research/structure_regime_exit_replay.py - arm B (runner + declared regime exit) for exp_structure.

Synthetic only (the 15m tapes + temp git repo fixtures of test_breadth_candidate_runner); no real cache is read."""
import json
import os
import sys

import numpy as np
import pytest
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


# ------------------------------------------------------------------ follow-ups: fixed-R report, BTC packet
import structure_fixed_r_report as FR                                                       # noqa: E402
import structure_btc_packet as BP                                                           # noqa: E402


def _pkt(rs, regimes=None, t0='2024-12-30T00:00:00Z'):
    base = pd.Timestamp(t0.rstrip('Z'))
    tr = [dict(symbol='S', t_in=(base + pd.Timedelta(days=k)).isoformat() + 'Z',
               t_out=(base + pd.Timedelta(days=k, hours=1)).isoformat() + 'Z', R=r,
               regime=(regimes or ['btc_above_ema200'] * len(rs))[k]) for k, r in enumerate(rs)]
    return dict(trades=tr, packet_sha256='x', account={}, side='long', bust={'busted': False})


def test_fixed_r_report_totals_quarters_runs_and_parity():
    rs = [1.0, -1.0, -1.0, -1.0, 2.0, -1.0]
    rep = FR.report(_pkt(rs, ['btc_above_ema200'] * 3 + ['btc_below_ema200'] * 3), _pkt(rs[:4] + [5.0]))
    t = rep['totals']
    assert t['trades'] == 6 and t['net_r'] == -1.0 and t['pf'] == 3 / 4 and t['max_dd_r'] == 3.0
    assert rep['longest_losing_run']['n'] == 3 and rep['longest_losing_run']['r'] == -3.0
    assert rep['quarters'] == {'2024Q4': dict(trades=2, net_r=0.0), '2025Q1': dict(trades=4, net_r=-1.0)}
    assert rep['regime']['btc_below_ema200'] == dict(trades=3, net_r=0.0) and rep['positive_buckets'] == []
    assert rep['parity_vs_ref']['common'] == 5 and rep['parity_vs_ref']['exact_1e9'] == 4


def test_btc_packet_net_recomputes_by_hand_and_matches_runner(env, tmp_path):
    cache = BT_.write15(str(tmp_path / 'c15'), {'BTCUSDT': BT_.tape15(7, 100.0)})
    d = str(tmp_path / 'pk')
    for side in ('long', 'short'):
        pkt, rows = BP.run(side=side, out_dir=d, write_slice=True, cache=cache, regime_cache=env['cache'],
                           window=BT_.VT.WINDOW, repo=env['repo'])
        ref = B.run(cache=cache, regime_cache=env['cache'], window=BT_.VT.WINDOW, symbols=['BTCUSDT'], repo=env['repo'],
                    key=KEY, side=side, out=str(tmp_path / f'ref_{side}.json'))
        assert rows and pkt['trades'] == ref['trades']
        for r in rows:
            sd = r['side']
            assert abs(r['net'] - r['net_recomputed']) <= 1e-6        # the packet rounds $ to 6 decimals
            assert r['entry_fill'] == pytest.approx(r['entry_open'] * (1 + sd * BT.SLIP), rel=1e-12)
            assert r['R'] == pytest.approx(r['net'] / r['risk_usd'], rel=1e-6, abs=1e-6)
            assert r['gross_before_slip'] - r['slip_usd'] == pytest.approx(r['gross'], rel=1e-9, abs=1e-9)
    sl = pd.read_csv(os.path.join(d, 'btc_15m_slice.csv'))
    assert sl.t.max() < B._ms(np.array([np.datetime64(BT_.VT.WINDOW.split('/')[1].rstrip('Z'))]))[0]


def test_decomposition_identity_and_side_purity(env, tmp_path):
    cache = BT_.write15(str(tmp_path / 'c15'), {'BTCUSDT': BT_.tape15(7, 100.0)})
    for side, sd in (('long', 1), ('short', -1)):
        _, rows = BP.run(side=side, out_dir=str(tmp_path / 'pk'), cache=cache, regime_cache=env['cache'],
                         window=BT_.VT.WINDOW, repo=env['repo'])
        d = BP.decompose(rows)
        assert d['sides'] == [sd] and d['trades'] == len(rows)
        assert abs(d['gross_before_slip_r'] - d['slip_r'] - d['fee_r'] - d['funding_r'] - d['net_r']) <= 1e-6
        assert abs(d['net_r'] - d['net_r_runner']) <= 1e-5 * len(rows)
        assert d['slip_r'] > 0 and d['fee_r'] > 0 and d['funding_r'] > 0 and d['median_stop_bps'] > 0
    bad = [dict(rows[0], net_recomputed=float(rows[0]['net_recomputed']) + 1.0)]
    with pytest.raises(AssertionError, match='identity'):
        BP.decompose(bad)
