"""B4 funding_context_shadow: the point-in-time feature and the research comparison tool. Synthetic data only."""
import json, math, os, sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import funding_context as FC                                   # noqa: E402
from tools.research import funding_shadow_compare as CMP       # noqa: E402

UTC = timezone.utc
T0 = datetime(2025, 1, 1, tzinfo=UTC)
MS = timedelta(milliseconds=1)
INFO8 = {'fundingIntervalHours': 8}


def ms(d):
    return FC.utc_ms(d)


def series(n, step_h=8, start=T0, rate=lambda i: 0.0001 + 0.00001 * math.sin(i)):
    return [(ms(start + timedelta(hours=step_h * i)), rate(i)) for i in range(n)]


def at(rows, i):
    return datetime.fromtimestamp(rows[i][0] / 1000, UTC)


# ------------------------------------------------------------------ visibility
def test_visibility_boundary_exactly_at_t_vs_plus_1ms():
    rows = series(120)
    rows[-1] = (rows[-1][0], 0.01)                                         # a spike as the last settlement
    t = at(rows, 119)
    a = FC.funding_ctx(rows, INFO8, t, 'LONG')                             # settlement exactly at the close counts
    b = FC.funding_ctx(rows, INFO8, t - MS, 'LONG')                        # 1 ms earlier: not yet visible
    assert a['status'] == b['status'] == 'OK'
    assert a['last'] == 0.01 and b['last'] != 0.01 and a['age_h'] == 0
    # a settlement 1 ms after t is invisible
    rows2 = rows[:-1] + [(ms(t) + 1, 0.01)]
    c = FC.funding_ctx(rows2, INFO8, t, 'LONG')
    assert c['last'] == b['last'] and c['last'] != 0.01


def test_future_rows_never_change_the_result():
    rows = series(200)
    t = at(rows, 150) + timedelta(hours=3)
    a = FC.funding_ctx(rows[:151], INFO8, t, 'SHORT')
    fut = rows[:151] + [(r[0], 5.0) for r in rows[151:]]
    assert FC.funding_ctx(fut, INFO8, t, 'SHORT') == a == FC.funding_ctx(rows, INFO8, t, 'SHORT')


def test_derived_interval_log_is_point_in_time():
    rows = series(120)
    log = FC.derive_interval_log(rows)
    t = at(rows, 119)
    assert FC.funding_ctx(rows, log, t, 'LONG') == FC.funding_ctx(rows, INFO8, t, 'LONG')
    # the derived log built from the FULL history must not leak: same answer as a log built from visible rows only
    for k in (95, 100, 110):
        tk = at(rows, k) + timedelta(hours=1)
        assert FC.funding_ctx(rows, FC.derive_interval_log(rows), tk, 'LONG') == \
            FC.funding_ctx(rows[:k + 1], FC.derive_interval_log(rows[:k + 1]), tk, 'LONG')


# ------------------------------------------------------------------ intervals
def interval_change_rows(new_h=4, n_old=60, n_new=200):
    a = series(n_old, 8)
    start = at(a, n_old - 1) + timedelta(hours=new_h)
    b = series(n_new, new_h, start=start, rate=lambda i: 0.0001 + 0.00002 * math.cos(i))
    return a + b, n_old


@pytest.mark.parametrize('new_h', [4, 2])
def test_interval_change_mid_history_8_to_shorter(new_h):
    rows, k = interval_change_rows(new_h)
    log = FC.derive_interval_log(rows)
    assert [e.interval_h for e in log] == [8, new_h]
    assert log[1].effective_from_ms == rows[k][0] and log[1].known_at_ms == rows[k + 1][0]
    t = at(rows, len(rows) - 1)
    r = FC.funding_ctx(rows, log, t, 'LONG')
    assert r['status'] == 'OK' and r['interval_h'] == new_h
    # a static 8h claim (wrong for the new regime) still sees no gap but the age rule uses 8h: OK too
    # stale under the NEW interval: 1.5 x new_h + 1 min after the last settlement
    late = t + timedelta(hours=1.5 * new_h, minutes=1)
    assert FC.funding_ctx(rows, log, late, 'LONG')['reasons'] == ['stale']


def test_interval_change_4_to_8_ambiguous_until_confirmed():
    a = series(200, 4)
    start = at(a, 199) + timedelta(hours=8)
    rows = a + series(5, 8, start=start)
    log = FC.derive_interval_log(rows)
    assert [e.interval_h for e in log] == [4, 8]
    first8, confirm = at(rows, 200), at(rows, 201)
    r1 = FC.funding_ctx(rows, log, first8, 'LONG')          # one 8h spacing on a 4h symbol: gap until confirmed
    assert r1['status'] == 'INCOMPLETE' and 'gap' in r1['reasons']
    r2 = FC.funding_ctx(rows, log, confirm, 'LONG')         # confirmed by the next settlement: in force from first8
    assert r2['status'] == 'OK' and r2['interval_h'] == 8


def test_funding_info_rows_first_seen_after_t_are_invisible():
    rows = series(120)
    info = [{'symbol': 'XUSDT', 'fundingIntervalHours': '8', 'first_seen_utc': '2026-10-14T06:15:32Z'}]
    log = FC.interval_log_from_funding_info(info, 'XUSDT')
    t = at(rows, 119)
    r = FC.funding_ctx(rows, log, t, 'LONG')
    assert r['status'] == 'INCOMPLETE' and r['reasons'] == ['no_interval_in_force']
    late = datetime(2026, 10, 15, tzinfo=UTC)
    assert FC.funding_ctx(rows, log, late, 'LONG')['reasons'] == ['stale']   # visible now, but the history is old


# ------------------------------------------------------------------ INCOMPLETE rules
def test_gap_inside_lookback_is_incomplete():
    rows = series(120)
    del rows[100]                                                           # one missing settlement (16h spacing)
    r = FC.funding_ctx(rows, INFO8, at(rows, len(rows) - 1), 'LONG')
    assert r['status'] == 'INCOMPLETE' and r['reasons'] == ['gap']


def test_gap_older_than_lookback_is_ignored():
    rows = series(200)
    del rows[10]
    assert FC.funding_ctx(rows, INFO8, at(rows, len(rows) - 1), 'LONG')['status'] == 'OK'


def test_gap_straddling_window_start_counts():
    rows = series(200)
    t = at(rows, 199)
    edge = ms(t - timedelta(days=30))
    i = next(i for i, r in enumerate(rows) if r[0] > edge)                 # first settlement inside the window
    del rows[i - 1]                                                         # its predecessor (outside) goes missing
    assert 'gap' in FC.funding_ctx(rows, INFO8, t, 'LONG')['reasons']


def test_stale_settlement_is_incomplete():
    rows = series(120)
    t = at(rows, 119)
    assert FC.funding_ctx(rows, INFO8, t + timedelta(hours=12), 'LONG')['status'] == 'OK'          # exactly 1.5 x 8h
    r = FC.funding_ctx(rows, INFO8, t + timedelta(hours=12) + MS, 'LONG')
    assert r['status'] == 'INCOMPLETE' and r['reasons'] == ['stale']


@pytest.mark.parametrize('hist,info,why', [
    (None, INFO8, 'no_history'), ([], INFO8, 'no_history'),
    ('ROWS', None, 'no_funding_info'), ('ROWS', {}, 'no_funding_info'), ('ROWS', [], 'no_funding_info')])
def test_missing_symbol_or_funding_info(hist, info, why):
    rows = series(120)
    h = rows if hist == 'ROWS' else hist
    r = FC.funding_ctx(h, info, at(rows, 119), 'LONG')
    assert r['status'] == 'INCOMPLETE' and why in r['reasons']


def test_less_than_30_days_is_incomplete():
    rows = series(120)
    t = at(rows, 0) + timedelta(days=30)
    assert FC.funding_ctx(rows, INFO8, t, 'LONG')['status'] == 'OK'                 # first settlement exactly t - 30d
    r = FC.funding_ctx(rows, INFO8, t - MS, 'LONG')
    assert r['status'] == 'INCOMPLETE' and r['reasons'] == ['lt_30d_history']


def test_zero_std_and_bad_rate_are_incomplete():
    flat = series(120, rate=lambda i: 0.0001)
    assert FC.funding_ctx(flat, INFO8, at(flat, 119), 'LONG')['reasons'] == ['zero_std']
    bad = series(120); bad[110] = (bad[110][0], '')
    assert FC.funding_ctx(bad, INFO8, at(bad, 119), 'LONG')['reasons'] == ['bad_rate']


def test_incomplete_never_carries_numbers():
    rows = series(120)
    cases = [FC.funding_ctx(rows, None, at(rows, 119), 'LONG'), FC.funding_ctx(rows[:20], INFO8, at(rows, 19), 'LONG'),
             FC.funding_ctx(rows, INFO8, at(rows, 119) + timedelta(days=3), 'SHORT'), FC.funding_ctx(None, None, T0, 1)]
    for r in cases:
        assert r['status'] == 'INCOMPLETE' and r['reasons']
        assert all(r[k] is None for k in FC.NUMERIC)
        assert FC.counterfactual_drop(r) is False


# ------------------------------------------------------------------ values, mirror, time types
def test_values_match_a_hand_computation():
    rows = series(120)
    t = at(rows, 119) + timedelta(hours=2)
    r = FC.funding_ctx(rows, INFO8, t, 'LONG')
    win = [x for s, x in rows if s > ms(t) - 30 * 86_400_000]
    m = sum(win) / len(win); sd = math.sqrt(sum((x - m) ** 2 for x in win) / (len(win) - 1))
    assert len(win) == 90
    assert r['last'] == rows[-1][1] and r['z30'] == pytest.approx((rows[-1][1] - m) / sd)
    assert r['mean3'] == pytest.approx(sum(x for _, x in rows[-3:]) / 3) and r['age_h'] == 2.0 and r['interval_h'] == 8


@pytest.mark.parametrize('sign', [1, -1])
def test_long_short_mirror_crowd_flips(sign):
    rows = series(120, rate=lambda i: sign * (0.0001 + 0.00001 * math.sin(i)))
    t = at(rows, 119)
    lo, sh = FC.funding_ctx(rows, INFO8, t, 'LONG'), FC.funding_ctx(rows, INFO8, t, 'short')
    assert lo['crowd'] == sign and sh['crowd'] == -sign
    assert {k: v for k, v in lo.items() if k != 'crowd'} == {k: v for k, v in sh.items() if k != 'crowd'}
    assert FC.funding_ctx(rows, INFO8, t, 1) == lo and FC.funding_ctx(rows, INFO8, t, -1) == sh


def test_naive_datetime_raises():
    rows = series(120)
    with pytest.raises(ValueError):
        FC.funding_ctx(rows, INFO8, datetime(2025, 3, 1), 'LONG')
    with pytest.raises(ValueError):
        FC.prepare_history([(datetime(2025, 1, 1), 0.0001)])
    with pytest.raises(TypeError):
        FC.funding_ctx(rows, INFO8, ms(T0), 'LONG')
    with pytest.raises(ValueError):
        FC.funding_ctx(rows, INFO8, at(rows, 119), 'FLAT')


def test_non_utc_aware_time_is_converted():
    rows = series(120)
    t = at(rows, 119)
    cairo = t.astimezone(timezone(timedelta(hours=3)))
    assert FC.funding_ctx(rows, INFO8, cairo, 'LONG') == FC.funding_ctx(rows, INFO8, t, 'LONG')


def test_load_funding_csv_stops_at_cutoff(tmp_path):
    p = tmp_path / 'X.csv'
    rows = series(10)
    body = 'fundingTime,fundingRate,markPrice,time_utc\n' + ''.join(f'{s},{x},,\n' for s, x in rows)
    p.write_text(body + 'not-a-number,0,,\n', encoding='utf-8')            # a row past the cutoff is never parsed
    h = FC.load_funding_csv(str(p), at(rows, 5))
    assert h.times == tuple(s for s, _ in rows[:5])
    assert FC.load_funding_csv(str(tmp_path / 'missing.csv'), T0) is None


# ------------------------------------------------------------------ comparison tool (synthetic packets)
def _write_funding(root, sym, rows):
    d = root / 'funding_rate'; d.mkdir(parents=True, exist_ok=True)
    (d / f'{sym}.csv').write_text('fundingTime,fundingRate,markPrice,time_utc\n' +
                                  ''.join(f'{s},{x:.8f},,\n' for s, x in rows), encoding='utf-8')


def test_compare_tool_known_removed_set_and_arithmetic(tmp_path):
    base = series(200, rate=lambda i: 0.0001 + 0.00001 * math.sin(i))
    spike = base[:150] + [(base[150][0], 0.002)] + base[151:]             # z30 >> 2 at settlement 150, mean3 > 0
    _write_funding(tmp_path, 'AUSDT', spike)
    _write_funding(tmp_path, 'BUSDT', base)
    t_sp = at(spike, 150).strftime('%Y-%m-%dT%H:%M:%SZ')
    t_ok = at(base, 160).strftime('%Y-%m-%dT%H:%M:%SZ')
    t_new = at(base, 10).strftime('%Y-%m-%dT%H:%M:%SZ')                    # < 30 days of history -> INCOMPLETE
    T = [dict(symbol='AUSDT', side=1, t_in=t_sp, R=-1.0, pnl=-100.0, fees=10.0, regime='btc_above_ema200'),   # removed
         dict(symbol='AUSDT', side=-1, t_in=t_sp, R=2.0, pnl=200.0, fees=10.0, regime='btc_above_ema200'),   # crowd -1: kept
         dict(symbol='BUSDT', side=1, t_in=t_ok, R=1.5, pnl=150.0, fees=10.0, regime='btc_below_ema200'),    # z small: kept
         dict(symbol='CUSDT', side=1, t_in=t_ok, R=-0.5, pnl=-50.0, fees=10.0, regime='btc_below_ema200'),   # no file: INC
         dict(symbol='BUSDT', side=1, t_in=t_new, R=-1.0, pnl=-100.0, fees=10.0, regime='btc_above_ema200')] # <30d: INC
    src = CMP.FundingSource(str(tmp_path), CMP.SEALED_CUTOFF)
    res, tagged = CMP.compare(T, src)
    assert [d for _, _, d in tagged] == [True, False, False, False, False]
    assert res['removed'] == dict(n=1, net_r=-1.0, pf=0.0, net_r_2xfee=-1.1, pf_2xfee=0.0)
    f = res['filtered']
    assert (f['n'], f['net_r'], f['pf']) == (4, 2.0, round(3.5 / 1.5, 4))
    # 2x fee: fee R = fees / (pnl / R) = 0.1 for every trade
    assert f['net_r_2xfee'] == pytest.approx(2.0 - 0.4) and f['pf_2xfee'] == round(3.3 / 1.7, 4)
    assert res['baseline']['n'] == 5 and res['baseline']['net_r'] == 1.0
    assert res['incomplete']['n'] == 2 and res['incomplete']['share'] == 0.4
    assert res['incomplete']['reasons'] == {'no_history': 1, 'no_funding_info': 1, 'lt_30d_history': 1}
    assert res['by_regime']['btc_above_ema200']['removed']['n'] == 1
    assert sum(q['baseline']['n'] for q in res['by_quarter'].values()) == 5


def test_compare_run_writes_outputs_and_refuses_sealed(tmp_path):
    rows = series(200)
    _write_funding(tmp_path, 'BUSDT', rows)
    pk = dict(window=dict(end='2026-09-28T00:00:00Z'), side='long', strategy=dict(key='k', mgmt_mode='defaults'),
              universe=dict(book=['BUSDT']), trades=[dict(symbol='BUSDT', side=1, t_in=at(rows, 160).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                          R=1.0, pnl=10.0, fees=1.0, regime='btc_above_ema200')])
    (tmp_path / 'CORE-10_k_long.json').write_text(json.dumps(pk), encoding='utf-8')
    doc = CMP.run([str(tmp_path / 'CORE-10_*.json')], str(tmp_path / 'out'), str(tmp_path))
    assert doc['results'][0]['packet']['tier'] == 'CORE-10' and doc['results'][0]['incomplete']['n'] == 0
    assert (tmp_path / 'out' / 'funding_shadow_compare.txt').exists()
    pk['trades'][0]['t_in'] = '2026-09-28T00:00:00Z'
    (tmp_path / 'CORE-10_k_long.json').write_text(json.dumps(pk), encoding='utf-8')
    with pytest.raises(ValueError):
        CMP.run([str(tmp_path / 'CORE-10_*.json')], str(tmp_path / 'out'), str(tmp_path))
    with pytest.raises(ValueError):
        CMP.run([str(tmp_path / 'CORE-10_*.json')], str(tmp_path / 'out'), str(tmp_path),
                cutoff=datetime(2026, 10, 1, tzinfo=UTC))


# ------------------------------------------------------------------ self-review fixes
@pytest.mark.parametrize('bad', [{'fundingIntervalHours': ''}, {'fundingIntervalHours': 'x'}, {'fundingIntervalHours': 0},
                                 {'fundingIntervalHours': -8}, {'fundingIntervalHours': 7.5},
                                 [{'interval_h': None, 'effective_from_ms': None, 'known_at_ms': None}]])
def test_malformed_funding_info_is_incomplete_not_guessed(bad):
    rows = series(120)
    r = FC.funding_ctx(rows, bad, at(rows, 119), 'LONG')
    assert r['status'] == 'INCOMPLETE' and 'bad_funding_info' in r['reasons'] and r['z30'] is None


def test_ms_jitter_settlement_is_not_visible_at_the_round_close():
    rows = series(120)
    t = at(rows, 119)
    jit = rows[:-1] + [(rows[-1][0] + 13, 0.01)]                          # Binance-style 13 ms late settlement
    assert FC.funding_ctx(jit, INFO8, t, 'LONG')['last'] != 0.01
    assert FC.funding_ctx(jit, INFO8, t + 13 * MS, 'LONG')['last'] == 0.01
    assert FC.derive_interval_log(jit)[0].interval_h == 8                  # jitter still snaps to 8h
