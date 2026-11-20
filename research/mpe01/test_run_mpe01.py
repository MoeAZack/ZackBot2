"""Hostile mechanics tests for run_mpe01 (synthetic bars only; no market data, no economics)."""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_mpe01 as M

RULE = dict(step=0.001, min_qty=0.001, min_notional=5.0, tick=0.01)
DEC = np.datetime64('2025-03-03T04:00')
ATR = 2.0; DIST = 2.5 * ATR                       # limit offset 0.25 ATR = 0.5 below/above the 100.0 signal close
BP = 1e-4


def bars(n=700, px=100.0, vol=1e6, t0=DEC):
    t = t0 + np.arange(n) * M.M15
    return dict(t=t, o=np.full(n, px), h=np.full(n, px + 0.05), l=np.full(n, px - 0.05), c=np.full(n, px), v=np.full(n, vol))


def coin(b, sig_close=100.0, rule=RULE):
    a = dict(c=np.array([sig_close]), rule=rule)
    return dict(a=a, b=b, dec_of={0: DEC}, dec=[(DEC, 1, 0, DIST)])


def run(b, sd=1, arm='maker', scen='base', mode='m15', sig_close=100.0):
    return M.run_signal(coin(b, sig_close), 'AUSDT', sd, 0, DIST, arm, scen, mode)


def test_limit_is_tick_rounded_passive():
    assert abs(M.to_tick(99.507, 0.01, up=False) - 99.50) < 1e-12
    assert abs(M.to_tick(100.503, 0.01, up=True) - 100.51) < 1e-12
    assert abs(M.to_tick(99.50, 0.01, up=False) - 99.50) < 1e-12
    b = bars(); b['l'][1] = 99.40
    k, lot = run(b, sig_close=100.003); assert abs(lot['e'] - 99.50) < 1e-12        # 99.503 floored for a long
    b = bars(); b['h'][1] = 100.60
    k, lot = run(b, sd=-1, sig_close=99.997); assert abs(lot['e'] - 100.50) < 1e-12  # 100.497 ceiled for a short


def test_touch_or_one_tick_through_never_fills():
    b = bars(); b['l'][1] = 99.50                                     # post bar = index 1 (D + 15 min); L = 99.50
    k, od = run(b); assert k == 'order' and od['why'] == 'touch_only'
    b = bars(); b['l'][2] = 99.49                                     # 1 tick through
    k, od = run(b); assert k == 'order' and od['why'] == 'touch_only'
    b = bars(); b['l'][2] = 99.48                                     # 2 ticks through -> fill at L
    k, lot = run(b); assert k == 'trade' and lot['e'] == 99.50 and lot['t_in'] == pd.Timestamp(b['t'][2])


def test_volume_gate_then_later_fill():
    b = bars(); b['l'][1] = 99.40; b['v'][1] = 100 * 1.6 - 1          # qty = floor(8/5/0.001)*0.001 = 1.6
    k, od = run(b); assert k == 'order' and od['why'] == 'volume_short'
    b['l'][3] = 99.40
    k, lot = run(b); assert k == 'trade' and lot['t_in'] == pd.Timestamp(b['t'][3]) and abs(lot['qty'] - 1.6) < 1e-12
    b['v'][1] = 100 * 1.6                                             # exactly 100 x qty is enough
    k, lot = run(b); assert k == 'trade' and lot['t_in'] == pd.Timestamp(b['t'][1])


def test_postonly_cross_rejected_both_sides():
    b = bars(); b['o'][1] = 99.50
    k, od = run(b); assert k == 'reject'
    b = bars(); b['o'][1] = 100.50
    k, od = run(b, sd=-1); assert k == 'reject'
    b = bars(); b['o'][0] = 90.0                                      # only the post-time open matters, not the latency bar
    k, od = run(b); assert k == 'order'


def test_order_expires_after_four_bars():
    b = bars(); b['l'][5] = 99.0                                      # bars 1..4 work, bar 5 is after expiry
    k, od = run(b); assert k == 'order' and od['why'] == 'not_reached'
    assert od['t_free'] == b['t'][5]
    b = bars(); b['l'][4] = 99.0
    k, lot = run(b); assert k == 'trade'


def test_fill_bar_books_stop_but_never_target_or_partial():
    b = bars(); b['l'][1] = 99.40; b['h'][1] = 99.50 + 3 * DIST + 1   # fill bar also spans the 3R target
    k, lot = run(b); assert k == 'trade' and lot['why'] == 'time' and lot['part'] is None
    b = bars(); b['l'][1] = 99.50 - DIST - 0.01; b['h'][1] = 99.50 + 3 * DIST + 1
    k, lot = run(b); assert lot['why'] == 'stop_fillbar' and lot['x_ref'] == 99.50 - DIST
    assert lot['t_out'] == pd.Timestamp(b['t'][1]) + pd.Timedelta(minutes=15)


def test_fill_bar_opening_beyond_stop_is_conservative_stop_not_gap():
    b = bars(); b['o'][2] = 99.50 - DIST - 1; b['l'][2] = b['o'][2] - 0.1; b['h'][2] = b['o'][2] + 0.1
    k, lot = run(b)
    assert lot['e'] == 99.50 and lot['why'] == 'stop_fillbar' and lot['x_ref'] == 99.50 - DIST


def test_maker_partial_qty_fees_and_identity():
    b = bars(); b['l'][1] = 99.40
    e = 99.50; b['h'][3] = e + DIST + 0.01                            # +1R in bar 3
    b['h'][6] = e + 3 * DIST + 0.01                                   # 3R in bar 6
    k, lot = run(b)
    q = lot['qty']; q1 = np.floor(q * 0.5 / 0.001 + 1e-9) * 0.001
    assert lot['why'] == 'target' and abs(lot['part']['qty'] - q1) < 1e-12
    x1 = (e + DIST) * (1 - M.SLIP); x2 = (e + 3 * DIST) * (1 - M.SLIP)
    assert abs(lot['entry_fee'] - e * q * 4 * BP) < 1e-12
    assert abs(lot['gross'] - ((x1 - e) * q1 + (x2 - e) * (q - q1))) < 1e-9
    assert abs(lot['fees'] - (e * q * 4 * BP + x1 * q1 * 5 * BP + x2 * (q - q1) * 5 * BP)) < 1e-12
    assert abs(lot['pnl'] - (lot['gross'] - lot['fees'] - lot['fund'])) < 1e-9
    k, s8 = run(b, scen='stress8'); assert abs(s8['entry_fee'] - e * q * 12 * BP) < 1e-12
    k, at = run(b, scen='alltaker'); assert abs(at['entry_fee'] - e * q * 7 * BP) < 1e-12


def test_breakeven_not_retroactive_on_15m():
    b = bars(); b['l'][1] = 99.40; e = 99.50
    b['h'][3] = e + DIST + 0.01; b['l'][3] = e - 0.2                  # +1R and below entry in the same bar: no BE exit
    b['l'][4] = e - 0.01                                              # next bar: BE stop
    k, lot = run(b)
    assert lot['why'] == 'be' and lot['t_out'] == pd.Timestamp(b['t'][4]) + pd.Timedelta(minutes=15)


def test_short_gap_exit_at_open():
    b = bars(); b['h'][1] = 100.60                                    # short L = 100.50, fill (2 ticks through)
    b['o'][3] = 100.50 + DIST + 1; b['h'][3] = b['o'][3] + 0.1; b['l'][3] = b['o'][3] - 0.1
    k, lot = run(b, sd=-1)
    assert k == 'trade' and lot['e'] == 100.50 and lot['why'] == 'stop_gap' and lot['x_ref'] == b['o'][3]
    assert lot['t_out'] == pd.Timestamp(b['t'][3])


def test_taker_matched_clock_and_time_cap():
    b = bars(); b['o'][1] = 100.2
    k, lot = run(b, arm='taker')
    assert k == 'trade' and lot['e_ref'] == 100.2 and abs(lot['e'] - 100.2 * (1 + M.SLIP)) < 1e-12
    assert lot['t_in'] == pd.Timestamp(DEC) + pd.Timedelta(minutes=15)
    assert lot['why'] == 'time' and lot['held_h'] == 120.0
    b = bars(); b['l'][1] = 99.40
    k, mk = run(b)
    assert mk['why'] == 'time' and mk['t_out'] == pd.Timestamp(DEC) + pd.Timedelta(minutes=15) + pd.Timedelta(hours=120)


def test_worst_path_groups_4h_after_fill():
    b = bars(); b['l'][1] = 99.40; e = 99.50
    b['h'][3] = e + 3 * DIST + 0.01; b['l'][3] = e - DIST - 0.01     # same 4h bin: stop-first
    k, w = run(b, mode='worst'); assert w['why'] == 'stop'
    k, c = run(b, mode='m15'); assert c['why'] == 'stop'


def _multi(b1, b2):
    c1 = coin(b1); c2 = coin(b2)
    return {'AUSDT': c1, 'BUSDT': c2}


def test_capacity_reject_tries_next_unfilled_blocks():
    b1 = bars(); b1['o'][1] = 99.0                                    # A rejects post-only
    b2 = bars(); b2['l'][2] = 99.40                                   # B fills
    tr, sk, od = M.simulate(_multi(b1, b2), ['AUSDT', 'BUSDT'], 'maker', 'base', 'm15')
    assert [o['status'] for o in od] == ['postonly_cross', 'filled'] and tr[0]['sym'] == 'BUSDT'
    b1 = bars()                                                       # A works but never fills -> B is not tried
    tr, sk, od = M.simulate(_multi(b1, b2), ['AUSDT', 'BUSDT'], 'maker', 'base', 'm15')
    assert [o['status'] for o in od] == ['unfilled'] and not tr


def test_terminal_open_marked_and_reserves_slot():
    b = bars(n=40); b['l'][1] = 99.40
    c = coin(b); c['dec'] = [(DEC, 1, 0, DIST)]
    tr, sk, od = M.simulate({'AUSDT': c}, ['AUSDT'], 'maker', 'base', 'm15')
    assert tr[0]['why'] == 'TERMINAL_OPEN' and tr[0]['t_out'] == pd.Timestamp(b['t'][-1]) + pd.Timedelta(minutes=15)


def test_missing_post_bar_refused_no_optimistic_fill():
    b = bars(); b['t'] = b['t'] + M.M15 * 3                          # 15m data starts after the post time
    k, why = run(b); assert k == 'skip' and why == 'missing_15m'
    k, why = run(b, arm='taker'); assert k == 'skip' and why == 'missing_15m'


def test_load15_rejects_gaps(tmp=None):
    import tempfile
    d = tempfile.mkdtemp()
    t = (np.datetime64('2025-01-01T00:00') + np.r_[0, 1, 3] * M.M15).astype('datetime64[ms]').astype('int64')
    pd.DataFrame(dict(t=t, o=1.0, h=1.0, l=1.0, c=1.0, v=1.0, qv=1.0)).to_csv(os.path.join(d, 'AUSDT_15m.csv'), index=False,
                                                                              encoding='utf-8')
    try:
        M.load15(d, 'AUSDT'); assert False
    except ValueError:
        pass


def test_frozen_constants():
    assert (M.LIMIT_ATR, M.THROUGH_TICKS, M.VOL_MULT, M.WORK_BARS) == (0.25, 2, 100.0, 4)
    assert M.SPLIT == pd.Timestamp('2025-10-01') and M.COINS == ['ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'DOGEUSDT', 'ETHUSDT',
                                                                 'LINKUSDT', 'SOLUSDT', 'XRPUSDT']
    assert M.COSTS['taker']['base'] == (5 * BP, M.SLIP, 5 * BP, M.SLIP) and M.COSTS['maker']['base'][:2] == (4 * BP, 0)


if __name__ == '__main__':
    fs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for f in fs: f(); print('PASS', f.__name__)
