"""C2 vol_compression_breakout.v0 synthetic fixtures (draft 6229540622, Cowork list B 6229554095, Codex FREEZE 6229577171).
Synthetic in-memory bars only. Hand-built planted-compression fixtures inject ATR14 (20 everywhere, ATR[b-1] = 7.5 for
the filters, ATR[b] = 30 for the stop buffer, unless a test overrides it); the planted-box generator and the no-lookahead
tests use the computed strategies ATR."""
import os
import sys
from fractions import Fraction

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import vol_compression_breakout as VC                                                       # noqa: E402

B = 130
DEF = (100.0, 101.0, 99.0, 100.0)          # o, h, l, c  -> box Hb 101, Lb 99, mid 100
BRK = (100.0, 106.0, 99.0, 105.0)          # C 105 > 101.75, body 5 >= 3.75, close location 6/7
AFT = (105.0, 106.0, 104.0, 105.0)         # above Hb, inside stop 97 / target 129
CAND = (100.0, 108.0, 99.0, 107.5)         # a later breakout above a box topped at <= 106


def frame(ov=None, N=160, b=B, brk=BRK, aft=AFT, base_atr=20.0, a_prev=7.5, a_b=30.0, atr_ov=None, rows=None):
    rows = list(rows) if rows is not None else [DEF] * b + [brk] + [aft] * (N - b - 1)
    for i, r in (ov or {}).items():
        rows[i] = r
    d = pd.DataFrame(np.array(rows, dtype=float), columns=['o', 'h', 'l', 'c'])
    d['v'] = 1.0
    A = np.full(len(rows), base_atr)
    A[b - 1], A[b] = a_prev, a_b
    for i, v in (atr_ov or {}).items():
        A[i] = v
    d['atr'] = A
    return d


def run(df):
    return VC.run_symbol(df)


def sigs(df):
    return [(g['side'], g['b']) for g in run(df)['signals']]


def one(df):
    r = run(df)
    assert len(r['trades']) == 1, r
    return r['trades'][0]


def flip(df):
    f = df.copy()
    f['o'], f['h'], f['l'], f['c'] = 200 - df['o'], 200 - df['l'], 200 - df['h'], 200 - df['c']
    return f


def cand_at(atr_ov, p):
    atr_ov[p - 1], atr_ov[p] = 7.5, 30.0
    return atr_ov


# ---- base: exact prices, ATR index pinning, time cap
def test_base_signal_exact_prices_and_time_cap():
    r = run(frame())
    g, = r['signals']
    assert (g['side'], g['b'], g['box_hi'], g['box_lo'], g['stop']) == ('long', 130, 101.0, 99.0, 97.0)
    assert (g['atr_prev'], g['atr_b']) == (7.5, 30.0)              # filters ATR[b-1]; stop buffer ATR[b] = 3
    t, = r['trades']
    assert r['skips'] == [] and r['blocked'] == []
    assert (t['entry_i'], t['entry'], t['stop'], t['target'], t['R_unit']) == (131, 105.0, 97.0, 129.0, 8.0)
    assert (t['label'], t['exit_i'], t['exit'], t['gap_exit']) == ('time', 149, 105.0, False)   # close b+18, open b+19
    assert t['R_gross'] == 0.0 and t['hold_bars'] == 18


def test_entry_is_open_b_plus_1_not_close_b():
    t = one(frame({131: (106.0, 107.0, 105.0, 106.0)}))
    assert (t['entry_i'], t['entry'], t['R_unit'], t['target']) == (131, 106.0, 9.0, 133.0)


def test_cost_constants_are_the_runner_ones():
    import backtest as BT
    assert (VC.FEE, VC.SLIP, VC.FUND_PER_BAR) == (BT.FEE, BT.SLIP, BT.FUND_PER_BAR) == (0.0005, 0.0002, 0.00005)
    assert VC.FUND_PER_BAR == pytest.approx(3.125e-6 * 16)


def test_filters_use_atr_b_minus_1_not_atr_b():
    assert sigs(frame(a_prev=30.0, a_b=7.5)) == []                  # ratio 30/20 at b-1 fails


def test_stop_uses_atr_b_not_b_minus_1():
    g, = run(frame(a_b=10.0))['signals']
    assert g['stop'] == 99.0                                        # mid 100 - 0.10 x ATR[b] 10 (ATR[b-1] would give 99.25)


# ---- compression ratio / median / width / history
@pytest.mark.parametrize('base,ok', [(20.0, True), (10.0, True), (9.99, False)])
def test_compression_ratio_boundary(base, ok):                     # 7.5 / 10 == 0.75 passes (<=)
    assert sigs(frame(base_atr=base)) == ([('long', 130)] if ok else [])


@pytest.mark.parametrize('lo,hi,ok', [(9.0, 11.0, True), (9.0, 10.98, False)])
def test_even_count_median_is_mean_of_middle_two(lo, hi, ok):
    ov = {i: lo for i in range(10, 69)}                             # 59 lo + ATR[129] 7.5 = 60 low values
    ov.update({i: hi for i in range(69, 129)})                      # 60 high values
    assert sigs(frame(atr_ov=ov)) == ([('long', 130)] if ok else [])


def test_median_fr_unit():
    assert VC.median_fr([3.0, 1.0, 2.0, 4.0]) == Fraction(5, 2)
    assert VC.median_fr([3.0, 1.0, 2.0]) == 2


@pytest.mark.parametrize('low,ok', [(78.5, True), (78.49, False)])
def test_box_width_boundary(low, ok):                              # Hb - Lb == 3.0 x 7.5 passes (<=)
    assert sigs(frame({125: (100.0, 101.0, low, 100.0)})) == ([('long', 130)] if ok else [])


@pytest.mark.parametrize('b,ok', [(120, True), (119, False)])
def test_needs_120_prior_atr_values(b, ok):                        # earliest eligible b = 120 (ATR index 0 defined)
    assert sigs(frame(N=150, b=b)) == ([('long', b)] if ok else [])


@pytest.mark.parametrize('i,v,ok', [(10, np.nan, False), (10, 0.0, False), (10, -1.0, False), (9, np.nan, True),
                                    (130, np.nan, False)])
def test_atr_window_must_be_finite_and_positive(i, v, ok):        # window b-120..b-1 = 10..129; ATR[b] finite
    assert sigs(frame(atr_ov={i: v})) == ([('long', 130)] if ok else [])


def test_box_is_b_minus_12_to_b_minus_1():
    assert sigs(frame({117: (100.0, 130.0, 99.0, 100.0)})) == [('long', 130)]   # b-13 outside the box
    assert sigs(frame({118: (100.0, 130.0, 99.0, 100.0)})) == []                 # b-12 inside: Hb 130
    # a deep low on bar b itself must not widen the box (close location then fails, so check the box via detect)
    d = frame(brk=(98.0, 106.0, 96.0, 105.0))                      # L[b] 96 < Lb 99; CL 9/10
    g, = VC.detect(d, 'long')
    assert (g['box_hi'], g['box_lo'], g['stop']) == (101.0, 99.0, 97.0)


# ---- breakout bar thresholds
@pytest.mark.parametrize('c,ok', [(101.75, False), (101.76, True)])
def test_breakout_buffer_strict(c, ok):                            # C > Hb + 0.10 x 7.5
    assert sigs(frame(brk=(97.0, 102.0, 97.0, c))) == ([('long', 130)] if ok else [])


@pytest.mark.parametrize('o,ok', [(101.25, True), (101.26, False)])
def test_body_threshold_equality(o, ok):                           # C - O >= 0.5 x 7.5
    assert sigs(frame(brk=(o, 106.0, 99.0, 105.0))) == ([('long', 130)] if ok else [])


@pytest.mark.parametrize('c,ok', [(103.0, True), (102.99, False)])
def test_close_location_equality(c, ok):                           # (C - L) / (H - L) >= 0.70
    assert sigs(frame(brk=(99.0, 106.0, 96.0, c))) == ([('long', 130)] if ok else [])


def test_flat_bar_gives_no_signal_either_side():
    for brk in ((105.0, 105.0, 105.0, 105.0), (95.0, 95.0, 95.0, 95.0)):
        df = frame(brk=brk)
        assert VC.detect(df, 'long') == [] and VC.detect(df, 'short') == []


# ---- entry, gap and cost guard
@pytest.mark.parametrize('o,why', [(97.0, 'gap_stop'), (96.0, 'gap_stop'), (97.5, 'cost_guard')])
def test_gap_at_entry(o, why):
    r = run(frame({131: (o, o + 1, o - 0.5, o)}))
    assert r['trades'] == [] and [s['skip'] for s in r['skips']] == [why]


def _guard_frame(E):
    rows = [(2480.0, 2490.0, 2470.0, 2480.0)] * 130 + [(2488.0, 2500.0, 2487.0, 2498.0)] + [(E, E + 1, E - 1, E)] * 29
    return frame(rows=rows, a_b=10.0)                              # stop = 2480 - 1 = 2479 = 0.9916 x 2500


@pytest.mark.parametrize('E,ok', [(2500.0, True), (2499.99, False)])
def test_cost_guard_equality_and_entry_denominator(E, ok):        # R / E >= 0.84%; R / C[b] would pass 2499.99
    r = run(_guard_frame(E))
    if ok:
        t, = r['trades']
        assert (t['entry'], t['stop'], t['R_unit'], t['target']) == (2500.0, 2479.0, 21.0, 2563.0)
    else:
        assert r['trades'] == [] and [s['skip'] for s in r['skips']] == ['cost_guard']


def test_gap_up_target_rebases_from_actual_entry():
    t = one(frame({131: (110.0, 111.0, 109.0, 110.0)}))
    assert (t['entry'], t['R_unit'], t['target']) == (110.0, 13.0, 149.0)


def test_no_next_bar_skip_still_signals():
    r = run(frame(N=131))
    assert sigs(frame(N=131)) == [('long', 130)] and [s['skip'] for s in r['skips']] == ['no_next_bar']


# ---- exits and precedence
def test_stop_beats_target_same_bar():
    t = one(frame({135: (105.0, 130.0, 96.0, 105.0)}))
    assert (t['label'], t['exit_i'], t['exit'], t['R_gross']) == ('stop', 135, 97.0, -1.0)


def test_target_hit_intrabar():
    t = one(frame({135: (105.0, 129.0, 104.0, 128.0)}))
    assert (t['label'], t['exit_i'], t['exit'], t['R_gross']) == ('target', 135, 129.0, 3.0)


def test_entry_bar_intrabar_stop():
    t = one(frame({131: (105.0, 106.0, 96.0, 105.0)}))
    assert (t['label'], t['exit_i'], t['exit']) == ('stop', 131, 97.0)


def test_gap_through_stop_fills_at_open_flagged_below_minus_1():
    t = one(frame({135: (95.0, 96.0, 94.0, 95.0)}))
    assert (t['label'], t['exit'], t['gap_exit'], t['R_gross'], t['lt_minus1']) == ('stop', 95.0, True, -1.25, True)


def test_gap_through_target_fills_at_open():
    t = one(frame({135: (130.0, 131.0, 129.5, 130.0)}))
    assert (t['label'], t['exit'], t['gap_exit'], t['R_gross']) == ('target', 130.0, True, 25 / 8)


def test_stop_checked_before_close_invalidation_same_bar():
    t = one(frame({135: (105.0, 106.0, 96.0, 100.0)}))             # L <= stop and C < Hb on one bar
    assert (t['label'], t['exit_i'], t['exit']) == ('stop', 135, 97.0)


def test_invalidation_first_at_close_b_plus_1_fills_open_b_plus_2():
    t = one(frame({131: (105.0, 106.0, 100.0, 100.5), 132: (102.0, 103.0, 101.5, 102.0)}))
    assert (t['label'], t['exit_i'], t['exit'], t['R_gross'], t['gap_exit']) == ('invalidation', 132, 102.0, -0.375,
                                                                                  False)


def test_close_at_box_high_is_not_invalidation():
    t = one(frame({131: (105.0, 106.0, 101.0, 101.0)}))
    assert (t['label'], t['exit_i']) == ('time', 149)


@pytest.mark.parametrize('nxt,label,X', [((96.0, 97.0, 95.0, 96.0), 'stop', 96.0),
                                         ((131.0, 132.0, 130.0, 131.0), 'target', 131.0),
                                         ((102.0, 103.0, 101.5, 102.0), 'invalidation', 102.0)])
def test_gap_at_open_owns_label_over_pending_close_exit(nxt, label, X):
    t = one(frame({135: (105.0, 106.0, 100.0, 100.5), 136: nxt}))
    assert (t['label'], t['exit_i'], t['exit'], t['gap_exit']) == (label, 136, X, label != 'invalidation')


def test_invalidation_gap_below_stop_flagged():
    t = one(frame({135: (105.0, 106.0, 100.0, 100.5), 136: (90.0, 91.0, 89.0, 90.0)}))
    assert (t['label'], t['exit'], t['lt_minus1']) == ('stop', 90.0, True) and t['R_gross'] == -15 / 8


@pytest.mark.parametrize('c,label', [(100.5, 'invalidation'), (101.0, 'time')])
def test_invalidation_label_beats_time_cap(c, label):
    t = one(frame({148: (105.0, 106.0, 100.0, c)}))
    assert (t['label'], t['exit_i']) == (label, 149)


def test_time_cap_pending_at_b_plus_18_but_gap_stop_at_b_plus_19():
    t = one(frame({149: (95.0, 96.0, 94.0, 95.0)}))
    assert (t['label'], t['exit_i'], t['exit']) == ('stop', 149, 95.0)


def test_open_at_end():
    r = run(frame(N=145))
    t, = r['trades']
    assert t['label'] == 'open_end' and t['R_net'] is None and VC.summarize(r['trades'])['open_end'] == 1


# ---- box consumption
@pytest.mark.parametrize('first', ['cost', 'gap'])
@pytest.mark.parametrize('p,ok', [(142, False), (143, True)])
def test_skipped_signal_consumes_box_b12_blocked_b13_allowed(first, p, ok):
    row1 = (97.5, 98.0, 97.0, 97.5) if first == 'cost' else (96.0, 97.0, 95.0, 96.0)
    r = run(frame({131: row1, p: CAND}, aft=DEF, atr_ov=cand_at({}, p)))
    assert [s['skip'] for s in r['skips'] if s['b'] == 130] == ['cost_guard' if first == 'cost' else 'gap_stop']
    assert [g['b'] for g in r['signals']] == ([130, p] if ok else [130])
    assert [x['b'] for x in r['blocked']] == ([] if ok else [p])
    assert [t['b'] for t in r['trades']] == ([p] if ok else [])


def test_blocked_candidate_does_not_consume():
    ov = cand_at(cand_at({}, 142), 143)
    ov[142] = 7.5                                                   # ATR[142] is ATR[b-1] for 143 (and 142's buffer)
    r = run(frame({131: (97.5, 98.0, 97.0, 97.5), 142: CAND, 143: (108.0, 116.0, 107.0, 115.5)}, aft=DEF, atr_ov=ov))
    assert [g['b'] for g in r['signals']] == [130, 143] and [x['b'] for x in r['blocked']] == [142]


def test_failed_filter_does_not_consume():
    df = frame(brk=(101.26, 106.0, 99.0, 105.0), ov={135: CAND}, atr_ov=cand_at({}, 135))
    assert sigs(df) == [('long', 135)]


# ---- slot (one trade per symbol)
def _slot_frame(p, q=None):
    ov = {p: (106.0, 112.0, 105.0, 111.0)}
    for k in range(p + 1, 200):
        ov[k] = (111.0, 112.0, 110.0, 111.0)
    A = cand_at({}, p)
    if q is not None:
        ov[q] = (112.0, 118.0, 111.0, 117.0)
        cand_at(A, q)
    return frame(ov, N=200, atr_ov=A)


def test_signal_at_close_before_next_open_exit_is_ignored_and_box_consumed():
    r = run(_slot_frame(148))                                       # trade 1 time-exits at open 149
    assert [g['b'] for g in r['signals']] == [130, 148]
    assert [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 148)]
    t, = r['trades']
    assert (t['label'], t['exit_i']) == ('time', 149)


@pytest.mark.parametrize('q,ok', [(160, False), (161, True)])
def test_overlap_ignored_signal_consumes_box(q, ok):
    r = run(_slot_frame(148, q))
    assert [t['b'] for t in r['trades']] == ([130, q] if ok else [130])
    assert [x['b'] for x in r['blocked']] == ([] if ok else [q])


def test_signal_on_exit_bar_is_taken():
    r = run(_slot_frame(149))                                       # trade 1 exits at open 149; signal at close 149
    assert [(t['b'], t['entry_i']) for t in r['trades']] == [(130, 131), (149, 150)]
    assert r['trades'][0]['exit_i'] == 149 and r['skips'] == []


def test_signal_while_open_is_ignored():
    r = run(_slot_frame(145))
    assert [t['b'] for t in r['trades']] == [130] and [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 145)]


def test_same_bar_long_short_conflict_is_asserted(monkeypatch):    # injection only: unreachable on valid bars
    real = VC.detect
    monkeypatch.setattr(VC, 'detect', lambda df, side, atr=None: [dict(g, side=side) for g in real(df, 'long', atr)])
    with pytest.raises(AssertionError):
        VC.run_symbol(frame())


# ---- mirror
EXTRAS = [{}, {131: (106.0, 107.0, 105.0, 106.0)}, {135: (105.0, 130.0, 96.0, 105.0)},
          {135: (105.0, 129.0, 104.0, 128.0)}, {135: (95.0, 96.0, 94.0, 95.0)},
          {131: (105.0, 106.0, 100.0, 100.5), 132: (102.0, 103.0, 101.5, 102.0)},
          {135: (105.0, 106.0, 100.0, 100.5), 136: (96.0, 97.0, 95.0, 96.0)}, {148: (105.0, 106.0, 100.0, 100.5)}]


@pytest.mark.parametrize('extra', EXTRAS)
def test_long_short_mirror(extra):
    df = frame(extra)
    lt, st = one(df), one(flip(df))
    assert (lt['side'], st['side']) == ('long', 'short')
    for k in ('b', 'entry_i', 'exit_i', 'label', 'gap_exit', 'R_unit', 'R_gross', 'hold_bars', 'lt_minus1'):
        assert lt[k] == st[k], k
    for k in ('entry', 'stop', 'target', 'exit'):
        assert st[k] == 200 - lt[k], k


def test_mirror_cost_guard_uses_real_entry():                     # R / E differs under K - p; each side on its own E
    rows = [(1000.0, 1001.0, 999.0, 1000.0)] * 130 + [(1000.0, 1006.0, 999.0, 1005.0)] + [(1005.0, 1006.0, 1004.0,
                                                                                           1005.0)] * 29
    df = frame(rows=rows)                                           # long R 8 / 1005 < 0.84%
    f = df.copy()
    f['o'], f['h'], f['l'], f['c'] = 1010 - df['o'], 1010 - df['l'], 1010 - df['h'], 1010 - df['c']
    assert [s['skip'] for s in run(df)['skips']] == ['cost_guard']   # 8 / 1005 = 0.796%
    assert [s['skip'] for s in run(f)['skips']] == []                # short R 8 / 5 -> taken
    assert run(f)['trades'][0]['side'] == 'short'


# ---- exact cost arithmetic, both sides
@pytest.mark.parametrize('flipped', [False, True])
def test_exact_net_R_costs_known_trade(flipped):
    df = frame({135: (105.0, 106.0, 96.0, 105.0)})                  # long 105 -> stop 97, bars 131..135 close 105
    if flipped:
        df = flip(df)                                               # short 95 -> stop 103, closes 95
    t = one(df)
    if not flipped:
        E, X, ef, xf, closes = 105.0, 97.0, 105.0 * 1.0002, 97.0 * 0.9998, 5 * 105.0
    else:
        E, X, ef, xf, closes = 95.0, 103.0, 95.0 * 0.9998, 103.0 * 1.0002, 5 * 95.0
    assert (t['entry'], t['exit'], t['R_unit'], t['R_gross']) == (E, X, 8.0, -1.0)
    assert t['entry_fill'] == pytest.approx(ef, abs=1e-12) and t['exit_fill'] == pytest.approx(xf, abs=1e-12)
    assert t['slip_R'] == pytest.approx((E + X) * 0.0002 / 8, abs=1e-15)
    assert t['fee_R'] == pytest.approx((ef + xf) * 0.0005 / 8, abs=1e-15)
    assert t['fund_R'] == pytest.approx(0.00005 * closes / 8, abs=1e-15)
    net = -1.0 - ((E + X) * 0.0002 + (ef + xf) * 0.0005 + 0.00005 * closes) / 8
    assert t['R_net'] == pytest.approx(net, abs=1e-12)


# ---- planted-box generator (computed ATR): float oracle differential, no lookahead
def planted(seed, N=700, start=200.0):
    rng = np.random.default_rng(seed)
    plants = {p: rng.choice(['long', 'short']) for p in range(170, N - 40, 110)}
    rows, c = [], start
    comp = {q for p in plants for q in range(p - 12, p)}
    for i in range(N):
        o = c
        if i in plants:
            m = 3.0 if plants[i] == 'long' else -3.0
            c = o + m
            h, l = (c + 0.05, o - 0.05) if m > 0 else (o + 0.05, c - 0.05)
        elif i in comp:
            c = o + rng.normal(0, 0.05)
            h, l = max(o, c) + 0.03, min(o, c) - 0.03
        else:
            c = o + rng.normal(0, 1.0)
            h, l = max(o, c) + abs(rng.normal(0, 0.5)), min(o, c) - abs(rng.normal(0, 0.5))
        rows.append((o, h, l, c))
    d = pd.DataFrame(rows, columns=['o', 'h', 'l', 'c'])
    d['v'] = 1.0
    return d, plants


def oracle(df):
    """Independent float re-statement of the frozen detector (both sides, no consumption)."""
    A = VC.atr14(df)
    o, h, l, c = (df[k].to_numpy() for k in 'ohlc')
    out = set()
    for b in range(120, len(df)):
        w = A[b - 120:b]
        if not (np.all(np.isfinite(w)) and np.all(w > 0)):
            continue
        a, hb, lb = A[b - 1], h[b - 12:b].max(), l[b - 12:b].min()
        if a / np.median(w) > 0.75 or hb - lb > 3.0 * a or h[b] == l[b]:
            continue
        if c[b] > hb + 0.1 * a and c[b] - o[b] >= 0.5 * a and (c[b] - l[b]) / (h[b] - l[b]) >= 0.7:
            out.add(('long', b))
        if c[b] < lb - 0.1 * a and o[b] - c[b] >= 0.5 * a and (h[b] - c[b]) / (h[b] - l[b]) >= 0.7:
            out.add(('short', b))
    return out


SEEDS = range(12)


@pytest.mark.parametrize('seed', SEEDS)
def test_planted_generator_matches_float_oracle(seed):
    df, plants = planted(seed)
    got = {(g['side'], g['b']) for sd in VC.SIDES for g in VC.detect(df, sd)}
    assert got == oracle(df)
    assert {(s, p) for p, s in plants.items()} <= got                 # every planted box fires on its side


def test_planted_generator_smoke_run_symbol():
    n_tr = 0
    for seed in SEEDS:
        df, _ = planted(seed)
        r = VC.run_symbol(df)
        bs = [g['b'] for g in r['signals']]
        assert all(b2 - b1 >= 13 for b1, b2 in zip(bs, bs[1:]))
        prev_exit = -1
        for t in r['trades']:
            assert t['entry_i'] == t['b'] + 1 and t['b'] >= prev_exit
            assert t['R_unit'] >= 0.0084 * t['entry'] - 1e-9
            if t['exit_i'] is not None:
                assert t['entry_i'] <= t['exit_i'] <= t['b'] + 19
                assert t['R_net'] < t['R_gross']
                prev_exit = t['exit_i']
        n_tr += len(r['trades'])
        s = VC.summarize(r['trades'])
        assert s['total']['n'] == s['long']['n'] + s['short']['n']
    assert n_tr >= 24


@pytest.mark.parametrize('seed', [0, 3])
def test_no_lookahead_truncate_and_mutate(seed):
    df, _ = planted(seed)
    full = VC.run_symbol(df)
    rng = np.random.default_rng(99)
    for g in full['signals']:
        b = g['b']
        key = (g['side'], b, g['stop'], g['box_hi'], g['box_lo'])
        cut = VC.run_symbol(df.iloc[:b + 1].copy())
        assert [(x['side'], x['b'], x['stop'], x['box_hi'], x['box_lo']) for x in cut['signals']][-1] == key
        m = df.copy()
        k = np.arange(b + 1, len(m))
        oo = m['o'].to_numpy()[k] + rng.normal(0, 5, len(k))
        cc = oo + rng.normal(0, 5, len(k))
        m.loc[k, 'o'], m.loc[k, 'c'] = oo, cc
        m.loc[k, 'h'], m.loc[k, 'l'] = np.maximum(oo, cc) + 1, np.minimum(oo, cc) - 1
        before = [(x['side'], x['b'], x['stop']) for x in VC.run_symbol(m)['signals'] if x['b'] <= b]
        assert before == [(x['side'], x['b'], x['stop']) for x in full['signals'] if x['b'] <= b]


def test_changing_open_b_plus_1_only_changes_entry_and_R():
    df, plants = planted(1)
    base = VC.run_symbol(df)
    t0 = base['trades'][0]
    b = t0['b']
    m = df.copy()
    d = 0.4 if t0['side'] == 'long' else -0.4
    m.loc[b + 1, 'o'] += d
    m.loc[b + 1, 'h'] = max(m.loc[b + 1, 'h'], m.loc[b + 1, 'o'])
    m.loc[b + 1, 'l'] = min(m.loc[b + 1, 'l'], m.loc[b + 1, 'o'])
    t1 = [t for t in VC.run_symbol(m)['trades'] if t['b'] == b][0]
    assert (t1['side'], t1['stop'], t1['entry_i']) == (t0['side'], t0['stop'], t0['entry_i'])
    assert t1['entry'] == pytest.approx(t0['entry'] + d) and t1['R_unit'] == pytest.approx(t0['R_unit'] + abs(d))
    assert t1['target'] == pytest.approx(t1['entry'] + (3 if d > 0 else -3) * t1['R_unit'])


# ---- summary
def test_summary_counts():
    trs = [one(frame({135: (95.0, 96.0, 94.0, 95.0)})), one(flip(frame({135: (105.0, 129.0, 104.0, 128.0)}))),
           one(frame({131: (105.0, 106.0, 100.0, 100.5), 132: (90.0, 91.0, 89.0, 90.0)}))]
    s = VC.summarize(trs)
    assert (s['total']['n'], s['long']['n'], s['short']['n']) == (3, 2, 1)
    assert s['total']['net_R'] == pytest.approx(sum(t['R_net'] for t in trs))
    assert s['total']['gross_R'] == pytest.approx(-1.25 + 3.0 - 15 / 8)
    assert s['total']['lt_minus1'] == 2 and s['total']['lt_minus1_by_label']['stop'] == 2
    w = trs[1]['R_net']
    assert s['total']['pf'] == pytest.approx(w / -(trs[0]['R_net'] + trs[2]['R_net']))
    assert s['total']['net_R'] == pytest.approx(s['total']['gross_R'] - s['total']['fee_R'] - s['total']['slip_R']
                                                - s['total']['fund_R'])
