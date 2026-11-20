"""Hostile mechanics tests for run_rbrwide (synthetic candles only; no market data, no economics)."""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_rbrwide as R

RULE = dict(step=0.001, min_qty=0.001, min_notional=5.0)
H = pd.Timedelta(hours=1)


def raw_panel(syms, n_h, seed=3, t0='2023-01-02 00:00'):
    rng = np.random.default_rng(seed)
    t = pd.date_range(t0, periods=n_h, freq='h')
    raw = {}
    for k, s in enumerate(syms):
        c = 100 * np.exp(np.cumsum(rng.normal(0.0002 * (k - 4), 0.012, n_h))); o = np.r_[c[0], c[:-1]]
        v = rng.uniform(0.5, 1.5, n_h) * 1e6
        a = dict(t=t.values, o=o, h=np.maximum(o, c) * 1.002, l=np.minimum(o, c) * 0.998, c=c,
                 elig=np.ones(n_h, bool), rule=RULE)
        a['v4'] = pd.Series(v, index=t).groupby(t.floor('4h')).sum()
        raw[s] = a
    return raw


def test_signals_match_frozen_build_context_on_contiguous_data():
    syms = ['BTCUSDT'] + [f'C{k}USDT' for k in range(9)]
    raw = raw_panel(syms, 4 * 1500)
    fr = R.frames4h(raw, syms)
    mine = R.signals(fr)
    ctx = R.SF.build_context({s: fr[s] for s in syms})
    fired = 0
    for s in syms:
        ref = R.SF.sig_rank_breadth_reversal_4h(fr[s], R.PARAMS, ctx[s])
        se = ref['se'].fillna(False).values.astype(bool); sx = ref['sx'].fillna(False).values.astype(bool)
        assert (mine[s][0] == se).all() and (mine[s][1] == sx).all(), s
        fired += se.sum()
    assert fired > 0


def test_volume_is_real_4h_sum():
    raw = raw_panel(['AUSDT'], 40)
    fr = R.frames4h(raw, ['AUSDT'])
    assert np.allclose(fr['AUSDT'].v.values, raw['AUSDT']['v4'].values[:len(fr['AUSDT'])])
    assert not np.allclose(fr['AUSDT'].v.values, 1.0)


def test_rank_return_is_time_based_no_compression():
    syms = [f'C{k}USDT' for k in range(9)]
    raw = raw_panel(syms, 4 * 300)
    a = raw['C0USDT']                                                 # drop 4h bins 100..101 (8 hours) for C0
    keep = np.r_[0:400, 408:len(a['t'])]
    for k in ('t', 'o', 'h', 'l', 'c', 'elig'): a[k] = a[k][keep]
    fr = R.frames4h(raw, syms)
    r7, r7n, b7 = R.cross_section(fr)
    t_after = pd.Timestamp(raw['C1USDT']['t'][0]) + pd.Timedelta(hours=4 * 102 + 4 * 41)   # its t-168h is a dropped bin
    assert np.isnan(r7.loc[t_after, 'C0USDT']) and r7n.loc[t_after] == 8
    t_ok = pd.Timestamp(raw['C1USDT']['t'][0]) + pd.Timedelta(hours=4 * 200)
    assert r7n.loc[t_ok] == 9


def _lot_data(n=200):
    t = pd.date_range('2023-01-02 00:00', periods=n, freq='h').values
    a = dict(t=t, o=np.full(n, 100.0), h=np.full(n, 100.2), l=np.full(n, 99.8), c=np.full(n, 100.0),
             elig=np.ones(n, bool), rule=RULE, sx1=np.zeros(n, bool))
    return a


def test_signal_exit_at_4h_close_after_intrabar_events():
    a = _lot_data(); a['sx1'][11] = True                              # 4h bin 08:00-12:00 closes at bar 11
    t = R.run_lot(a, 3, -1, 2.0, 7, 'causal')                         # short entry at 04:00 open
    assert t['why'] == 'signal' and t['t_out'] == pd.Timestamp(a['t'][11]) + H and t['x_ref'] == 100.0
    a = _lot_data(); a['sx1'][11] = True; a['h'][11] = 103.0          # stop touched in the same bar: stop first
    s = R.run_lot(a, 3, -1, 2.0, 7, 'causal')
    assert s['why'] == 'stop'
    w = R.run_lot(_lot_data() | dict(sx1=a['sx1']), 3, -1, 2.0, 7, 'worst')
    assert w['why'] == 'signal' and w['t_out'] == pd.Timestamp(a['t'][11]) + H


def test_time_cap_is_76_hours_backtest_parity():
    t = R.run_lot(_lot_data(), 3, -1, 2.0, 7, 'causal')
    assert t['why'] == 'time' and t['held_h'] == 76.0
    w = R.run_lot(_lot_data(), 3, -1, 2.0, 7, 'worst')
    assert w['why'] == 'time' and w['held_h'] == 76.0


def test_short_partial_then_2R_target():
    a = _lot_data(); dist = 2.0; e = 100 * (1 - R.SLIP)
    a['o'][6:] = 99.0; a['c'][6:] = 99.0; a['h'][6:] = 99.2; a['l'][6:] = 98.8
    a['o'][6] = 100.0; a['l'][6] = e - dist - 0.1                     # +1R for the short in bar 6
    a['l'][9] = e - 2 * dist - 0.1                                    # 2R target in bar 9
    t = R.run_lot(a, 3, -1, dist, 15, 'causal')
    q = t['qty']; q1 = np.floor(q * 0.5 / 0.001 + 1e-9) * 0.001
    assert t['why'] == 'target' and abs(t['part']['qty'] - q1) < 1e-12
    x1 = (e - dist) * (1 + R.SLIP); x2 = (e - 2 * dist) * (1 + R.SLIP)
    assert abs(t['gross'] - ((e - x1) * q1 + (e - x2) * (q - q1))) < 1e-9
    assert abs(t['pnl'] - (t['gross'] - t['fees'] - t['fund'])) < 1e-9


def test_prepare_short_only_window_and_eligibility():
    syms = ['BTCUSDT'] + [f'C{k}USDT' for k in range(9)]
    raw = raw_panel(syms, 4 * 1500, t0='2023-01-02 00:00')
    coins = R.prepare(raw, syms)
    n = sum(len(c['dec']) for c in coins.values())
    assert n > 0 and all(d[1] == -1 for c in coins.values() for d in c['dec'])
    s = next(s for s, c in coins.items() if c['dec'])
    i_last = coins[s]['dec'][0][2]
    raw[s]['elig'][i_last] = False
    coins2 = R.prepare(raw, syms)
    assert len(coins2[s]['dec']) == len(coins[s]['dec']) - 1


def test_arms_are_explicit():
    assert len(R.CONTROL) == 17 and set(R.MISSING_CORE20) == {'HYPEUSDT', 'LSKUSDT', 'PUMPUSDT'}
    assert len(R.COMPLEMENT) == 23 and not set(R.COMPLEMENT) & set(R.CORE20)
    assert sorted(R.CONTROL + R.COMPLEMENT) == sorted(R.CANON40)


def test_simulate_uses_rbr_lot_and_restores_harness():
    a = _lot_data(300); a['sx1'][11] = True
    coins = {'AUSDT': dict(a=a, dec=[(a['t'][4], -1, 3, 2.0)])}
    tr, _ = R.simulate(coins, ['AUSDT'], ['AUSDT'], 7, 'causal')
    assert tr[0]['why'] == 'signal' and R.X.run_lot is not R.run_lot



def test_signal_beats_time_label_on_the_deadline_bar():
    a = _lot_data(); a['sx1'][79] = True                              # entry 04:00, deadline 80:00 = close of bar 79
    t = R.run_lot(a, 3, -1, 2.0, 7, 'causal')
    assert t['why'] == 'signal' and t['held_h'] == 76.0


def test_sx_maps_to_last_1h_bar_of_each_4h_bin():
    syms = ['BTCUSDT'] + [f'C{k}USDT' for k in range(9)]
    raw = raw_panel(syms, 4 * 600)
    fr = R.frames4h(raw, syms); sg = R.signals(fr)
    coins = R.prepare(raw, syms)
    for s in syms:
        want = np.zeros(len(raw[s]['t']), bool); want[fr[s]['i'].values[sg[s][1]]] = True
        assert (coins[s]['a']['sx1'] == want).all() and (np.flatnonzero(want) % 4 == 3).all()
        assert want.any()


if __name__ == '__main__':
    fs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for f in fs: f(); print('PASS', f.__name__)
