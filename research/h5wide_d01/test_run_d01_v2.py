"""Hostile mechanics tests for run_d01_v2 (synthetic candles only, no market data, no economics)."""
import sys, os, tempfile, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_d01_v2 as D

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


if __name__ == '__main__':
    fs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for f in fs: f(); print('PASS', f.__name__)
