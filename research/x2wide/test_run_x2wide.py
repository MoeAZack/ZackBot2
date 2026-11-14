"""Hostile mechanics tests for run_x2wide (synthetic candles only; no market data, no economics)."""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_x2wide as X

RULE = dict(step=0.001, min_qty=0.001, min_notional=5.0)
H = pd.Timedelta(hours=1)


def mk(n, px=100.0, t0='2023-01-02 00:00', rule=RULE):
    t = pd.date_range(t0, periods=n, freq='h').values
    return dict(t=t, o=np.full(n, px), h=np.full(n, px + .2), l=np.full(n, px - .2), c=np.full(n, px),
                elig=np.ones(n, bool), rule=rule)


def bar(a, j, o=None, h=None, l=None, c=None):
    for k, v in (('o', o), ('h', h), ('l', l), ('c', c)):
        if v is not None: a[k][j] = v


def lift(a, j, px=101.0):
    """From 1h bar j on, trade flat at px (above the entry fill), so breakeven is not gapped by a flat open."""
    a['o'][j:] = px; a['c'][j:] = px; a['h'][j:] = px + .2; a['l'][j:] = px - .2


def test_to4h_complete_bins_only():
    a = mk(12)
    a['t'] = np.delete(a['t'], 5); [a.__setitem__(k, np.delete(a[k], 5)) for k in ('o', 'h', 'l', 'c', 'elig')]
    a['h'][0] = 150.0; a['c'][3] = 101.0
    b = X.to4h(a)
    assert list(b.t) == [pd.Timestamp('2023-01-02 00:00'), pd.Timestamp('2023-01-02 08:00')]
    assert b.h[0] == 150.0 and b.c[0] == 101.0 and b.i[0] == 3 and b.i[1] == 10


def test_short_fill_stop_and_gap():
    a = mk(40)
    lot = X.run_lot(a, 3, -1, 2.0, 15, 'causal')                     # entry at index 4 open 100
    assert abs(lot['e'] - 100 * (1 - X.SLIP)) < 1e-12
    a = mk(40); bar(a, 7, o=103.0, h=103.5, l=102.5, c=103.0)        # short stop = e + 2 ~ 101.98: gap above
    g = X.run_lot(a, 3, -1, 2.0, 15, 'causal')
    assert g['why'] == 'stop_gap' and g['x_ref'] == 103.0 and abs(g['x'] - 103 * (1 + X.SLIP)) < 1e-12
    assert g['t_out'] == pd.Timestamp(a['t'][7]) and g['pnl'] < -8.0
    a = mk(40); bar(a, 7, h=102.5)
    s = X.run_lot(a, 3, -1, 2.0, 15, 'causal')
    assert s['why'] == 'stop' and abs(s['x_ref'] - (s['e'] + 2.0)) < 1e-12 and s['t_out'] == pd.Timestamp(a['t'][7]) + H


def test_partial_qty_fees_and_funding():
    a = mk(60)
    dist = 2.0
    e = 100 * (1 + X.SLIP)
    lift(a, 6); bar(a, 6, o=100.0, h=e + dist + 0.1, c=101.0)          # +1R reached in bar 6 -> partial
    bar(a, 9, h=e + 3 * dist + 0.1, c=106.0)                           # 3R target in bar 9
    t = X.run_lot(a, 3, 1, dist, 15, 'causal')
    q = t['qty']; q1 = np.floor(q * 0.5 / 0.001 + 1e-9) * 0.001
    assert t['why'] == 'target' and abs(t['part']['qty'] - q1) < 1e-12 and abs(t['q_final'] - (q - q1)) < 1e-12
    fee = X.FEES[15]
    x1 = (e + dist) * (1 - X.SLIP); x2 = (e + 3 * dist) * (1 - X.SLIP)
    assert abs(t['fees'] - (e * q + x1 * q1 + x2 * (q - q1)) * fee) < 1e-9
    assert abs(t['gross'] - ((x1 - e) * q1 + (x2 - e) * (q - q1))) < 1e-9
    want_fund = (q * a['c'][4:7].sum() + (q - q1) * a['c'][7:10].sum()) * X.FUND_H   # full qty through the partial bar
    assert abs(t['fund'] - want_fund) < 1e-12
    assert abs(t['pnl'] - (t['gross'] - t['fees'] - t['fund'])) < 1e-9
    assert abs(sum(v for _, v in t['cash']) - t['pnl']) < 1e-9 and t['risk'] <= 8.0 + 1e-9


def test_same_bar_tp1_then_be_not_retroactive():
    a = mk(60); dist = 2.0; e = 100 * (1 + X.SLIP)
    lift(a, 6); bar(a, 6, o=100.0, h=e + dist + 0.1, l=e - 0.5, c=101.0)  # +1R and back below entry inside one bar
    bar(a, 7, l=e - 0.5)
    t = X.run_lot(a, 3, 1, dist, 7, 'causal')
    assert t['why'] == 'be' and t['t_out'] == pd.Timestamp(a['t'][7]) + H and abs(t['x_ref'] - e) < 1e-12
    assert t['part']['qty'] > 0                                        # partial kept, no exit in bar 6


def test_stop_and_tp1_same_bar_is_stop_first():
    a = mk(60); dist = 2.0; e = 100 * (1 + X.SLIP)
    bar(a, 6, h=e + dist + 0.1, l=e - dist - 0.1)
    t = X.run_lot(a, 3, 1, dist, 7, 'causal')
    assert t['why'] == 'stop' and t['part'] is None and t['q_final'] == t['qty']


def test_worst_path_resolves_inside_4h_stop_first():
    a = mk(80, t0='2023-01-02 00:00'); dist = 2.0; e = 100 * (1 + X.SLIP)
    # entry at 04:00 (index 4); 4h bin 08:00-12:00 = indices 8..11: target in 1h bar 8, stop in 1h bar 10
    bar(a, 8, h=e + 3 * dist + 0.1)
    bar(a, 10, l=e - dist - 0.1)
    c = X.run_lot(a, 3, 1, dist, 15, 'causal')
    w = X.run_lot(a, 3, 1, dist, 15, 'worst')
    assert c['why'] == 'target' and c['t_out'] == pd.Timestamp(a['t'][8]) + H
    assert w['why'] == 'stop' and w['t_out'] == pd.Timestamp(a['t'][11]) + H and w['pnl'] < c['pnl']


def test_worst_path_breakeven_only_from_next_4h_bar():
    a = mk(200); dist = 2.0; e = 100 * (1 + X.SLIP)
    lift(a, 8); bar(a, 8, o=100.0, h=e + dist + 0.1)                   # +1R in 1h bar 8
    bar(a, 10, l=e - 0.5)                                              # back below entry in bar 10 (same 4h bin)
    c = X.run_lot(a, 3, 1, dist, 7, 'causal')
    w = X.run_lot(a, 3, 1, dist, 7, 'worst')
    assert c['why'] == 'be' and c['t_out'] == pd.Timestamp(a['t'][10]) + H
    assert w['why'] == 'time' and w['part']['qty'] > 0                 # no retroactive BE inside the 4h bar


def test_partial_blocked_by_min_notional_keeps_breakeven():
    a = mk(60, rule=dict(step=0.001, min_qty=0.001, min_notional=300.0)); dist = 2.0; e = 100 * (1 + X.SLIP)
    lift(a, 6); bar(a, 6, o=100.0, h=e + dist + 0.1)
    bar(a, 8, l=e - 0.5)
    t = X.run_lot(a, 3, 1, dist, 7, 'causal')
    assert t['part'].get('blocked') and t['q_final'] == t['qty'] and t['why'] == 'be'


def test_breakeven_gap_exits_at_open():
    a = mk(60); dist = 2.0; e = 100 * (1 + X.SLIP)
    bar(a, 6, h=e + dist + 0.1)                                        # flat 100 afterwards: open below BE stop e
    t = X.run_lot(a, 3, 1, dist, 7, 'causal')
    assert t['why'] == 'be_gap' and t['t_out'] == pd.Timestamp(a['t'][7]) and t['x_ref'] == 100.0


def test_partial_qty_floors_to_step():
    a = mk(60); dist = 2.4; e = 100 * (1 + X.SLIP)
    lift(a, 6); bar(a, 6, o=100.0, h=e + dist + 0.1)
    t = X.run_lot(a, 3, 1, dist, 7, 'causal')
    assert abs(t['qty'] - 3.333) < 1e-12 and abs(t['part']['qty'] - 1.666) < 1e-12 and abs(t['q_final'] - 1.667) < 1e-12


def test_daily_cash_partial_on_its_day_rest_on_exit_day():
    # Cairo = UTC+2 in January. Entry 04:00 UTC Jan 2, partial profit Jan 2, BE exit at 00:00 UTC Jan 3 (Cairo Jan 3).
    a = mk(300); e = 100 * (1 + X.SLIP)
    lift(a, 6); bar(a, 6, o=100.0, h=e + 2.1); bar(a, 23, l=e - 0.5)
    one, _ = X.simulate(_coins([(a['t'][4], 1, 3, 2.0)], [], a=a), ['AAAUSDT', 'BBBUSDT'], ['AAAUSDT', 'BBBUSDT'], 15, 'causal')
    A = one[0]
    assert A['why'] == 'be' and A['pnl'] > 0 and A['cash'][-1][1] < 0
    assert X.D.cairo_day(A['cash'][1][0]) != X.D.cairo_day(A['t_out'])
    saved = X.HALT
    try:
        X.HALT = abs(A['cash'][-1][1]) * 0.99                         # the exit-day cash alone halts a later Jan-3 decision
        tr, sk = X.simulate(_coins([(a['t'][4], 1, 3, 2.0), (a['t'][30], 1, 29, 2.0)], [], a=a), ['AAAUSDT', 'BBBUSDT'],
                            ['AAAUSDT', 'BBBUSDT'], 15, 'causal')
        assert len(tr) == 1 and any(s[2] == 'daily_halt' for s in sk)
    finally:
        X.HALT = saved


def test_time_cap_120_elapsed_hours():
    a = mk(200)
    t = X.run_lot(a, 3, 1, 2.0, 7, 'causal')
    assert t['why'] == 'time' and t['held_h'] == 120.0
    w = X.run_lot(a, 3, 1, 2.0, 7, 'worst')
    assert w['why'] == 'time' and w['held_h'] == 120.0


def _coins(dec_a, dec_b, a=None, b=None):
    a = a or mk(300); b = b or mk(300)
    return {'AAAUSDT': dict(a=a, dec=dec_a), 'BBBUSDT': dict(a=b, dec=dec_b)}


def test_global_slot_ties_and_equal_boundary():
    a = mk(300); bar(a, 7, l=90.0)                                     # A: stop exit at close of bar 7 = t[8]
    t = a['t']
    ca = [(t[4], 1, 3, 2.0)]
    cb = [(t[4], 1, 3, 2.0), (t[7], 1, 6, 2.0), (t[8], 1, 7, 2.0)]   # tie at t4; t7 blocked; t8 = equal boundary
    tr, _ = X.simulate(_coins(ca, cb, a=a), ['AAAUSDT', 'BBBUSDT'], ['AAAUSDT', 'BBBUSDT'], 7, 'causal')
    assert [x['sym'] for x in tr] == ['AAAUSDT', 'BBBUSDT'] and tr[1]['t_sig'] == pd.Timestamp(t[8])
    tr2, _ = X.simulate(_coins(ca, cb, a=a), ['AAAUSDT', 'BBBUSDT'], ['BBBUSDT', 'AAAUSDT'], 7, 'causal')
    assert tr2[0]['sym'] == 'BBBUSDT'


def test_terminal_open_censors_later_signals():
    a = mk(30); b = mk(300)
    tr, _ = X.simulate(_coins([(a['t'][4], 1, 3, 2.0)], [(b['t'][40], 1, 39, 2.0)], a=a, b=b),
                       ['AAAUSDT', 'BBBUSDT'], ['AAAUSDT', 'BBBUSDT'], 7, 'causal')
    assert len(tr) == 1 and tr[0]['why'] == 'TERMINAL_OPEN'


def test_daily_halt_counts_entry_fee_and_partials():
    a = mk(300); bar(a, 7, l=90.0)
    saved = X.HALT
    try:
        X.HALT = 7.0
        tr, sk = X.simulate(_coins([(a['t'][4], 1, 3, 2.0), (a['t'][12], 1, 11, 2.0)], [], a=a), ['AAAUSDT', 'BBBUSDT'],
                            ['AAAUSDT', 'BBBUSDT'], 15, 'causal')
        assert len(tr) == 1 and any(s[2] == 'daily_halt' for s in sk)
    finally:
        X.HALT = saved


def test_signal_long_and_short_from_frozen_rule():
    n4 = 300
    t4 = pd.date_range('2023-01-01', periods=n4, freq='4h')
    btc = pd.DataFrame(dict(t=t4, o=100.0, h=100.5, l=99.5, c=100.0, v=1.0))
    alt_c = np.r_[np.full(250, 10.0), 10.0 + 0.05 * np.arange(1, 51)]
    alt = pd.DataFrame(dict(t=t4, o=alt_c, h=alt_c + .01, l=alt_c - .01, c=alt_c, v=1.0))
    bx = X.S.indicators(btc); bx = bx.set_index(pd.to_datetime(bx.t))
    le, se, atr = X.signals(alt, bx)
    assert list(np.flatnonzero(le)) == [250] and not se.any()
    le2, se2, _ = X.signals(alt.assign(o=20 - alt_c, h=20 - alt_c + .01, l=20 - alt_c - .01, c=20 - alt_c), bx)
    assert list(np.flatnonzero(se2)) == [250] and not le2.any()


def test_prepare_respects_eligibility_and_window():
    n = 300 * 4
    a = mk(n, t0='2023-01-01 00:00')
    alt_c = np.r_[np.full(1000, 10.0), 10.0 + 0.01 * np.arange(1, 201)]
    a['o'] = a['c'] = alt_c.copy(); a['h'] = alt_c + .01; a['l'] = alt_c - .01
    b4 = pd.DataFrame(dict(t=pd.date_range('2023-01-01', periods=300, freq='4h'), o=100.0, h=100.5, l=99.5, c=100.0, v=1.0))
    bx = X.S.indicators(b4); bx = bx.set_index(pd.to_datetime(bx.t))
    dec, d4, le, se = X.prepare(a, bx)
    assert len(dec) == 1 and dec[0][1] == 1 and dec[0][2] == 1003 and dec[0][0] == a['t'][1003] + np.timedelta64(1, 'h')
    a['elig'][1003] = False
    dec2, _, _, _ = X.prepare(a, bx)
    assert dec2 == []



def _old_mgmt_bars(a, j0, mode):                                       # de27e871 version (full remaining history)
    n = len(a['t'])
    if mode == 'causal': return [[j] for j in range(j0, n)]
    key = pd.to_datetime(a['t'][j0:]).floor('4h').values
    out, cur = [], [j0]
    for j in range(j0 + 1, n):
        if key[j - j0] == key[j - j0 - 1]: cur.append(j)
        else: out.append(cur); cur = [j]
    out.append(cur)
    return out


def test_speed_patch_parity_with_gaps_and_paths():
    rng = np.random.default_rng(7)
    for case in range(150):
        n = 400; a = mk(n)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n))); o = np.r_[100.0, c[:-1]]
        a['o'], a['c'] = o, c; a['h'] = np.maximum(o, c) * 1.002; a['l'] = np.minimum(o, c) * 0.998
        g0 = int(rng.integers(100, 140)); g1 = g0 + int(rng.integers(1, 40))   # a data gap near the 120 h deadline
        keep = np.r_[0:g0, g1:n]
        for k in ('t', 'o', 'h', 'l', 'c', 'elig'): a[k] = a[k][keep]
        for mode in ('causal', 'worst'):
            for side in (1, -1):
                new = X.run_lot(a, 3, side, 1.5, 15, mode)
                saved = X.mgmt_bars; X.mgmt_bars = _old_mgmt_bars
                try: old = X.run_lot(a, 3, side, 1.5, 15, mode)
                finally: X.mgmt_bars = saved
                for k in ('why', 't_out', 'pnl', 'fund', 'fees', 'gross', 'q_final'):
                    assert old[k] == new[k], (case, mode, side, k)
                assert old['marks'] == new['marks']


if __name__ == '__main__':
    fs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for f in fs: f(); print('PASS', f.__name__)
