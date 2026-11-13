"""EXTRA31-SCREEN: the frozen pass rule, the 1h -> 4h/1d bar builder and the late-listing panel (research only)."""
import os, sys, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'research', 'extra31_screen'))
import run_screen as RS


def _tr(rows):
    return pd.DataFrame(rows, columns=['sym', 't_in', 'R7', 'R15', 'R23']).assign(t_in=lambda d: pd.to_datetime(d.t_in))


def _good(n=60):
    rows = []
    for i in range(n):
        rows.append(('A', '2023-06-01', 0.5, 0.4, 0.3)); rows.append(('B', '2025-06-01', 0.5, 0.4, 0.3))
    return rows


def test_contract_is_frozen_shape():
    c = RS.CONTRACT
    assert len(c['universe']['extra31']) == 31 and len(set(c['universe']['extra31'])) == 31
    assert not set(c['universe']['extra31']) & {'ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT'}
    assert c['pass_rule']['all_five_required'] is True
    assert RS.MIN_TRADES == 100 and RS.SPLIT == '2024-09-14 00:00' and abs(RS.RISK * RS.START - 8.0) < 1e-9
    assert set(RS.SKIP) == {'cp_x2_alt_btc_ratio_break_4h', 'rank_breadth_reversal_4h'}


def test_pass_needs_every_gate():
    s, g, st = RS.verdict(_tr(_good()))
    assert s == 'PASS' and all(g.values()) and st['n'] == 120


def test_fail_under_100_trades():
    s, g, _ = RS.verdict(_tr(_good(49)))
    assert s == 'FAIL' and not g['trades'] and g['meanR15']


def test_fail_when_one_half_negative():
    rows = [('A', '2023-06-01', 2.0, 2.0, 2.0)] * 60 + [('B', '2025-06-01', -0.1, -0.1, -0.1)] * 60
    s, g, _ = RS.verdict(_tr(rows))
    assert s == 'FAIL' and g['meanR15'] and not g['halves']


def test_fail_when_best_coin_carries_it():
    rows = [('A', '2023-06-01', 3.0, 3.0, 3.0), ('A', '2025-06-01', 3.0, 3.0, 3.0)] * 30
    rows += [('B', '2023-06-01', -0.1, -0.1, -0.1), ('B', '2025-06-01', -0.1, -0.1, -0.1)] * 30
    s, g, st = RS.verdict(_tr(rows))
    assert s == 'FAIL' and g['halves'] and not g['ex_best_coin'] and st['best_coin'] == 'A'


def test_fail_single_coin_has_no_ex_best():
    s, g, _ = RS.verdict(_tr([('A', '2023-06-01', 1.0, 1.0, 1.0), ('A', '2025-06-01', 1.0, 1.0, 1.0)] * 60))
    assert s == 'FAIL' and not g['ex_best_coin']


def test_zero_at_15_fails_zero_at_23_passes_gate():
    rows = [('A', '2023-06-01', 0.1, 0.0, -0.1), ('B', '2025-06-01', 0.1, 0.0, -0.1)] * 60
    _, g, _ = RS.verdict(_tr(rows))
    assert not g['meanR15'] and not g['meanR23']
    rows = [('A', '2023-06-01', 0.2, 0.1, 0.0), ('B', '2025-06-01', 0.2, 0.1, 0.0)] * 60
    _, g, _ = RS.verdict(_tr(rows))
    assert g['meanR15'] and g['meanR23']


def test_no_trades_fails():
    assert RS.verdict(_tr([]))[0] == 'FAIL'


def _h(n, start='2024-01-01'):
    t = pd.date_range(start, periods=n, freq='1h')
    x = np.arange(n, dtype=float) + 10
    return pd.DataFrame(dict(t=t, o=x, h=x + 2, l=x - 1, c=x + 1, v=1.0, qv=5.0))


def test_4h_bins_need_all_four_bars():
    d = _h(48).drop(index=[5]).reset_index(drop=True)          # the 04:00 bin of day 1 is incomplete
    b = RS.to_tf(d, '4h')
    assert len(b) == 11 and pd.Timestamp('2024-01-01 04:00') not in set(b.t)
    r = b.iloc[0]
    assert (r.o, r.h, r.l, r.c, r.v, r.qv) == (10.0, 15.0, 9.0, 14.0, 4.0, 20.0)


def test_1d_bins_need_24_bars():
    b = RS.to_tf(_h(24 * 3 - 1), '1d')
    assert len(b) == 2 and b.iloc[1].c == _h(48).iloc[-1].c


def test_cohorts_never_invent_candles():
    raw = {'BTCUSDT': _h(100), 'OLD': _h(100), 'LATE': _h(60, '2024-01-02 16:00'), 'LATE2': _h(60, '2024-01-02 16:00')}
    got = list(RS.cohorts(raw))
    assert [(str(s), tr, sorted(f)) for s, tr, f in got] == [
        ('2024-01-01 00:00:00', ['OLD'], ['BTCUSDT', 'OLD']),
        ('2024-01-02 16:00:00', ['LATE', 'LATE2'], ['BTCUSDT', 'LATE', 'LATE2', 'OLD'])]
    f = got[1][2]
    assert all(len(d) == 60 and d.t.iloc[0] == pd.Timestamp('2024-01-02 16:00') and d.c.notna().all() for d in f.values())
    assert f['OLD'].c.iloc[0] == raw['OLD'].c.iloc[40]


def test_load_reads_ms_timestamps_and_rejects_duplicates(tmp_path):
    d = _h(5); d['t'] = d.t.astype('datetime64[ms]').astype('int64')
    d.to_csv(tmp_path / 'XUSDT_1h.csv', index=False, encoding='utf-8')
    x = RS.load_1h(str(tmp_path), 'XUSDT')
    assert x.t.iloc[0] == pd.Timestamp('2024-01-01') and list(x.columns) == ['t', 'o', 'h', 'l', 'c', 'v', 'qv']
    pd.concat([d, d.iloc[:1]]).to_csv(tmp_path / 'YUSDT_1h.csv', index=False, encoding='utf-8')
    try:
        RS.load_1h(str(tmp_path), 'YUSDT'); assert False
    except ValueError:
        pass
