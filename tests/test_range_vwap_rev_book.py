"""REX-01 synthetic fixtures: adapter, X1 book, intrabar order, missing data, partial / risk, range gate, causal
prefix and book sizing (groups F1-F4, F6-F8, F10). Generated tapes only; no market data, no holdout."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as S                                                                       # noqa: E402
from rex_synth import ms, rex                                                               # noqa: E402

RV = S.RV
LE = S.long_entry(5)                                 # long signal at the close of bar 5 -> fill at the open of bar 6


def one(**kw):
    return {'AAA': S.build(**kw)}


def rel(side, p):
    """A long-terms price seen from `side` (the mirror around 100)."""
    return p if side == 'long' else 200.0 - p


def book(side, n, **kw):
    tape = one(n=n, side=side, **kw)
    return tape, S.run(tape, side, n)


def ov(tape, side, res, arm, n):
    (tr,) = res['trades']
    return RV.overlay(tape, RV.PRIMARY, side, tr, arm, end=ms(n))


def assert_mirror(a, b):
    """Long result `a` vs short result `b`: identical event structure, prices mirrored around 100 (within a tick of
    adverse rounding), R equal within 0.02."""
    ka = [(t['entry_ms'], t['exit_ms'], t['reason'], t['qty'], t['gap']) for t in a['trades']]
    kb = [(t['entry_ms'], t['exit_ms'], t['reason'], t['qty'], t['gap']) for t in b['trades']]
    assert ka == kb
    for x, y in zip(a['trades'], b['trades']):
        for f in ('fill', 'stop', 'exit_fill'):
            assert abs(y[f] - (200.0 - x[f])) <= 0.0201
        assert abs(x['r'] - y['r']) < 0.02 and x['z'] == -y['z']
    assert a['status'] == b['status'] and a['refusals'] == b['refusals']


# ------------------------------------------------------------------ F1 symmetry (7 + 7)
F1 = {'entry': (12, dict(vwap=LE), None), 'x1': (16, dict(vwap=LE, close={12: 104.0}), None),
      'x2': (16, dict(vwap=LE, close={12: 104.0}), 'x2'), 'x3': (16, dict(vwap=LE, close={12: 104.0}), 'x3'),
      'invalidation': (12, dict(vwap={**LE, 8: 106.0}), None),
      'stop': (12, dict(vwap=LE, ohlc={8: (100, 101, 97.0, 99)}), None), 'cap': (40, dict(vwap=LE), None)}


@rex('F1', list(F1), sides=True)
def test_f1_symmetry(side, case):
    n, kw, arm = F1[case]
    other = 'short' if side == 'long' else 'long'
    (ta, a), (tb, b) = book(side, n, **kw), book(other, n, **kw)
    lng, sht = (a, b) if side == 'long' else (b, a)
    assert_mirror(lng, sht)
    if case == 'entry':
        assert a['status'] == RV.INCOMPLETE and a['censor']['reason'] == 'incomplete.split_end'
        assert a['signals'] == [('AAA', ms(6), -1.5 if side == 'long' else 1.5)]
        lot = a['open_lots']['AAA']
        assert (lot['fill'], lot['stop'], lot['qty']) == (rel(side, 100.1), rel(side, 97.1), 33.222)
        return
    assert a['status'] == RV.COMPLETE
    (tr,) = a['trades']
    want = {'x1': ('exit.mean', ms(13)), 'x2': ('exit.mean', ms(13)), 'x3': ('exit.mean', ms(13)),
            'invalidation': ('exit.invalidation', ms(9)), 'stop': ('exit.stop', ms(8)), 'cap': ('exit.cap', ms(36))}
    assert (tr['reason'], tr['exit_ms']) == want[case]
    if arm:
        pa, pb = ov(ta, side, a, arm, n), ov(tb, other, b, arm, n)
        assert pa['status'] == pb['status'] == RV.COMPLETE
        assert [(s['reason'], s['exit_ms'], s['qty']) for s in pa['slices']] == \
               [(s['reason'], s['exit_ms'], s['qty']) for s in pb['slices']]
        assert abs(pa['r'] - pb['r']) < 0.02
        if arm == 'x2':
            assert [s['reason'] for s in pa['slices']] == ['exit.partial', 'exit.breakeven']
        else:
            (sl,) = pa['slices']
            assert sl['reason'] == 'exit.stop' and sl['exit_ms'] == ms(13)
            assert (pa['path'][-1][1] > 97.1) if side == 'long' else (pa['path'][-1][1] < 102.9)


# ------------------------------------------------------------------ F2 boundary / re-entry (5 + 5)
F2 = ['z_prev_eq_k', 'z_eq_k', 'reentry_at_exit_fill', 'reentry_after_exit_fill', 'opposite_side']
STOP9 = {9: (100, 101, 96.0, 100)}


@rex('F2', F2, sides=True)
def test_f2_boundary_reentry(side, case):
    if case == 'z_prev_eq_k':                        # z_(i-1) == -k exactly: entry (<=)
        tape, r = book(side, 12, vwap={-1: 103.0, 4: 104.0, 5: 103.0})
        assert [x[1] for x in r['signals']] == [ms(6)]
    elif case == 'z_eq_k':                           # z_i == -k exactly: no entry (needs > -k)
        tape, r = book(side, 12, vwap={-1: 103.0, 4: 105.0, 5: 104.0})
        assert r['signals'] == [] and r['status'] == RV.COMPLETE
    elif case in ('reentry_at_exit_fill', 'reentry_after_exit_fill'):
        at = case == 'reentry_at_exit_fill'
        vw = {**LE, 8: 105.0, 9: 103.0} if at else {**LE, 9: 105.0, 10: 103.0}
        tape, r = book(side, 14, vwap=vw, ohlc=STOP9)
        assert r['trades'][0]['reason'] == 'exit.stop' and r['trades'][0]['exit_ms'] == ms(9)
        j = 9 if at else 10                          # the decision bar of the second signal
        ind = RV.indicators(RV.PRIMARY, RV.read(tape['AAA'], ms(j + 1), RV.PRIMARY.lookback))
        assert RV.entry_x1(RV.PRIMARY, ind, side)    # the rule fires at that close ...
        entries = [x[1] for x in r['signals']]
        if at:                                       # ... but trigger bar 8 closes AT the exit fill (open of 9)
            assert entries == [ms(6)]
        else:                                        # trigger bar 9 closes strictly after it
            assert entries == [ms(6), ms(11)]
    else:                                            # the other side's pattern never enters this side's book
        other = 'short' if side == 'long' else 'long'
        tape = one(n=12, vwap=LE, side=other)
        r = S.run(tape, side, 12)
        assert r['signals'] == [] and r['trades'] == []


# ------------------------------------------------------------------ F3 intrabar order / stop reference (9 + 9)
F3 = ['stop_plus_mean', 'gap', 'pending_exit_plus_gap', 'partial_plus_gap_original', 'partial_then_gap_breakeven',
      'trail_own_bar', 'exact_touch', 'one_tick_through', 'mark_only_breach']


@rex('F3', F3, sides=True)
def test_f3_intrabar(side, case):
    if case == 'stop_plus_mean':                     # stop touched and a mean close in the same bar -> stop
        tape, r = book(side, 12, vwap=LE, ohlc={8: (100, 104.5, 97.0, 103.5)})
        (t,) = r['trades']
        assert t['reason'] == 'exit.stop' and t['exit_ms'] == ms(8) and not t['gap']
        assert r['counts'].get('same_bar_stop_priority') == 1
    elif case == 'gap':                              # opens through the stop: gap fill at the open
        tape, r = book(side, 12, vwap=LE, ohlc={8: (96.5, 98, 95, 97.5)})
        (t,) = r['trades']
        assert t['reason'] == 'exit.stop' and t['gap'] and t['exit_ms'] == ms(8)
        assert abs(t['exit_fill'] - rel(side, 96.5)) < 0.5 and t['r'] < -1
    elif case == 'pending_exit_plus_gap':            # a pending mean exit and a gap open: the gap stop row
        tape, r = book(side, 12, vwap=LE, close={7: 103.5}, ohlc={8: (96.5, 98, 95, 97.5)})
        (t,) = r['trades']
        assert t['reason'] == 'exit.stop' and t['gap']
        _, r0 = book(side, 12, vwap=LE, close={7: 103.5})
        assert r0['trades'][0]['reason'] == 'exit.mean'
    elif case == 'partial_plus_gap_original':        # X2: open through the original stop -> whole gap stop
        tape, r = book(side, 12, vwap=LE, close={7: 103.5}, ohlc={8: (96.5, 98, 95, 97.5)})
        p = ov(tape, side, r, 'x2', 12)
        assert [(s['reason'], s['gap'], s['qty']) for s in p['slices']] == [('exit.stop', True, 33.222)]
    elif case == 'partial_then_gap_breakeven':       # partial fills, the next open gaps through breakeven
        tape, r = book(side, 12, vwap=LE, close={7: 103.5},
                       ohlc={8: (103.5, 104, 101, 101.5), 9: (99.5, 100, 98.5, 99)})
        p = ov(tape, side, r, 'x2', 12)
        assert [(s['reason'], s['exit_ms'], s['gap']) for s in p['slices']] == \
               [('exit.partial', ms(8), False), ('exit.breakeven', ms(9), True)]
    elif case == 'trail_own_bar':                    # X3: a stop set at close i never acts on bar i
        tape, r = book(side, 12, vwap=LE, ohlc={7: (100, 104.5, 99.5, 104)})
        p = ov(tape, side, r, 'x3', 12)
        path = dict((bo, st) for bo, st, _ in p['path'])
        assert path[ms(7)] == rel(side, 97.1)        # bar 7 low 99.5 is below its own trail: no exit in bar 7
        assert p['slices'][0]['exit_ms'] == ms(8) and path[ms(8)] == p['path'][-1][1]
        assert (path[ms(8)] > 99.5) if side == 'long' else (path[ms(8)] < 100.5)
    elif case in ('exact_touch', 'one_tick_through'):
        low = 97.1 if case == 'exact_touch' else 97.09
        tape, r = book(side, 12, vwap=LE, ohlc={8: (100, 101, low, 99)})
        (t,) = r['trades']
        assert t['reason'] == 'exit.stop' and not t['gap'] and t['exit_ms'] == ms(8)
        assert abs(t['exit_fill'] - rel(side, 97.1)) < 0.2       # filled at the stop (slipped), not at the low
        _, r1 = book(side, 12, vwap=LE, ohlc={8: (100, 101, 97.11, 99)})
        assert r1['status'] == RV.INCOMPLETE and r1['trades'] == []           # one tick short: no trigger
    else:                                            # the mark breaches the stop, last price does not: no trigger
        tape = one(n=12, side=side, vwap=LE)
        for h in range(ms(8), ms(9), S.HOUR):
            tape['AAA'].marks[h] = rel(side, 90.0)
        r = S.run(tape, side, 12)
        assert r['trades'] == [] and r['open_lots']['AAA']['entry_ms'] == ms(6)


# ------------------------------------------------------------------ F4 gaps / delist (7 + 7)
F4 = ['held_hole', 'entry_without_next_open', 'eligible_flat_hole', 'slippage_window_hole',
      'held_series_end_no_notice', 'notice_exit_first_open_after', 'flat_series_end_no_notice']


@rex('F4', F4, sides=True)
def test_f4_gaps_delist(side, case):
    if case == 'held_hole':
        tape, r = book(side, 12, vwap=LE, holes=(8,))
        assert r['status'] == RV.INCOMPLETE and r['trades'] == []
        (row,) = r['incomplete']
        assert (row['reason'], row['entry_ms'], row['first_unaccountable_ms']) == ('incomplete.gap', ms(6), ms(8))
    elif case == 'entry_without_next_open':
        tape, r = book(side, 12, vwap=LE, holes=(6,))
        (row,) = r['incomplete']
        assert (row['reason'], row['phase'], row['entry_ms']) == ('incomplete.gap', 'next_open', None)
    elif case == 'eligible_flat_hole':
        tape, r = book(side, 20, holes=(10,))
        (row,) = r['incomplete']
        assert (row['reason'], row['phase'], row['first_unaccountable_ms']) == ('incomplete.gap', 'decision', ms(10))
        assert r['censor']['last_valid_ms'] == ms(10)
    elif case == 'slippage_window_hole':
        x = S.build(n=12, side=side, holes=(3,))
        with pytest.raises(RV.Incomplete) as e:
            RV.bps_at(x, ms(10), RV.C.STRESS['base'])
        assert e.value.reason == 'incomplete.slip_history' and e.value.first_ms == ms(3)
        r = S.run({'AAA': x}, side, 12)
        assert r['status'] == RV.INCOMPLETE                                  # never a completed fill
    elif case == 'held_series_end_no_notice':
        tape, r = book(side, 14, vwap=LE, holes=tuple(range(10, 14)))
        (row,) = r['incomplete']
        assert (row['reason'], row['entry_ms']) == ('incomplete.delisted', ms(6))
    elif case == 'notice_exit_first_open_after':
        tape = one(n=14, side=side, vwap=LE, holes=tuple(range(11, 14)))
        nv = RV.NoticeView({'AAA': ms(9) + S.HOUR})
        r = S.run(tape, side, 14, notices=nv)
        (t,) = r['trades']
        assert (t['reason'], t['exit_ms']) == ('exit.delist', ms(10)) and r['status'] == RV.COMPLETE
        r2 = S.run(one(n=14, side=side, vwap=LE, holes=tuple(range(11, 14))), side, 14,
                   notices=RV.NoticeView({'AAA': ms(10)}))
        # a notice known exactly at the close ms(10): the open at ms(10) is not strictly after it -> exit due at ms(11),
        # which is missing: the held lot is unaccountable (never closed with hindsight)
        assert r2['status'] == RV.INCOMPLETE and r2['incomplete'][0]['reason'] == 'incomplete.delisted'
    else:
        tape, r = book(side, 14, holes=tuple(range(10, 14)))
        (row,) = r['incomplete']
        assert (row['reason'], row['entry_ms']) == ('incomplete.gap', None)


# ------------------------------------------------------------------ F6 partial / risk (6 + 6)
F6 = ['odd_step', 'below_min_whole', 'r_over_d', 'open_risk_le_d', 'overlays_use_x1_qty_stop',
      'mean_beyond_entry_runner_exits_at_partial_open']
X1KW = dict(vwap=LE, close={12: 104.0})


@rex('F6', F6, sides=True)
def test_f6_partial_risk(side, case):
    if case == 'odd_step':
        tape, r = book(side, 16, rule=dict(S.RULE, step=0.002), **X1KW)
        p = ov(tape, side, r, 'x2', 16)
        assert r['trades'][0]['qty'] == 33.222 and [s['qty'] for s in p['slices']] == [16.61, 16.612]
    elif case == 'below_min_whole':
        tape, r = book(side, 16, rule=dict(S.RULE, min_qty=20.0), **X1KW)
        p = ov(tape, side, r, 'x2', 16)
        assert [(s['reason'], s['exit_ms'], s['qty']) for s in p['slices']] == [('exit.mean_whole', ms(13), 33.222)]
        assert p['counts'] == {'x2_whole_below_min': 1}
    elif case == 'r_over_d':
        tape, r = book(side, 16, **X1KW)
        (t,) = r['trades']
        assert t['D'] == t['qty'] * abs(t['fill'] - t['stop']) and t['r'] == t['net'] / t['D']
        p = ov(tape, side, r, 'x3', 16)
        assert p['r'] == p['net'] / t['D']
    elif case == 'open_risk_le_d':
        tape, r = book(side, 20, vwap=LE, close={7: 101.0, 8: 102.0, 9: 102.5, 10: 102.8, 11: 104.0, 12: 104.0,
                                                 13: 104.0, 14: 96.0})
        (t,) = r['trades']
        sg = 1 if side == 'long' else -1
        for arm in ('x2', 'x3'):
            p = ov(tape, side, r, arm, 20)
            stops = [st for _, st, _ in p['path']]
            assert all(sg * (b - a) >= 0 for a, b in zip(stops, stops[1:]))           # monotone, never loosened
            assert all(q * sg * (t['fill'] - st) <= t['D'] + 1e-9 for _, st, q in p['path'])
    elif case == 'overlays_use_x1_qty_stop':
        tape, r = book(side, 16, **X1KW)
        (t,) = r['trades']
        for arm in ('x2', 'x3'):
            p = ov(tape, side, r, arm, 16)
            assert (p['qty'], p['D'], p['stop0'], p['entry_ms']) == (t['qty'], t['D'], t['stop'], t['entry_ms'])
            assert p['path'][0][1] == t['stop']
    else:
        tape, r = book(side, 12, vwap=LE, close={7: 103.5}, ohlc={8: (100.0, 101, 99.5, 100.5)})
        p = ov(tape, side, r, 'x2', 12)
        assert [(s['reason'], s['exit_ms'], s['gap']) for s in p['slices']] == \
               [('exit.partial', ms(8), False), ('exit.breakeven', ms(8), True)]


# ------------------------------------------------------------------ F7 range gate (4 + 4)
F7 = ['drift_eq_1atr_passes', 'drift_gt_1atr_blocks', 'invalidation_all_arms', 'zero_volume']


@rex('F7', F7, sides=True)
def test_f7_range_gate(side, case):
    if case in ('drift_eq_1atr_passes', 'drift_gt_1atr_blocks'):
        lag = 101.0 if case == 'drift_eq_1atr_passes' else 100.99
        tape, r = book(side, 12, vwap={-1: lag, 4: 105.0, 5: 103.0})
        ind = RV.indicators(RV.PRIMARY, RV.read(tape['AAA'], ms(6), RV.PRIMARY.lookback))
        assert ind.rg is (case == 'drift_eq_1atr_passes') and ind.atr == 2
        assert [x[1] for x in r['signals']] == ([ms(6)] if ind.rg else [])
    elif case == 'invalidation_all_arms':
        tape, r = book(side, 12, vwap={**LE, 8: 106.0})
        (t,) = r['trades']
        assert (t['reason'], t['exit_ms']) == ('exit.invalidation', ms(9))
        for arm in ('x2', 'x3'):
            p = ov(tape, side, r, arm, 12)
            assert [(s['reason'], s['exit_ms']) for s in p['slices']] == [('exit.invalidation', ms(9))]
    else:
        tape, r = book(side, 12, vwap=LE, vol={i: (0.0, 0.0) for i in range(-60, 12)})
        ind = RV.indicators(RV.PRIMARY, RV.read(tape['AAA'], ms(6), RV.PRIMARY.lookback))
        assert ind.vwap is None and ind.z is None and ind.rg is None
        assert r['signals'] == [] and r['status'] == RV.COMPLETE


# ------------------------------------------------------------------ F8 causal prefix (5 + 5)
F8 = ['mutate_before_i_minus_47', 'mutate_i_minus_47', 'append_future', 'x1_identical_with_without_overlays',
      'n_plus_12_mutate_before_i_minus_59']


def _mutate(x, idx):
    for i in idx:
        b = x.bars[RV.TF * i + S.H]
        x.bars[b.open_ms] = b._replace(open=b.open + 7, high=b.high + 9, low=b.low + 5, close=b.close + 7,
                                       quote_volume=b.quote_volume * 3, volume=b.volume * 2)


@rex('F8', F8, sides=True)
def test_f8_causal_prefix(side, case):
    cfg = RV.PRIMARY
    if case in ('mutate_before_i_minus_47', 'mutate_i_minus_47'):
        a = S.build(n=12, side=side, vwap=LE)
        b = copy.deepcopy(a)
        _mutate(b, range(-60, -42) if case == 'mutate_before_i_minus_47' else (-42,))   # i = 5: i - 47 = -42
        ia, ib = (RV.indicators(cfg, RV.read(x, ms(6), cfg.lookback)) for x in (a, b))
        if case == 'mutate_before_i_minus_47':
            assert ia == ib and RV.entry_x1(cfg, ib, side)
        else:
            assert ia.vwap_lag != ib.vwap_lag and ia.vwap == ib.vwap
    elif case == 'append_future':
        ra = S.run(one(n=12, side=side, vwap=LE), side, 12)
        rb = S.run(one(n=30, side=side, vwap=LE, close={20: 120.0}), side, 12)
        assert ra == rb and ra['signals'] == [('AAA', ms(6), -1.5 if side == 'long' else 1.5)]
    elif case == 'x1_identical_with_without_overlays':
        tape = one(n=16, side=side, **X1KW)
        r0 = S.run(tape, side, 16)
        frozen = copy.deepcopy(r0)
        for arm in ('x2', 'x3'):
            for t in r0['trades']:
                RV.overlay(tape, cfg, side, t, arm, end=ms(16))
        assert r0 == frozen and S.run(tape, side, 16) == frozen
    else:
        nb = next(c for c in RV.neighbours() if c.n == RV.N + 12)
        assert nb.lookback == RV.W_BARS == 60
        a = S.build(n=12, side=side, pre=70, vwap=LE)
        b = copy.deepcopy(a)
        _mutate(b, range(-70, -54))                  # decision i = 5 reads i - 59 = -54 .. 5
        assert RV.indicators(nb, RV.read(a, ms(6), 60)) == RV.indicators(nb, RV.read(b, ms(6), 60))
        c = copy.deepcopy(a)
        _mutate(c, (-54,))
        assert RV.indicators(nb, RV.read(a, ms(6), 60)) != RV.indicators(nb, RV.read(c, ms(6), 60))
        exact = S.build(n=12, side=side, pre=60, vwap=LE)     # the first decision at H reads exactly [H - W, H)
        assert [x.open_ms for x in RV.read(exact, S.H, 60)] == list(range(S.H - RV.WARMUP_MS, S.H, S.TF))
        bad = S.build(n=12, side=side, pre=60, vwap=LE, holes=(-59,))
        assert S.run({'AAA': bad}, side, 12, cfg=nb)['censor']['first_unaccountable_ms'] == ms(-59)


# ------------------------------------------------------------------ F10 book / sizing (6 + 6)
F10 = ['fifth_lot', 'gross_3x', 'cairo_day_halt', 'min_notional_at_decision', 'd_below_1pct_w', 'fill_time_refusal']


@rex('F10', F10, sides=True)
def test_f10_book_sizing(side, case):
    if case == 'fifth_lot':
        tape = {s: S.build(n=12, side=side, vwap=LE) for s in 'ABCDE'}
        r = S.run(tape, side, 12)
        assert sorted(r['open_lots']) == ['A', 'B', 'C', 'D'] and r['refusals'] == {'book.max_positions': 1}
    elif case == 'gross_3x':
        tape, r = book(side, 12, hl=0.1, vwap=S.long_entry(5, up=0.5, back=0.3))
        lot = r['open_lots']['AAA']
        assert lot['atr'] == pytest.approx(0.2) and lot['qty'] == 272.727       # floor(3 x 10,000 / (100 x 1.10))
        assert 0.01 * 10_000 / (1.5 * 0.2 + 0.01) > lot['qty']                   # the risk size was larger
    elif case == 'cairo_day_halt':
        a = S.build(n=14, side=side, vwap=LE, ohlc={8: (80, 81, 79, 80)}, close={i: 80.0 for i in range(9, 14)})
        b = S.build(n=14, side=side, vwap=S.long_entry(9))
        r = S.run({'A': a, 'B': b}, side, 14)
        assert r['trades'][0]['symbol'] == 'A' and r['trades'][0]['gap']
        assert [x[:2] for x in r['signals']] == [('A', ms(6)), ('B', ms(10))]
        assert RV.E.cairo_day(ms(6)) == RV.E.cairo_day(ms(10))
        assert r['refusals'] == {'book.day_halt': 1} and 'B' not in r['open_lots']
    elif case == 'min_notional_at_decision':
        tape, r = book(side, 12, vwap=LE, rule=dict(S.RULE, min_notional=1e6))
        assert r['refusals'] == {'execution.size_min': 1} and not r['open_lots']
    elif case == 'd_below_1pct_w':
        tape, r = book(side, 16, **X1KW)
        (t,) = r['trades']
        assert t['D'] < 0.01 * RV.BOOK['equity0'] and t['D'] == t['qty'] * abs(t['fill'] - t['stop'])
    else:                                            # min notional passes on close_i, fails on the executable fill
        tape, r = book(side, 12, vwap=LE, rule=dict(S.RULE, min_notional=3300.0),
                       abs_ohlc={6: (99.0, 100.5, 98.5, 100.0)})
        assert r['refusals'] == {'execution.size_min_fill': 1} and not r['open_lots']
