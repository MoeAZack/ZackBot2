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


# ---------------------------------------------------------------- SIEVE-V1 (Codex rule v1)
import sieve_v1 as SV


def test_sieve_cases_and_constants():
    c = SV.CASES['cases']
    assert SV.RISK_USD == 8.0 and SV.CAP_USD == 3333.33 and SV.SPLIT == '2025-01-01 00:00' and SV.T0 == '2022-08-26 06:00'
    assert len(SV.case_coins('ema_st')) == 31 and len(SV.case_coins('c101')) == 23
    assert not set(SV.case_coins('c101')) & set(c['c101']['prior_used']) and len(c['c101']['prior_used']) == 8
    assert c['ema_st']['management'] == {'stop_atr': 2.5, 'exit': c['ema_st']['management']['exit']}
    assert {k: v for k, v in c['c101']['management'].items() if k != 'exit'} == {'stop_atr': 1.5, 'tp_r': 1.5, 'max_bars': 8}


def test_sieve_orders_are_fixed():
    coins = SV.case_coins('ema_st')
    o = SV.orders(coins, 40)
    assert len(o) == 40 and o[0] == sorted(coins) and all(sorted(x) == sorted(coins) for x in o)
    assert o == SV.orders(coins, 40) and len({tuple(x) for x in o}) == 40
    assert o[1] == [sorted(coins)[i] for i in np.random.default_rng(20261113).permutation(31)]


def _st(n=120, r=0.2, e=0.2, l=0.2, x=0.1, r23=0.1, ne=60, nl=60):
    return dict(n=n, n_early=ne, n_late=nl, meanR7=r, meanR15=r, meanR23=r23, meanR15_early=e, meanR15_late=l, meanR15_ex_best=x)


def test_sieve_gates_use_medians_and_order0_sample():
    M, g = SV.gates([_st(), _st(r=-5.0), _st()])
    assert all(g.values()) and M['meanR15'] == 0.2                    # one bad order does not move the median
    assert not SV.gates([_st(r=-0.1), _st(r=-0.1), _st()])[1]['meanR15']
    assert not SV.gates([_st(n=99), _st(n=99), _st()])[1]['trades']
    assert not SV.gates([_st(ne=29), _st(), _st()])[1]['order0_30_per_half']
    assert SV.gates([_st(), _st(ne=5), _st()])[1]['order0_30_per_half']   # only order 0 carries the sample gate
    assert not SV.gates([_st(r23=-0.01)] * 3)[1]['meanR23'] and SV.gates([_st(r23=0.0)] * 3)[1]['meanR23']
    assert not SV.gates([_st(x=0.0)] * 3)[1]['ex_best_coin'] and not SV.gates([_st(e=0.0)] * 3)[1]['early']
    assert not any(SV.gates([dict(n=0, n_early=0, n_late=0)] * 3)[1].values())


def test_sieve_order_stats_r_budget_and_best_coin_by_pnl():
    tr = pd.DataFrame(dict(sym=['A', 'A', 'B', 'B'], t_in=pd.to_datetime(['2024-01-01', '2025-01-01', '2024-06-01', '2025-06-01']),
                           pnl7=[16.0, 16.0, 4.0, 4.0], pnl15=[16.0, 8.0, 4.0, -4.0], pnl23=[8.0, 0.0, 0.0, -8.0], R=[2.0, 1.0, 0.5, -0.5]))
    s = SV.order_stats(tr)
    assert s['n'] == 4 and s['n_early'] == 2 and s['n_late'] == 2                # the split bar itself is "later"
    assert s['meanR15'] == 0.75 and s['meanR23'] == 0.0 and s['meanR15_early'] == 1.25 and s['meanR15_late'] == 0.25
    assert s['best_coin'] == 'A' and s['meanR15_ex_best'] == 0.0 and s['n_ex_best'] == 2


def test_sieve_liquidity_needs_30_complete_days_and_720_bars():
    d = _h(24 * 40); d['qv'] = 1e7 / 24 + 1.0
    e = SV.liquidity_1h(d)
    assert not e.iloc[:24 * 30].any() and e.iloc[24 * 30:].all()
    d2 = d.copy(); d2.loc[d2.index >= 24 * 35, 'qv'] = 1.0                        # thin days enter the median only later
    assert SV.liquidity_1h(d2).iloc[24 * 35]
    d3 = d.drop(index=[24 * 31]).reset_index(drop=True)                           # a gap restarts the 720-bar run
    assert not SV.liquidity_1h(d3).iloc[24 * 31:].any()
    d4 = d.copy(); d4['qv'] = 1e7 / 24 - 1.0
    assert not SV.liquidity_1h(d4).any()


# ---------------------------------------------------------------- SIEVE-V1 panel form
import sieve_v1_panel as SP


def test_panel_cases_are_the_screen_survivors_with_pinned_management():
    c = SP.CASES['cases']
    assert set(c) == {'exp_trend', 'bear_breakdown', 'rotation', 'cp_n3_turtle_daily', 'cp_y2_breadth_thrust_1d', 'ema_st_panel'}
    assert all(len(SP.case_coins(k)) == 31 and v['label'].startswith('CONSUMED_SYMBOL_TRANSFER_DIAGNOSTIC') for k, v in c.items())
    assert c['rotation']['params'] == {'k': 5} and c['bear_breakdown']['management'] == {'stop_atr': 2.0, 'trail_atr': 3.0}
    assert all(v['source'] in RS.CONTRACT['sources'] and RS.CONTRACT['sources'][v['source']]['commit'] == v['commit'] for v in c.values())


def test_cut_ctx_keeps_scalars_and_cuts_aligned_fields():
    ctx = dict(btc_down=np.array([True, False, True, False]), rank=pd.Series([1.0, 2.0, 3.0, 4.0]), n_symbols=7, other=np.array([1, 2]))
    o = SP.cut_ctx(ctx, 2, 4)
    assert list(o['btc_down']) == [True, False] and list(o['rank']) == [3.0, 4.0] and list(o['rank'].index) == [0, 1]
    assert o['n_symbols'] == 7 and list(o['other']) == [1, 2]


def test_mp_variant_is_frozen_and_default_is_rule_v1():
    import json, os, inspect
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'research', 'extra31_screen')
    v = json.load(open(os.path.join(here, 'sieve_v1_mp_variant.json'), encoding='utf-8'))
    cases = json.load(open(os.path.join(here, 'sieve_v1_panel_cases.json'), encoding='utf-8'))['cases']
    assert v['max_pos'] == [3, 5] and len(v['cases']) == 6 and set(v['cases']) <= set(cases)
    assert 'POST_HOC' in v['label'] and max(v['max_pos']) * 8 <= 0.025 * 4000
    src = open(os.path.join(here, 'sieve_v1_panel.py'), encoding='utf-8').read()
    assert "'--max-pos', type=int, default=1, choices=[1, 3, 5]" in src and 'max_pos=mp, symbols=order' in src
    assert "'TRANSFER_SCREEN_PASS' if mp == 1 else" in src
