"""Synthetic tests for the independent SPOTFLOW oracle (no market data, no outcomes)."""
import sys, os, tempfile
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spotflow_oracle as O

H = pd.Timedelta(hours=1)
RULE = dict(step=0.001, min_qty=0.001, min_notional=5.0)
T0 = pd.Timestamp('2025-01-01 00:00')


def bars(n, t0=T0, px=100.0, qv=1e9):
    idx = pd.date_range(t0, periods=n, freq='h')
    return pd.DataFrame(dict(o=px, h=px + 0.2, l=px - 0.2, c=px, v=1.0, qv=qv, tbq=qv / 2), index=idx)


def test_flow_sign_and_denominator():
    f = O.flow([100, 100, 100, 0, 100, 100], [100, 0, 60, 0, 101, -1])
    assert f[0] == 1.0 and f[1] == -1.0 and abs(f[2] - 0.2) < 1e-15
    assert np.isnan(f[3]) and np.isnan(f[4]) and np.isnan(f[5])


def test_ms_us_boundary_and_header_and_conflicts():
    d = tempfile.mkdtemp()
    ms = int(pd.Timestamp('2024-12-31 23:00').value // 10 ** 6); us = int(pd.Timestamp('2025-01-01 00:00').value // 10 ** 3)
    row = lambda t, c: f'{t},1,2,0.5,{c},10,{t + 1},100,5,4,40,0\n'
    p1 = os.path.join(d, 'a.csv'); p2 = os.path.join(d, 'b.csv')
    open(p1, 'w', encoding='utf-8').write('open_time,open,high,low,close,volume,close_time,quote_volume,count,'
                                          'taker_buy_volume,taker_buy_quote_volume,ignore\n' + row(ms, 1.5))
    open(p2, 'w', encoding='utf-8').write(row(us, 1.6) + row(us, 1.6))
    k = O.read_klines([p1, p2])
    assert list(k.index) == [pd.Timestamp('2024-12-31 23:00'), pd.Timestamp('2025-01-01 00:00')] and k.tbq.iloc[1] == 40
    open(p2, 'w', encoding='utf-8').write(row(us, 1.6) + row(us, 1.7))
    try:
        O.read_klines([p2]); assert False
    except ValueError:
        pass


def test_ret3_requires_consecutive_bars_no_ffill():
    b = bars(10); b.loc[b.index[5], 'c'] = 110.0
    r = O.ret3(b)
    assert abs(r.iloc[5] - np.log(1.1)) < 1e-15 and np.isnan(r.iloc[2]) and abs(r.iloc[3]) < 1e-15
    b2 = b.drop(b.index[6])
    r2 = O.ret3(b2)
    assert r2.loc[b.index[7]:b.index[9]].isna().all() and np.isfinite(r2.loc[b.index[5]])


def test_pit_elig_prior_days_only_and_run():
    b = bars(24 * 40, qv=1e9 / 24)                                    # 1e9/day >> 10m
    e = O.pit_elig(b)
    assert not e.iloc[:720].any()                                     # <720 consecutive bars / <30 prior days
    day31 = T0 + pd.Timedelta(days=30)
    assert e[day31:].all() and not e[:day31 - H].any()
    b2 = b.copy(); b2.loc[b2.index >= day31 + pd.Timedelta(days=2), 'qv'] = 0.0      # future volume collapse
    assert (O.pit_elig(b2)[:day31 + pd.Timedelta(days=2) - H] == e[:day31 + pd.Timedelta(days=2) - H]).all()
    b3 = b.drop(b.index[24 * 35 + 3])                                 # gap resets the 720-bar run
    e3 = O.pit_elig(b3)
    assert not e3[b.index[24 * 35 + 4]:b.index[24 * 35 + 4] + 700 * H].any()


def _setup(n=24 * 40, t0=T0):
    fut = bars(n, t0); spot = bars(n, t0); perp = bars(n, t0); btc = bars(n, t0)
    t = fut.index[24 * 35]
    for k in range(3): fut.loc[t - k * H, 'c'] = 98.2 + 0.6 * k                 # closes 99.4, 98.8, 98.2 at t-2..t
    spot.loc[t, 'tbq'] = 0.61 * 1e9; perp.loc[t, 'tbq'] = 0.39 * 1e9              # SpotI +0.22, PerpI -0.22
    return fut, perp, spot, btc, t


def test_long_signal_levels_and_closed_hour_join():
    fut, perp, spot, btc, t = _setup()
    s = O.signals(fut, perp, spot, btc)
    assert list(s.index) == [t] and s.side.iloc[0] == 1
    spot2 = spot.copy(); spot2.loc[t, 'tbq'] = 0.5999 * 1e9                        # SpotI just below 0.2
    assert O.signals(fut, perp, spot2, btc).empty
    spot2.loc[t, 'tbq'] = 0.6 * 1e9 * (1 + 1e-12)                                  # 0.2 level is inclusive (float-safe input)
    assert len(O.signals(fut, perp, spot2, btc)) == 1
    fut5 = fut.copy(); fut5.loc[t - 2 * H:t, 'c'] = 100.0 * np.exp(-0.001)       # 3h move far inside ATR/close: no signal
    assert (O.wilder_atr(fut5) / fut5.c)[t] > 0.003 and O.signals(fut5, perp, spot, btc).empty
    spot3 = spot.drop(t)                                                          # missing spot hour: no ffill, no signal
    assert O.signals(fut, perp, spot3, btc).empty
    spot4 = spot.copy(); spot4.loc[t + H, 'tbq'] = 0.6 * 1e9; spot4.loc[t, 'tbq'] = 0.5e9   # flow one hour later: no signal
    assert O.signals(fut, perp, spot4, btc).empty


def test_short_mirror_and_btc_skip():
    fut, perp, spot, btc, t = _setup()
    for k in range(3): fut.loc[t - k * H, 'c'] = 101.8 - 0.6 * k
    spot.loc[t, 'tbq'] = 0.39e9; perp.loc[t, 'tbq'] = 0.61e9
    s = O.signals(fut, perp, spot, btc)
    assert list(s.index) == [t] and s.side.iloc[0] == -1
    btc.loc[t, 'c'] = 100 * np.exp(0.015 + 1e-12)                                  # float-safe: skip is inclusive at 0.015
    assert O.signals(fut, perp, spot, btc).empty
    btc.loc[t, 'c'] = 100 * np.exp(0.0149)
    assert len(O.signals(fut, perp, spot, btc)) == 1


def test_window_edges():
    fut, perp, spot, btc, t = _setup(t0=O.T_FROM - 24 * 35 * H)          # signal hour exactly T_FROM: kept
    assert t == O.T_FROM and len(O.signals(fut, perp, spot, btc)) == 1
    fut, perp, spot, btc, t = _setup(t0=O.T_FROM - 24 * 35 * H - H)      # one hour earlier: excluded
    assert O.signals(fut, perp, spot, btc).empty
    fut, perp, spot, btc, t = _setup(t0=O.T_TO - 24 * 35 * H + H)        # one hour after T_TO: excluded
    assert O.signals(fut, perp, spot, btc).empty


def test_spot_venue_eligibility_required():
    fut, perp, spot, btc, t = _setup()
    spot2 = spot.copy(); spot2['qv'] = 1e5; spot2['tbq'] = spot2.qv / 2; spot2.loc[t, 'tbq'] = 0.61e5   # spot illiquid
    assert O.signals(fut, perp, spot2, btc).empty


def test_signals_ignore_future_bars():
    fut, perp, spot, btc, t = _setup()
    s1 = O.signals(fut, perp, spot, btc)
    fut2 = fut.copy(); fut2.loc[t + H:, ['o', 'h', 'l', 'c']] *= 1.5
    spot2 = spot.copy(); spot2.loc[t + H:, 'qv'] = 0.0
    assert O.signals(fut2, perp, spot2, btc).equals(s1)


def _lot_bars(n=40):
    return bars(n)


def test_lot_long_time_exit_and_cash_identity():
    f = _lot_bars(); ts = f.index[3]
    L = O.oracle_lot(f, ts, 1, 1.0, RULE, 7)
    e = 100 * (1 + O.SLIP); q = np.floor(8 / 3.0 / 0.001 + 1e-9) * 0.001
    assert L['t_in'] == ts + H and abs(L['e'] - e) < 1e-12 and abs(L['qty'] - q) < 1e-12
    assert L['why'] == 'time' and L['t_out'] == ts + H + 12 * H
    x = 100 * (1 - O.SLIP)
    assert abs(L['fees'] - (e + x) * q * 0.0005) < 1e-12 and abs(L['fund'] - 12 * q * 100 * O.FUND_H) < 1e-12
    assert abs(L['pnl'] - ((x - e) * q - L['fees'] - L['fund'])) < 1e-12


def test_lot_short_gap_stop_target_terminal():
    f = _lot_bars(); ts = f.index[3]; es = 100 * (1 - O.SLIP)
    f.loc[f.index[6], ['o', 'h', 'l', 'c']] = [104.0, 104.2, 103.8, 104.0]
    L = O.oracle_lot(f, ts, -1, 1.0, RULE)
    assert L['why'] == 'stop_gap' and L['t_out'] == f.index[6] and abs(L['x'] - 104 * (1 + O.SLIP)) < 1e-12
    f = _lot_bars(); f.loc[f.index[7], 'h'] = es + 3.0 + 0.01; f.loc[f.index[7], 'l'] = es - 6.01
    L = O.oracle_lot(f, ts, -1, 1.0, RULE)
    assert L['why'] == 'stop'                                          # stop-first in the same bar
    f = _lot_bars(); f.loc[f.index[7], 'l'] = es - 6.01
    L = O.oracle_lot(f, ts, -1, 1.0, RULE, 15)
    assert L['why'] == 'target' and L['t_out'] == f.index[7] + H
    f = _lot_bars(10)
    L = O.oracle_lot(f, f.index[3], 1, 1.0, RULE)
    assert L['why'] == 'TERMINAL_OPEN' and L['t_out'] == f.index[-1] + H
    f = _lot_bars().drop(T0 + 4 * H)
    assert O.oracle_lot(f, T0 + 3 * H, 1, 1.0, RULE) == dict(refused='missing_next_hour')


if __name__ == '__main__':
    fs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for f in fs: f(); print('PASS', f.__name__)
