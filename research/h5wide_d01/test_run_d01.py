"""Synthetic mechanics checks for run_d01.simulate (no market data, no economics)."""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_d01 as D

RULE = dict(step=0.001, min_qty=0.001, min_notional=5.0)


def mk(o, h, l, c, sig_at, atr=1.0, t0='2023-01-01'):
    n = len(o); t = pd.date_range(t0, periods=n, freq='h').values
    sig = np.zeros(n, bool); sig[sig_at] = True
    return dict(t=t, o=np.array(o, float), h=np.array(h, float), l=np.array(l, float), c=np.array(c, float),
                atr=np.full(n, atr), sig=sig, rule=RULE)


def run(a, cost=0):
    return D.simulate({'AAAUSDT': a}, ['AAAUSDT'], ['AAAUSDT'], cost)[0]


def test_stop_first_and_fixed_risk():
    # entry at 100 (bar 1 open), stop 97, target 112; bar 2 touches both -> stop
    a = mk([100, 100, 100, 100], [100, 101, 113, 100], [100, 99, 96, 100], [100, 100, 100, 100], [0])
    tr = run(a)
    assert tr[0]['why'] == 'stop' and abs(tr[0]['x'] - 97) < 1e-9
    assert tr[0]['risk'] <= 8.0 + 1e-9 and abs(tr[0]['R'] + tr[0]['risk'] / 8 + tr[0]['fund'] / 8) < 1e-9


def test_adverse_gap_fill():
    a = mk([100, 100, 90, 90], [100, 101, 91, 91], [100, 99, 89, 89], [100, 100, 90, 90], [0])
    tr = run(a)
    assert tr[0]['why'] == 'stop_gap' and tr[0]['x'] == 90


def test_time_exit_and_costs():
    n = 60
    a = mk([100] * n, [100.5] * n, [99.5] * n, [100] * n, [0])
    tr = run(a, cost=15)
    assert tr[0]['why'] == 'time' and tr[0]['held'] == 48
    assert abs(tr[0]['fees'] - 2 * 100 * tr[0]['qty'] * 0.0015) < 1e-9


def test_one_position_and_tie_order():
    n = 120
    a = mk([100] * n, [100.5] * n, [99.5] * n, [100] * n, [0, 2, 49])
    b = dict(a); b['sig'] = a['sig'].copy()
    tr = D.simulate({'AAAUSDT': a, 'BBBUSDT': b}, ['AAAUSDT', 'BBBUSDT'], ['BBBUSDT', 'AAAUSDT'], 0)[0]
    # signal at 0 -> BBB wins the tie; signal at 2 is ignored (position open); the exit bar is 48 and 49 > 48 is taken
    assert [t['sym'] for t in tr] == ['BBBUSDT', 'BBBUSDT'] and tr[1]['t_sig'] > tr[0]['t_out']


if __name__ == '__main__':
    for f in [test_stop_first_and_fixed_risk, test_adverse_gap_fill, test_time_exit_and_costs, test_one_position_and_tie_order]:
        f(); print('PASS', f.__name__)
