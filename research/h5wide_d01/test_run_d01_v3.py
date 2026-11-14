"""Hostile mechanics tests for run_d01_v3 (synthetic candles only, no market data, no economics)."""
import sys, os, tempfile, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_d01_v3 as D

RULE = dict(step=0.001, min_qty=0.001, min_notional=5.0)


def mk(o, h, l, c, sig_at, atr=1.0, t0='2023-01-01', times=None):
    n = len(o)
    t = np.array(times, dtype='datetime64[ns]') if times is not None else pd.date_range(t0, periods=n, freq='h').values
    sig = np.zeros(n, bool); sig[list(sig_at)] = True
    return dict(t=t, o=np.array(o, float), h=np.array(h, float), l=np.array(l, float), c=np.array(c, float),
                atr=np.full(n, atr), sig=sig, rule=RULE)


def flat(n, px=100.0):
    return [px] * n, [px + .5] * n, [px - .5] * n, [px] * n


def test_f1_cairo_dst():
    assert D.cairo_day(pd.Timestamp('2024-07-01 21:30')) == dt.date(2024, 7, 2)      # UTC+3 in summer
    assert D.cairo_day(pd.Timestamp('2024-01-01 21:30')) == dt.date(2024, 1, 1)      # UTC+2 in winter


def test_f2_daily_halt_uses_net_pnl():
    # three stop-outs and one target win on the same Cairo day; v1 (losses only) would halt, net must not
    o, h, l, c = flat(400)
    a = mk(o, h, l, c, [0, 10, 20, 30, 40], atr=1.0)
    for j in (2, 12, 32): a['l'][j] = 90.0                                           # stop hit
    a['h'][22] = 200.0                                                                  # target hit
    for i in range(5): pass
    D.RISK_SAVE = D.RISK
    tr, sk = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 15)
    day = {}
    for t in tr: day[D.cairo_day(t['t_out'])] = day.get(D.cairo_day(t['t_out']), 0) + t['pnl']
    assert all(v > -D.HALT for v in day.values())
    assert not any(s[2] == 'daily_halt' for s in sk)
    assert len(tr) == 5


def test_f2_daily_halt_triggers_on_net_loss():
    o, h, l, c = flat(400)
    a = mk(o, h, l, c, list(range(0, 400, 4)), atr=1.0)
    for j in range(2, 400, 4): a['l'][j] = 90.0
    saved = D.HALT; D.HALT = 20.0
    try:
        tr, sk = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 15)
    finally:
        D.HALT = saved
    assert any(s[2] == 'daily_halt' for s in sk)


def test_f3_terminal_open_reserves_slot():
    o, h, l, c = flat(30)
    a = mk(o, h, l, c, [0, 5, 10])
    b = mk(o, h, l, c, [6])
    tr, sk = D.simulate({'AAAUSDT': a, 'BBBUSDT': b}, ['AAAUSDT', 'BBBUSDT'], ['AAAUSDT', 'BBBUSDT'], 7)
    assert len(tr) == 1 and tr[0]['why'] == 'TERMINAL_OPEN'
    s = D.summarize(tr, sk, 1.0, 1.0)
    assert s['terminal_open'] == 1 and s['closes'] == 0


def test_f4_duplicates_raise_and_missing_next_hour():
    with tempfile.TemporaryDirectory() as td:
        pd.DataFrame(dict(t=[0, 3600000, 3600000], o=1, h=1, l=1, c=1, v=1, qv=1)).to_csv(os.path.join(td, 'XUSDT_1h.csv'), index=False)
        try:
            D.load(td, 'XUSDT', {}); assert False
        except ValueError as e:
            assert 'duplicate' in str(e)
    times = list(pd.date_range('2023-01-01', periods=3, freq='h')) + list(pd.date_range('2023-01-01 05:00', periods=60, freq='h'))
    o, h, l, c = flat(63)
    a = mk(o, h, l, c, [2], times=times)                                                # next candle is 3 h later
    tr, sk = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    assert not tr and sk[0][2] == 'missing_next_hour'


def test_f4_time_cap_is_elapsed_hours():
    times = list(pd.date_range('2023-01-01', periods=10, freq='h')) + list(pd.date_range('2023-01-02 06:00', periods=60, freq='h'))
    o, h, l, c = flat(70)
    a = mk(o, h, l, c, [0], times=times)
    tr, _ = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    assert tr[0]['why'] == 'time' and tr[0]['held_h'] == 48.0


def test_f5_slippage_and_fee_split():
    o, h, l, c = flat(60)
    a = mk(o, h, l, c, [0])
    tr, _ = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 15)
    t = tr[0]
    assert abs(t['e'] - 100 * 1.0002) < 1e-9 and abs(t['x'] - 100 * 0.9998) < 1e-9
    assert abs(t['fees'] - (t['e'] + t['x']) * t['qty'] * 0.0013) < 1e-9
    assert abs(t['pnl'] - ((t['x'] - t['e']) * t['qty'] - t['fees'] - t['fund'])) < 1e-9
    assert t['risk'] <= 8.0 + 1e-9


def test_f5_stop_gap_uses_open_and_stop_from_fill():
    o, h, l, c = flat(10)
    a = mk(o, h, l, c, [0])
    a['o'][3] = a['h'][3] = a['l'][3] = a['c'][3] = 90.0
    tr, _ = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    assert tr[0]['why'] == 'stop_gap' and tr[0]['x_ref'] == 90.0 and abs(tr[0]['x'] - 90 * 0.9998) < 1e-9


def test_f6_marked_dd_sees_intratrade_drawdown():
    o, h, l, c = flat(60)
    a = mk(o, h, l, c, [0], atr=2.0)
    a['c'][10] = 95.0; a['l'][10] = 95.0                                                # deep mark, no stop (stop 94)
    tr, sk = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    s = D.summarize(tr, sk, 1.0, 1.0)
    assert s['marked_dd_usd'] > abs(tr[0]['pnl']) and s['closedR_maxdd'] >= 0


def test_f7_missing_rule_refused():
    o, h, l, c = flat(60)
    a = mk(o, h, l, c, [0]); a['rule'] = None
    tr, sk = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    assert not tr and sk[0][2] == 'no_rule'


# ---- v3 (Codex execution clarification 1) ----
def test_v3_equal_boundary_exit_then_entry_allowed():
    o, h, l, c = flat(60)
    a = mk(o, h, l, c, [0]); a['l'][3] = 90.0                                          # intrabar stop on candle 3
    b = mk(o, h, l, c, [2, 3])                                                          # 2 is blocked, 3 closes at A's exit
    tr, _ = D.simulate({'AAAUSDT': a, 'BBBUSDT': b}, ['AAAUSDT', 'BBBUSDT'], ['AAAUSDT', 'BBBUSDT'], 7)
    assert tr[0]['why'] == 'stop' and tr[0]['t_out'] == pd.Timestamp(a['t'][3]) + pd.Timedelta(hours=1)
    assert len(tr) == 2 and tr[1]['sym'] == 'BBBUSDT' and tr[1]['t_sig'] == pd.Timestamp(b['t'][3])
    assert tr[1]['t_in'] == pd.Timestamp(b['t'][4])


def test_v3_exit_times_gap_at_open_intrabar_at_close():
    o, h, l, c = flat(10)
    a = mk(o, h, l, c, [0]); a['o'][3] = a['h'][3] = a['l'][3] = a['c'][3] = 90.0
    g, _ = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    assert g[0]['why'] == 'stop_gap' and g[0]['t_out'] == pd.Timestamp(a['t'][3])
    o, h, l, c = flat(10)
    b = mk(o, h, l, c, [0]); b['h'][3] = 200.0
    t, _ = D.simulate({'AAAUSDT': b}, ['AAAUSDT'], ['AAAUSDT'], 7)
    assert t[0]['why'] == 'target' and t[0]['t_out'] == pd.Timestamp(b['t'][3]) + pd.Timedelta(hours=1)


def test_v3_funding_on_close_every_held_bar_including_exit():
    o, h, l, c = flat(80)
    a = mk(o, h, l, c, [0])
    for j in range(1, 80): a['c'][j] = 100.0 + 0.01 * j; a['h'][j] = a['c'][j] + 0.5
    tr, _ = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    t = tr[0]; assert t['why'] == 'time' and t['held_h'] == 48.0
    want = t['qty'] * sum(a['c'][1:49]) * D.FUND_H                                    # bars 1..48, exit bar 48 included
    assert abs(t['fund'] - want) < 1e-12
    assert abs(t['fund'] - t['qty'] * sum(a['o'][1:49]) * D.FUND_H) > 1e-9           # not on opens


def test_v3_entry_fee_booked_on_entry_cairo_day():
    # entry 21:00 UTC Jan 1 (Cairo 23:00 Jan 1), stop exit at close 00:00 UTC Jan 2 (Cairo Jan 2), then a Jan-2 signal.
    o, h, l, c = flat(60)
    a = mk(o, h, l, c, [20]); a['l'][23] = 90.0
    one, _ = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 23)
    A = one[0]
    assert D.cairo_day(A['t_in']) == dt.date(2023, 1, 1) and D.cairo_day(A['t_out']) == dt.date(2023, 1, 2)
    exit_day_net = A['pnl'] + A['entry_fee']                                           # entry fee belongs to Jan 1
    assert A['entry_fee'] > 0 and exit_day_net > A['pnl']
    a2 = mk(o, h, l, c, [20, 30]); a2['l'][23] = 90.0
    saved = D.HALT; D.HALT = (abs(A['pnl']) + abs(exit_day_net)) / 2                  # halts only if fee is on exit day
    try:
        tr, sk = D.simulate({'AAAUSDT': a2}, ['AAAUSDT'], ['AAAUSDT'], 23)
        assert len(tr) == 2 and not any(s[2] == 'daily_halt' for s in sk)
        D.HALT = abs(exit_day_net) * 0.99                                               # the exit-day net itself still halts
        tr, sk = D.simulate({'AAAUSDT': a2}, ['AAAUSDT'], ['AAAUSDT'], 23)
        assert len(tr) == 1 and any(s[2] == 'daily_halt' for s in sk)
    finally:
        D.HALT = saved


def test_v3_underwater_measured_through_final_bar():
    o, h, l, c = flat(10)
    a = mk(o, h, l, c, [0]); a['l'][3] = 90.0                                          # one loss, then idle to T1
    tr, sk = D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], 7)
    dd, uw_days, last = D.marked_curve(tr)
    first_mark = tr[0]['marks'][0][0]
    want = (D.T1 + pd.Timedelta(hours=1) - first_mark).total_seconds() / 86400
    assert last < 0 and abs(uw_days - want) < 1e-9


if __name__ == '__main__':
    fs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for f in fs: f(); print('PASS', f.__name__)
