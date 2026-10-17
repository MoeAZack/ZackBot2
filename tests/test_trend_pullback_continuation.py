"""C3 trend_pullback_continuation.v0 synthetic fixtures (draft 6230936151, Cowork plan 6230959827, Codex FREEZE
6230952961). Synthetic in-memory bars only; no cache, store or research data is read.

Hand-built fixtures inject the indicators so every gate boundary is an exact integer / binary-fraction comparison:
EMA50[i] = 900 + i (slope 6 over b-7..b-1), EMA20 = EMA50 + 20, ATR14 = 10 everywhere except ATR14[b] = 20 (so the
ATR[b-1] filters and the ATR[b] stop buffer / R cap are distinguishable). The planted pattern at b = 230:
  swing high 1180 at 225; pullback 226..229 (lowest low 1146 at 228 touches EMA20[228] + 2.5 = 1150.5);
  trigger 230 = (1152, 1166, 1150, 1164); entry open 231 = 1164; stop 1146 - 0.2 x 20 = 1142; R 22; target 1219.
Shorts use the affine reflection K - p with high/low (and the injected EMAs) reflected, K = 2328 so the mirrored entry
is also 1164 (equal cost-guard denominators). The random-tape oracle and causality tests use the computed indicators."""
import os
import sys
from fractions import Fraction as F

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import trend_pullback_continuation as TP                                                    # noqa: E402

B = 230
K = 2328.0
T = 0.125                                  # one exact binary tick
BG = (31, 36, 26, 31)                      # offsets from P = EMA50[b-1] = 900 + b - 1
PAT = [BG] * 7 + [(31, 51, 29, 46), (46, 47, 31, 33), (33, 34, 21, 23), (23, 24, 17, 21), (24, 27, 23, 26),
                  (23, 37, 21, 35)]        # rows b-12..b
AFT = (35, 37, 33, 35)
FOLLOW = (37, 46, 36, 44)                  # a second trigger at b+1 on the same swing


def P_of(b):
    return 900 + b - 1


def rel(t, P):
    return tuple(float(P + x) for x in t)


def frame(ov=None, N=280, b=B, pats=(), e20_ov=None, e50_ov=None, atr_ov=None, shift=0.0, a_b=20.0, follow=()):
    P = P_of(b)
    rows = [rel(BG, P)] * (b - 12) + [rel(t, P) for t in PAT] + [rel(AFT, P)] * max(N - b - 1, 0)
    for b2 in pats:
        P2 = P_of(b2)
        for j, t in enumerate(PAT):
            rows[b2 - 12 + j] = rel(t, P2)
        for k in range(b2 + 1, len(rows)):
            rows[k] = rel(AFT, P2)
    for f in follow:                                               # FOLLOW row at f, relative to the pattern at f-1
        rows[f] = rel(FOLLOW, P_of(f - 1))
    rows = rows[:N]
    for i, r in (ov or {}).items():
        rows[i] = tuple(float(x) for x in r)
    d = pd.DataFrame(np.array(rows, dtype=float), columns=['o', 'h', 'l', 'c'])
    d['v'] = 1.0
    i = np.arange(N, dtype=float)
    e50 = 900.0 + i
    e20 = e50 + 20.0
    A = np.full(N, 10.0)
    if b < N:
        A[b] = a_b
    for arr, o in ((e20, e20_ov), (e50, e50_ov), (A, atr_ov)):
        for j, v in (o or {}).items():
            arr[j] = v
    for k in 'ohlc':
        d[k] = d[k] + shift
    d['e20'], d['e50'], d['atr'] = e20 + shift, e50 + shift, A
    return d


def flip(df, k=K):
    f = df.copy()
    f['o'], f['h'], f['l'], f['c'] = k - df['o'], k - df['l'], k - df['h'], k - df['c']
    for c in ('e20', 'e50'):
        if c in df:
            f[c] = k - df[c]
    return f


def run(df):
    return TP.run_symbol(df)


def sigs(df):
    return [(g['side'], g['b']) for g in run(df)['signals']]


def one(df):
    r = run(df)
    assert len(r['trades']) == 1, r
    return r['trades'][0]


def ok_or_none(ok, b=B, side='long'):
    return [(side, b)] if ok else []


# ---------------------------------------------------------------- planted clean pullback, long and short
def test_base_long_exact_signal_and_trade():
    r = run(frame())
    g, = r['signals']
    assert (g['side'], g['b'], g['swing_i'], g['swing'], g['pb_extreme'], g['stop']) == ('long', 230, 225, 1180.0,
                                                                                         1146.0, 1142.0)
    assert (g['atr_prev'], g['atr_b']) == (10.0, 20.0)
    assert r['skips'] == [] and r['blocked'] == []
    t, = r['trades']
    assert (t['entry_i'], t['entry'], t['stop'], t['R_unit'], t['target']) == (231, 1164.0, 1142.0, 22.0, 1219.0)
    assert (t['label'], t['exit_i'], t['exit'], t['gap_exit'], t['R_gross'], t['hold_bars']) == ('time', 261, 1164.0,
                                                                                                 False, 0.0, 30)


def test_base_short_exact_signal_and_trade():
    r = run(flip(frame()))
    g, = r['signals']
    assert (g['side'], g['b'], g['swing_i'], g['swing'], g['pb_extreme'], g['stop']) == ('short', 230, 225, 1148.0,
                                                                                         1182.0, 1186.0)
    t, = r['trades']
    assert (t['entry_i'], t['entry'], t['stop'], t['R_unit'], t['target']) == (231, 1164.0, 1186.0, 22.0, 1109.0)
    assert (t['label'], t['exit_i'], t['exit'], t['R_gross']) == ('time', 261, 1164.0, 0.0)


def test_cost_constants_are_the_runner_ones():
    import backtest as BT
    assert (TP.FEE, TP.SLIP, TP.FUND_PER_BAR) == (BT.FEE, BT.SLIP, BT.FUND_PER_BAR) == (0.0005, 0.0002, 0.00005)


def test_frozen_parameters():
    assert (TP.WARM, TP.SWING, TP.SLOPE_LAG, TP.PERSIST_MIN, TP.PB_MIN, TP.CAP_BARS) == (220, 12, 6, 10, 2, 30)
    assert (TP.SLOPE_ATR, TP.DEPTH_ATR, TP.TOUCH_ATR, TP.FLOOR_ATR, TP.BODY_ATR, TP.CLOC_MIN) == (
        F(1, 2), 1, F(1, 4), F(1, 2), F(3, 10), F(3, 5))
    assert (TP.STOP_ATR, TP.RCAP_ATR, TP.COST_GUARD, TP.TARGET_R) == (F(1, 5), 3, F(1, 100), F(5, 2))


def test_indicator_convention_is_strategies_ema_and_wilder_atr():
    df, _ = tape(5)
    e20, e50, A = TP.indicators(df)
    c, h, l = (df[k].to_numpy() for k in 'chl')
    for n, got in ((20, e20), (50, e50)):
        a, y, ref = 2 / (n + 1), c[0], []
        for x in c:                                                 # seed EMA[0] = close[0], adjust=False
            y = y + a * (x - y)
            ref.append(y)
        assert np.allclose(got, ref, rtol=0, atol=1e-9)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    y, ref = tr[0], []
    for x in tr:
        y = y + (x - y) / 14
        ref.append(y)
    assert np.allclose(A, ref, rtol=0, atol=1e-9)
    import strategies as ST
    ind = ST.indicators(df)
    assert np.array_equal(e20, ind['e20'].to_numpy()) and np.array_equal(e50, ind['e50'].to_numpy())


# ---------------------------------------------------------------- warm-up and finiteness
@pytest.mark.parametrize('b,ok', [(220, True), (219, False)])
def test_warmup_earliest_b_220(b, ok):
    assert sigs(frame(N=270, b=b)) == ok_or_none(ok, b)


@pytest.mark.parametrize('kw,ok', [
    (dict(atr_ov={229: 0.0}), False), (dict(atr_ov={229: -1.0}), False), (dict(atr_ov={229: np.nan}), False),
    (dict(atr_ov={230: np.nan}), False), (dict(atr_ov={230: 0.0}), False), (dict(atr_ov={228: np.nan}), True),
    (dict(e50_ov={218: np.nan}), False), (dict(e50_ov={217: np.nan}), True), (dict(e20_ov={230: np.nan}), False),
    (dict(e20_ov={219: np.nan}), False), (dict(e20_ov={218: np.nan}), True), (dict(e50_ov={229: np.nan}), False),
    (dict(e50_ov={230: np.nan}, N=231), True)])
def test_referenced_values_finite_and_atr_positive(kw, ok):
    assert sigs(frame(**kw)) == ok_or_none(ok)


# ---------------------------------------------------------------- trend gates (at b-1)
@pytest.mark.parametrize('v,ok', [(1129.0, False), (1129.0 + T, True), (1129.0 - T, False)])
def test_ema20_above_ema50_strict(v, ok):
    assert sigs(frame(e20_ov={229: v})) == ok_or_none(ok)


@pytest.mark.parametrize('v,ok', [(1124.0, True), (1124.0 + T, False), (1124.0 - T, True)])
def test_ema50_slope_boundary_b7_to_b1(v, ok):                    # 1129 - EMA50[223] >= 0.5 x ATR[229] = 5
    assert sigs(frame(e50_ov={223: v})) == ok_or_none(ok)


def test_slope_uses_b_minus_7_not_b_minus_6():
    assert sigs(frame(e50_ov={224: 1128.0})) == ok_or_none(True)    # b-6 is not the reference
    assert sigs(frame(e50_ov={222: 1128.0})) == ok_or_none(True)


def _on_ema50(j, c):
    return (c, c + 2, c - 2, c)


@pytest.mark.parametrize('bars,third,ok', [((218, 219), None, True), ((218, 219, 220), 1120.0, False),
                                           ((218, 219, 220), 1120.0 + T, True)])
def test_persistence_10_of_12_strict_close_above(bars, third, ok):  # C == EMA50 does not count as above
    ov = {j: _on_ema50(j, 900.0 + j) for j in bars[:2]}
    if third is not None:
        ov[bars[2]] = _on_ema50(bars[2], third)
    assert sigs(frame(ov)) == ok_or_none(ok)


def test_persistence_window_is_b12_to_b1():
    ov = {j: _on_ema50(j, 900.0 + j) for j in (215, 216, 217)}      # outside the window: irrelevant
    assert sigs(frame(ov)) == ok_or_none(True)


# ---------------------------------------------------------------- swing reference
def test_swing_tie_latest_bar_wins_signal_metadata():
    g, = run(frame({222: (1160, 1180, 1155, 1160)}))['signals']
    assert (g['swing_i'], g['swing']) == (225, 1180.0)


def test_swing_tie_latest_wins_can_kill_the_setup():
    assert sigs(frame({228: (1152, 1180, 1146, 1150)})) == []      # latest max at b-2 -> pullback of 1 bar


@pytest.mark.parametrize('i,ok,swing', [(218, True, 218), (217, True, 225)])
def test_swing_window_b_minus_12_edge(i, ok, swing):
    r = run(frame({i: (1160, 1190, 1155, 1160)}))
    g, = r['signals']
    assert (g['swing_i'], g['swing']) == (swing, 1190.0 if swing == 218 else 1180.0)


def test_swing_window_b_minus_1_edge_empty_pullback():
    assert sigs(frame({229: (1153, 1185, 1152, 1155)})) == []


def test_trigger_bar_high_not_in_swing_window():
    g, = run(frame({230: (1170, 1182, 1160, 1175)}))['signals']
    assert (g['swing_i'], g['swing']) == (225, 1180.0)


@pytest.mark.parametrize('i,ok', [(227, True), (228, False)])
def test_pullback_minimum_two_bars(i, ok):
    rows = {227: (1162, 1181, 1150, 1152), 228: (1152, 1181, 1146, 1150)}
    r = run(frame({i: rows[i]}))
    assert [(g['side'], g['b']) for g in r['signals']] == ok_or_none(ok)
    if ok:
        assert r['signals'][0]['swing_i'] == 227


# ---------------------------------------------------------------- pullback gates
def _depth_rows(low):
    return {226: (1175, 1176, 1172, 1174), 227: (1174, 1175, low, 1172), 228: (1172, 1173, 1171, 1172),
            229: (1172, 1174, 1171, 1173), 230: (1172, 1178, 1171, 1177)}


@pytest.mark.parametrize('low,ok', [(1170.0, True), (1170.0 + T, False), (1170.0 - T, True)])
def test_depth_boundary(low, ok):                                  # 1180 - min low >= 1.0 x ATR[229] = 10
    df = frame(_depth_rows(low), e20_ov={j: 1168.0 for j in range(226, 231)})
    assert sigs(df) == ok_or_none(ok)


@pytest.mark.parametrize('v,ok', [(1143.5, True), (1143.5 - T, False), (1143.5 + T, True)])
def test_ema20_touch_boundary_per_bar_ema(v, ok):                 # low 1146 <= EMA20[228] + 0.25 x ATR[229]
    assert sigs(frame(e20_ov={228: v})) == ok_or_none(ok)


def test_touch_on_trigger_bar_does_not_count():
    df = frame({230: (1152, 1166, 1130, 1164)}, e20_ov={228: 1143.0})   # 228 no longer touches; bar b does
    assert sigs(df) == []


@pytest.mark.parametrize('c,ok', [(1128.0, True), (1128.0 - T, False)])
def test_structure_no_close_below_per_bar_ema50(c, ok):           # EMA50[228] = 1128; equality holds
    assert sigs(frame({228: (1152, 1153, 1127, c)})) == ok_or_none(ok)


@pytest.mark.parametrize('low,ok', [(1124.0, True), (1124.0 - T, False), (1124.0 + T, True)])
def test_structure_floor_ema50_b1_minus_half_atr(low, ok):        # min low >= 1129 - 5
    assert sigs(frame({228: (1152, 1153, low, 1150)})) == ok_or_none(ok)


def test_trigger_low_not_checked_against_floor_and_L_includes_trigger():
    r = run(frame({230: (1152, 1166, 1120, 1164)}))                # 1120 < floor 1124: still a signal
    g, = r['signals']
    assert (g['pb_extreme'], g['stop']) == (1120.0, 1116.0)
    t, = r['trades']
    assert (t['R_unit'], t['target']) == (48.0, 1284.0)


def test_L_includes_trigger_bar():
    g, = run(frame({230: (1152, 1166, 1140, 1164)}))['signals']
    assert (g['pb_extreme'], g['stop']) == (1140.0, 1136.0)


# ---------------------------------------------------------------- trigger gates
@pytest.mark.parametrize('c,ok', [(1156.0, False), (1156.0 + T, True)])
def test_trigger_close_above_prior_high_strict(c, ok):
    assert sigs(frame({230: (1150, 1157, 1149, c)})) == ok_or_none(ok)


@pytest.mark.parametrize('v,ok', [(1164.0, False), (1164.0 - T, True)])
def test_trigger_close_above_ema20_b_strict(v, ok):
    assert sigs(frame(e20_ov={230: v})) == ok_or_none(ok)


@pytest.mark.parametrize('o,ok', [(1161.0, True), (1161.0 + T, False), (1161.0 - T, True)])
def test_trigger_body_boundary(o, ok):                             # C - O >= 0.3 x ATR[229] = 3
    assert sigs(frame({230: (o, 1166, 1150, 1164)})) == ok_or_none(ok)


@pytest.mark.parametrize('c,ok', [(1158.0, True), (1158.0 - T, False), (1158.0 + T, True)])
def test_trigger_close_location_boundary(c, ok):                   # (C - 1140) / 30 >= 0.60
    assert sigs(frame({230: (1152, 1170, 1140, c)})) == ok_or_none(ok)


@pytest.mark.parametrize('c,ok', [(1180.0, False), (1180.0 - T, True)])
def test_trigger_below_swing_high_strict(c, ok):
    assert sigs(frame({230: (1170, 1182, 1160, c)})) == ok_or_none(ok)


def test_flat_trigger_bar_no_signal_either_side():
    for row in ((1164, 1164, 1164, 1164), (1150, 1150, 1150, 1150)):
        df = frame({230: row})
        assert TP.detect(df, 'long') == [] and TP.detect(df, 'short') == []


def test_filters_use_atr_b_minus_1_stop_uses_atr_b():
    g, = run(frame(a_b=5.0))['signals']
    assert g['stop'] == 1145.0                                     # 1146 - 0.2 x ATR[230]
    assert sigs(frame(atr_ov={229: 40.0})) == []                   # depth 34 < 40 at ATR[b-1]


# ---------------------------------------------------------------- entry, gap, quality, cost guard
def _entry(o):
    return {231: (o, o + 1, o - T, o)}


@pytest.mark.parametrize('o,why', [(1142.0, 'gap_stop'), (1130.0, 'gap_stop'), (1142.0 + T, 'cost_guard')])
def test_entry_gap_skips(o, why):
    r = run(frame(_entry(o)))
    assert r['trades'] == [] and [s['skip'] for s in r['skips']] == [why]
    assert sigs(frame(_entry(o))) == [('long', 230)]


def test_entry_is_open_b_plus_1():
    t = one(frame(_entry(1170.0)))
    assert (t['entry_i'], t['entry'], t['R_unit'], t['target']) == (231, 1170.0, 28.0, 1240.0)


@pytest.mark.parametrize('o,ok', [(1202.0, True), (1202.0 + T, False)])
def test_r_cap_equality_passes(o, ok):                             # R <= 3.0 x ATR[230] = 60
    r = run(frame(_entry(o)))
    if ok:
        t, = r['trades']
        assert (t['R_unit'], t['target']) == (60.0, 1352.0)
    else:
        assert r['trades'] == [] and [s['skip'] for s in r['skips']] == ['quality']


@pytest.mark.parametrize('shift,ok', [(1036.0, True), (1036.0 + T, False), (1036.0 - T, True)])
def test_cost_guard_equality_passes_on_actual_entry(shift, ok):   # R 22 / E (1164 + shift) >= 1%
    r = run(frame(shift=shift))
    if ok:
        t, = r['trades']
        assert (t['R_unit'], t['entry']) == (22.0, 1164.0 + shift)
    else:
        assert r['trades'] == [] and [s['skip'] for s in r['skips']] == ['cost_guard']


def test_cost_guard_denominator_is_actual_entry_not_close_b():
    r = run(frame(_entry(1163.0), shift=937.0))                     # E 2100, C[b] 2101, R 21: 21/2100 = 1% passes
    t, = r['trades']
    assert (t['entry'], t['R_unit']) == (2100.0, 21.0)              # 21/2101 would fail


def test_cost_guard_short_uses_real_short_entry():
    df = frame(shift=1036.0)                                        # long: 22 / 2200 passes exactly
    s = flip(df, 2 * 2200.0 + T)                                    # short entry 2200.125: 22 / 2200.125 < 1%
    assert [x['skip'] for x in run(s)['skips']] == ['cost_guard']
    assert run(flip(df, 4400.0))['trades'][0]['side'] == 'short'    # short entry 2200: passes


def test_gap_up_target_rebases_from_actual_entry():
    t = one(frame(_entry(1180.0)))
    assert (t['entry'], t['R_unit'], t['target']) == (1180.0, 38.0, 1275.0)


def test_no_next_bar_still_signals():
    r = run(frame(N=231))
    assert [(g['side'], g['b']) for g in r['signals']] == [('long', 230)]
    assert [s['skip'] for s in r['skips']] == ['no_next_bar']


# ---------------------------------------------------------------- exits and priority (long; mirrored below)
EXITS = {
    'gap_stop': ({235: (1140, 1141, 1139, 1140)}, ('stop', 235, 1140.0, True, -24 / 22)),
    'gap_target': ({235: (1220, 1221, 1219.5, 1220)}, ('target', 235, 1220.0, True, 56 / 22)),
    'open_eq_stop': ({235: (1142, 1143, 1141.5, 1142)}, ('stop', 235, 1142.0, True, -1.0)),
    'open_eq_target': ({235: (1219, 1220, 1218, 1219)}, ('target', 235, 1219.0, True, 2.5)),
    'stop_beats_target': ({235: (1164, 1220, 1141, 1164)}, ('stop', 235, 1142.0, False, -1.0)),
    'target_intrabar': ({235: (1164, 1219, 1160, 1218)}, ('target', 235, 1219.0, False, 2.5)),
    'entry_bar_stop': ({231: (1164, 1166, 1141, 1150)}, ('stop', 231, 1142.0, False, -1.0)),
    'low_eq_stop': ({235: (1164, 1166, 1142, 1160)}, ('stop', 235, 1142.0, False, -1.0)),
    'stop_beats_invalidation': ({250: (1164, 1166, 1141, 1149)}, ('stop', 250, 1142.0, False, -1.0)),
    'invalidation': ({250: (1164, 1166, 1145, 1149), 251: (1160, 1162, 1158, 1160)},
                     ('invalidation', 251, 1160.0, False, -4 / 22)),
    'invalidation_equality_holds': ({250: (1164, 1166, 1145, 1150)}, ('time', 261, 1164.0, False, 0.0)),
    'gap_stop_over_pending': ({250: (1164, 1166, 1145, 1149), 251: (1140, 1141, 1139, 1140)},
                              ('stop', 251, 1140.0, True, -24 / 22)),
    'gap_target_over_pending': ({250: (1164, 1166, 1145, 1149), 251: (1220, 1221, 1219, 1220)},
                                ('target', 251, 1220.0, True, 56 / 22)),
    'invalidation_beats_time': ({260: (1164, 1166, 1158, 1159)}, ('invalidation', 261, 1164.0, False, 0.0)),
    'time_when_close_eq_ema50': ({260: (1164, 1166, 1158, 1160)}, ('time', 261, 1164.0, False, 0.0)),
    'time_pending_gap_stop': ({261: (1140, 1141, 1139, 1140)}, ('stop', 261, 1140.0, True, -24 / 22)),
    'invalidation_gap_below_stop': ({250: (1164, 1166, 1145, 1149), 251: (1130, 1131, 1129, 1130)},
                                    ('stop', 251, 1130.0, True, -34 / 22)),
    'invalidation_b29_exits_b30': ({259: (1164, 1166, 1158, 1158), 260: (1170, 1171, 1169, 1170)},
                                   ('invalidation', 260, 1170.0, False, 6 / 22)),
}


@pytest.mark.parametrize('name', list(EXITS))
def test_exit_cases_long(name):
    ov, want = EXITS[name]
    t = one(frame(ov))
    assert (t['label'], t['exit_i'], t['exit'], t['gap_exit']) == want[:4]
    assert t['R_gross'] == pytest.approx(want[4], abs=1e-15)
    assert t['lt_minus1'] == (want[4] < -1)


def test_time_cap_decided_at_b30_exit_open_b31():
    t = one(frame(N=262))
    assert (t['label'], t['exit_i'], t['hold_bars']) == ('time', 261, 30)


def test_open_end_when_b31_missing():
    r = run(frame(N=261))
    t, = r['trades']
    assert (t['label'], t['exit_i'], t['R_net']) == ('open_end', None, None)
    assert TP.summarize(r['trades'])['open_end'] == 1


def test_invalidation_uses_per_bar_ema50():
    t = one(frame({240: (1164, 1166, 1145, 1145)}))                  # 1145 > EMA50[240] = 1140, < EMA50[250]
    assert (t['label'], t['exit_i']) == ('time', 261)


# ---------------------------------------------------------------- mirror parity (affine K - p, high/low swapped)
@pytest.mark.parametrize('name', list(EXITS) + ['base', 'gap_up'])
def test_long_short_mirror_hand(name):
    ov = {} if name == 'base' else _entry(1180.0) if name == 'gap_up' else EXITS[name][0]
    df = frame(ov)
    lr, sr = run(df), run(flip(df))
    lg, = lr['signals']
    sg, = sr['signals']
    assert (sg['side'], sg['b'], sg['swing_i']) == ('short', lg['b'], lg['swing_i'])
    for k in ('swing', 'pb_extreme', 'stop'):
        assert sg[k] == K - lg[k], k
    lt, = lr['trades']
    st, = sr['trades']
    for k in ('b', 'entry_i', 'exit_i', 'label', 'gap_exit', 'R_unit', 'R_gross', 'hold_bars', 'lt_minus1'):
        assert lt[k] == st[k], k
    for k in ('entry', 'stop', 'target', 'exit'):
        assert st[k] == K - lt[k], k


# ---------------------------------------------------------------- exact costs
@pytest.mark.parametrize('flipped', [False, True])
def test_exact_net_R_costs_known_trade(flipped):
    df = frame({235: (1164, 1166, 1141, 1164)})
    if flipped:
        df = flip(df)
    t = one(df)
    E, R, closes = 1164.0, 22.0, 5 * 1164.0
    if not flipped:
        X, ef, xf = 1142.0, E * 1.0002, 1142.0 * 0.9998
    else:
        X, ef, xf = 1186.0, E * 0.9998, 1186.0 * 1.0002
    assert (t['entry'], t['exit'], t['R_unit'], t['R_gross']) == (E, X, R, -1.0)
    assert t['entry_fill'] == pytest.approx(ef, abs=1e-12) and t['exit_fill'] == pytest.approx(xf, abs=1e-12)
    assert t['slip_R'] == pytest.approx((E + X) * 0.0002 / R, abs=1e-15)
    assert t['fee_R'] == pytest.approx((ef + xf) * 0.0005 / R, abs=1e-15)
    assert t['fund_R'] == pytest.approx(0.00005 * closes / R, abs=1e-15)
    net = -1.0 - ((E + X) * 0.0002 + (ef + xf) * 0.0005 + 0.00005 * closes) / R
    assert t['R_net'] == pytest.approx(net, abs=1e-12)


def test_funding_sums_real_closes_entry_to_exit_inclusive_both_sides():
    ov = {231: (1164, 1170, 1160, 1168), 232: (1168, 1174, 1164, 1172), 233: (1172, 1178, 1168, 1176),
          234: (1176, 1180, 1172, 1178), 235: (1178, 1179, 1141, 1150)}
    for df, closes in ((frame(ov), 5844.0), (flip(frame(ov)), 5 * K - 5844.0)):
        t = one(df)
        assert (t['label'], t['entry_i'], t['exit_i']) == ('stop', 231, 235)
        assert t['fund_R'] == pytest.approx(0.00005 * closes / 22, abs=1e-15)


def test_funding_is_a_cost_for_shorts_too():
    t = one(flip(frame({235: (1164, 1166, 1141, 1164)})))
    assert t['fund_R'] > 0 and t['R_net'] < t['R_gross'] - t['fee_R'] - t['slip_R'] + 1e-15


@pytest.mark.parametrize('col,v', [('o', 0.0), ('h', -1.0), ('l', 0.0), ('c', -5.0)])
def test_non_positive_prices_rejected(col, v):
    df = frame()
    df.loc[50, col] = v
    with pytest.raises(ValueError):
        TP.run_symbol(df)
    with pytest.raises(ValueError):
        TP.detect(df, 'short')


# ---------------------------------------------------------------- setup consumption
def test_taken_signal_consumes_follow_on_same_swing():
    r = run(frame(follow=(231,), a_b=10.0))
    assert [g['b'] for g in r['signals']] == [230] and [(x['b'], x['swing_i']) for x in r['blocked']] == [(231, 225)]
    assert r['skips'] == [] and [t['b'] for t in r['trades']] == [230]


def test_failed_candidate_does_not_consume():
    r = run(frame(follow=(231,), e20_ov={229: 1129.0}, a_b=10.0))            # 230 fails EMA20 > EMA50 at b-1
    assert [g['b'] for g in r['signals']] == [231] and r['blocked'] == []
    t, = r['trades']
    assert (t['b'], t['entry'], t['stop']) == (231, 1164.0, 1144.0)


def test_gap_skipped_signal_consumes():
    r = run(frame({231: (1140, 1141, 1139, 1140), 232: (1145, 1160, 1144, 1158)}))
    assert [s['skip'] for s in r['skips']] == ['gap_stop']
    assert [g['b'] for g in r['signals']] == [230] and [x['b'] for x in r['blocked']] == [232]


def test_gap_follow_on_is_a_real_candidate():
    r = run(frame({231: (1140, 1141, 1139, 1140), 232: (1145, 1160, 1144, 1158)}, e20_ov={229: 1129.0}))
    assert [g['b'] for g in r['signals']] == [232] and [t['b'] for t in r['trades']] == [232]


def test_cost_skipped_signal_consumes():
    r = run(frame(follow=(231,), shift=2000.0, a_b=10.0))
    assert [s['skip'] for s in r['skips']] == ['cost_guard']
    assert [g['b'] for g in r['signals']] == [230] and [x['b'] for x in r['blocked']] == [231]


def test_quality_skipped_signal_consumes():
    r = run(frame(follow=(231,), a_b=5.0))
    assert [s['skip'] for s in r['skips']] == ['quality']
    assert [g['b'] for g in r['signals']] == [230] and [x['b'] for x in r['blocked']] == [231]


def test_overlap_ignored_signal_consumes():
    r = run(frame(pats=(260,), follow=(261,)))
    assert [g['b'] for g in r['signals']] == [230, 260]
    assert [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 260)]
    assert [x['b'] for x in r['blocked']] == [261]
    t, = r['trades']
    assert (t['label'], t['exit_i'], t['exit']) == ('time', 261, 1196.0)


def test_overlap_follow_on_is_a_real_candidate_and_slot_x_is_free():
    r = run(frame(pats=(260,), follow=(261,), e20_ov={259: 1159.0}))
    assert [g['b'] for g in r['signals']] == [230, 261] and r['blocked'] == [] and r['skips'] == []
    assert [(t['b'], t['entry_i'], t['exit_i']) for t in r['trades']][:1] == [(230, 231, 261)]
    assert r['trades'][1]['b'] == 261


def _extend_rows(h231):
    return {231: (1170, h231, 1168, 1178), 232: (1178, 1179, 1165, 1167), 233: (1167, 1168, 1153, 1155),
            234: (1155, 1160, 1154, 1158), 235: (1158, 1172, 1157, 1170)}


def test_blocked_candidate_does_not_extend_setup():
    r = run(frame(_extend_rows(1180.0), a_b=5.0))                  # 230 quality-skipped; 231 blocked (swing 225)
    assert [g['b'] for g in r['signals']] == [230, 235]
    assert [(x['b'], x['swing_i']) for x in r['blocked']] == [(231, 225)]
    assert r['signals'][1]['swing_i'] == 231                         # 231 > 230: allowed (marker stays at 230)
    assert [t['b'] for t in r['trades']] == [235]


@pytest.mark.parametrize('h231,ok', [(1180.0, True), (1180.0 - T, False)])
def test_new_swing_index_must_exceed_prior_signal_b(h231, ok):
    ov = _extend_rows(h231)
    ov[230] = (1160, 1180, 1158, 1175)                               # trigger high ties the swing high
    r = run(frame(ov, a_b=5.0))
    assert [s['skip'] for s in r['skips'] if s['b'] == 230] == ['quality']
    assert [g['b'] for g in r['signals']] == ([230, 235] if ok else [230])
    assert [(x['b'], x['swing_i']) for x in r['blocked']] == ([] if ok else [(235, 230)])


def test_consumption_is_per_symbol_across_sides(monkeypatch):
    """A short candidate whose swing-low index <= the prior long signal b is blocked (injection: the frozen trend
    filters make an opposite-side follow-on within one swing window unreachable on hand tapes)."""
    real = TP.detect

    def fake(df, side, ind=None):
        out = real(df, 'long', ind)
        if side == 'long':
            return out
        return [dict(g, side='short', b=g['b'] + d, swing_i=g['swing_i'] + s) for g in out for d, s in ((3, 0), (9, 6))]
    monkeypatch.setattr(TP, 'detect', fake)
    r = TP.run_symbol(frame(_entry(1142.0)))                         # long 230 gap-skipped (slot free)
    assert [(g['side'], g['b']) for g in r['signals']] == [('long', 230), ('short', 239)]
    assert [(x['side'], x['b'], x['swing_i']) for x in r['blocked']] == [('short', 233, 225)]


# ---------------------------------------------------------------- slot
@pytest.mark.parametrize('b2,taken', [(260, False), (261, True)])
def test_slot_signal_at_x_minus_1_ignored_at_x_taken(b2, taken):
    r = run(frame(pats=(b2,)))
    t1 = r['trades'][0]
    assert (t1['b'], t1['label'], t1['exit_i']) == (230, 'time', 261)
    assert [g['b'] for g in r['signals']] == [230, b2]
    if taken:
        assert [(t['b'], t['entry_i']) for t in r['trades']] == [(230, 231), (261, 262)] and r['skips'] == []
    else:
        assert len(r['trades']) == 1 and [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 260)]


def test_signal_while_open_is_ignored():
    r = run(frame(pats=(250,)))
    assert [t['b'] for t in r['trades']] == [230] and [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 250)]
    assert r['signals'][1]['swing_i'] == 245


def test_open_end_owns_slot():
    r = run(frame(N=261, pats=(260,)))
    t, = r['trades']
    assert (t['b'], t['label']) == (230, 'open_end')
    assert [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 260)]


def test_same_bar_long_short_conflict_is_asserted(monkeypatch):  # injection only: unreachable under the trend filters
    real = TP.detect
    monkeypatch.setattr(TP, 'detect', lambda df, side, ind=None: [dict(g, side=side) for g in real(df, 'long', ind)])
    with pytest.raises(AssertionError):
        TP.run_symbol(frame())


# ---------------------------------------------------------------- random tapes biased toward planted pullbacks
def tape(seed, N=400):
    rng = np.random.default_rng(seed)
    q = 0.25
    rows, c, s, left, phase, cnt, k = [], 1000.0, 1, 0, 'trend', 0, 2
    for _ in range(N):
        if left <= 0:
            s, left = (1 if rng.random() < 0.5 else -1), int(rng.integers(50, 110))
        left -= 1
        o = c
        if phase == 'trend':
            mv = s * rng.uniform(0.5, 3.5)
            cnt += 1
            if cnt >= rng.integers(4, 9):
                phase, cnt, k = 'pull', 0, int(rng.integers(2, 5))
        elif phase == 'pull':
            mv = -s * rng.uniform(1.5, 5.0)
            cnt += 1
            if cnt >= k:
                phase, cnt = 'trig', 0
        else:
            mv = s * rng.uniform(4.0, 9.0)
            phase = 'trend'
        mv += rng.normal(0, 0.5)
        c = round((o + mv) / q) * q
        w = 0.4 if phase == 'trend' and cnt == 0 else 1.0
        h = round((max(o, c) + abs(rng.normal(0, w))) / q) * q
        l = round((min(o, c) - abs(rng.normal(0, w))) / q) * q
        rows.append((o, h, l, c))
    d = pd.DataFrame(rows, columns=['o', 'h', 'l', 'c'])
    d['v'] = 1.0
    return d, None


def oracle(df):
    """Independent Fraction re-statement in REAL prices with explicit long / short branches (no negation trick):
    detector, consumption, slot, exits and costs. Indicators come from TP.indicators (pinned separately above)."""
    e20f, e50f, Af = TP.indicators(df)
    o, h, l, c = ([F(float(x)) for x in df[k]] for k in 'ohlc')
    e20 = [F(float(x)) if np.isfinite(x) else None for x in e20f]
    e50 = [F(float(x)) if np.isfinite(x) else None for x in e50f]
    A = [F(float(x)) if np.isfinite(x) else None for x in Af]
    n = len(c)
    half, q, tenth3 = F(1, 2), F(1, 4), F(3, 10)
    cands = []
    for b in range(220, n):
        if any(v is None for v in e20[b - 12:b + 1] + e50[b - 12:b + 1]):
            continue
        if A[b - 1] is None or A[b] is None or A[b - 1] <= 0 or A[b] <= 0:
            continue
        a, ab = A[b - 1], A[b]
        W = range(b - 12, b)
        # long
        if (e20[b - 1] > e50[b - 1] and e50[b - 1] - e50[b - 7] >= half * a
                and len([j for j in W if c[j] > e50[j]]) >= 10):
            hs = max(h[j] for j in W)
            hi = max(j for j in W if h[j] == hs)
            P = list(range(hi + 1, b))
            if len(P) >= 2:
                ml = min(l[j] for j in P)
                if (hs - ml >= a and any(l[j] <= e20[j] + q * a for j in P) and all(c[j] >= e50[j] for j in P)
                        and ml >= e50[b - 1] - half * a and c[b] > h[b - 1] and c[b] > e20[b]
                        and c[b] - o[b] >= tenth3 * a and h[b] > l[b] and (c[b] - l[b]) / (h[b] - l[b]) >= F(3, 5)
                        and c[b] < hs):
                    cands.append(('long', b, hi, min(ml, l[b]) - F(1, 5) * ab, ab))
        # short
        if (e20[b - 1] < e50[b - 1] and e50[b - 7] - e50[b - 1] >= half * a
                and len([j for j in W if c[j] < e50[j]]) >= 10):
            ls = min(l[j] for j in W)
            li = max(j for j in W if l[j] == ls)
            P = list(range(li + 1, b))
            if len(P) >= 2:
                mh = max(h[j] for j in P)
                if (mh - ls >= a and any(h[j] >= e20[j] - q * a for j in P) and all(c[j] <= e50[j] for j in P)
                        and mh <= e50[b - 1] + half * a and c[b] < l[b - 1] and c[b] < e20[b]
                        and o[b] - c[b] >= tenth3 * a and h[b] > l[b] and (h[b] - c[b]) / (h[b] - l[b]) >= F(3, 5)
                        and c[b] > ls):
                    cands.append(('short', b, li, max(mh, h[b]) + F(1, 5) * ab, ab))
    sig, blk, skp, trs = [], [], [], []
    last, free = -1, 0
    for side, b, si, stop, ab in cands:
        if si <= last:
            blk.append((side, b))
            continue
        sig.append((side, b, si, float(stop)))
        last = b
        if b < free:
            skp.append(('overlap', side, b))
            continue
        e = b + 1
        if e >= n:
            skp.append(('no_next_bar', side, b))
            continue
        E = o[e]
        lng = side == 'long'
        if (E <= stop) if lng else (E >= stop):
            skp.append(('gap_stop', side, b))
            continue
        R = (E - stop) if lng else (stop - E)
        if R > 3 * ab:
            skp.append(('quality', side, b))
            continue
        if R < F(1, 100) * E:
            skp.append(('cost_guard', side, b))
            continue
        tgt = E + F(5, 2) * R if lng else E - F(5, 2) * R
        pend, x = None, None
        for kk in range(e, n):
            if (o[kk] <= stop) if lng else (o[kk] >= stop):
                x, X, lab, gp = kk, o[kk], 'stop', True
                break
            if (o[kk] >= tgt) if lng else (o[kk] <= tgt):
                x, X, lab, gp = kk, o[kk], 'target', True
                break
            if pend:
                x, X, lab, gp = kk, o[kk], pend, False
                break
            if (l[kk] <= stop) if lng else (h[kk] >= stop):
                x, X, lab, gp = kk, stop, 'stop', False
                break
            if (h[kk] >= tgt) if lng else (l[kk] <= tgt):
                x, X, lab, gp = kk, tgt, 'target', False
                break
            if (c[kk] < e50[kk]) if lng else (c[kk] > e50[kk]):
                pend = 'invalidation'
            elif kk >= b + 30:
                pend = 'time'
        if x is None:
            trs.append((side, b, e, None, 'open_end', None, None))
            free = float('inf')
            continue
        g = (X - E) / R if lng else (E - X) / R
        Ef, Xf = float(E), float(X)
        sg = 1 if lng else -1
        cost = ((Ef + Xf) * 0.0002 + (Ef * (1 + sg * 0.0002) + Xf * (1 - sg * 0.0002)) * 0.0005
                + 0.00005 * sum(float(v) for v in c[e:x + 1])) / float(R)
        trs.append((side, b, e, x, lab, float(g), float(g) - cost, gp))
        free = x
    return sig, blk, skp, trs


def module_view(r):
    sig = [(g['side'], g['b'], g['swing_i'], g['stop']) for g in r['signals']]
    blk = [(x['side'], x['b']) for x in r['blocked']]
    skp = [(s['skip'], s['side'], s['b']) for s in r['skips']]
    trs = []
    for t in r['trades']:
        if t['exit_i'] is None:
            trs.append((t['side'], t['b'], t['entry_i'], None, 'open_end', None, None))
        else:
            trs.append((t['side'], t['b'], t['entry_i'], t['exit_i'], t['label'], t['R_gross'], t['R_net'],
                        t['gap_exit']))
    return sig, blk, skp, trs


ORACLE_SEEDS = range(320)


def test_oracle_differential_300_plus_tapes_zero_mismatches():
    mism, n_sig, n_tr, n_blk, labels, skips = [], 0, 0, 0, set(), set()
    for seed in ORACLE_SEEDS:
        df, _ = tape(seed)
        got = module_view(TP.run_symbol(df))
        want = oracle(df)
        ok = got[:3] == want[:3] and len(got[3]) == len(want[3])
        if ok:
            for gt, wt in zip(got[3], want[3]):
                if gt[:5] != wt[:5] or (gt[5] is not None and (gt[5] != wt[5] or gt[7] != wt[7]
                                                               or abs(gt[6] - wt[6]) > 1e-9)):
                    ok = False
        if not ok:
            mism.append(seed)
        n_sig += len(got[0])
        n_tr += len(got[3])
        n_blk += len(got[1])
        labels |= {t[4] for t in got[3]}
        skips |= {s[0] for s in got[2]}
    assert mism == []
    assert n_sig >= 1500 and n_tr >= 400 and n_blk >= 1000                       # the bias produces real coverage
    assert {'stop', 'target', 'invalidation', 'time'} <= labels
    assert {'overlap', 'cost_guard'} <= skips
    longs = sum(1 for seed in range(20) for g in TP.run_symbol(tape(seed)[0])['signals'] if g['side'] == 'long')
    assert longs > 0


@pytest.mark.parametrize('seed', range(8))
def test_random_tape_mirror_exact(seed, monkeypatch):
    """Pre-cost mirror on random tapes: the long indicators are reflected and injected (K - EMA; ATR is invariant) so a
    second float EMA pass cannot move a boundary. Cost guard disabled here: it is evaluated on each real entry."""
    monkeypatch.setattr(TP, 'COST_GUARD', F(0))
    df, _ = tape(seed)
    e20, e50, A = TP.indicators(df)
    df = df.assign(e20=e20, e50=e50, atr=A)
    k = 2048.0
    a, m = TP.run_symbol(df), TP.run_symbol(flip(df, k))
    sw = {'long': 'short', 'short': 'long'}
    assert [(sw[g['side']], g['b'], g['swing_i']) for g in a['signals']] == [(g['side'], g['b'], g['swing_i'])
                                                                              for g in m['signals']]
    assert [(sw[x['side']], x['b']) for x in a['blocked']] == [(x['side'], x['b']) for x in m['blocked']]
    for g, h in zip(a['signals'], m['signals']):
        assert h['stop'] == pytest.approx(k - g['stop'], abs=1e-9)
    assert len(a['trades']) == len(m['trades']) and len(a['trades']) > 0
    for t, u in zip(a['trades'], m['trades']):
        for f in ('b', 'entry_i', 'exit_i', 'label', 'R_unit', 'R_gross', 'hold_bars'):
            assert t.get(f) == u.get(f), f
        assert u['entry'] == k - t['entry']


@pytest.mark.parametrize('seed', [0, 3, 11])
def test_causality_truncate_and_mutate_after_b(seed):
    df, _ = tape(seed)
    full = TP.run_symbol(df)
    assert full['signals']
    rng = np.random.default_rng(99)
    for g in full['signals']:
        b = g['b']
        key = (g['side'], b, g['swing_i'], g['stop'])
        cut = [(x['side'], x['b'], x['swing_i'], x['stop']) for x in TP.detect(df.iloc[:b + 1].copy(), g['side'])]
        assert cut[-1] == key
        m = df.copy()
        kk = np.arange(b + 1, len(m))
        if len(kk):
            oo = m['o'].to_numpy()[kk] + rng.normal(0, 20, len(kk))
            cc = oo + rng.normal(0, 20, len(kk))
            m.loc[kk, 'o'], m.loc[kk, 'c'] = oo, cc
            m.loc[kk, 'h'], m.loc[kk, 'l'] = np.maximum(oo, cc) + 1, np.minimum(oo, cc) - 1
        before = [(x['side'], x['b'], x['swing_i'], x['stop']) for x in TP.run_symbol(m)['signals'] if x['b'] <= b]
        assert before == [(x['side'], x['b'], x['swing_i'], x['stop']) for x in full['signals'] if x['b'] <= b]


def test_planted_generator_invariants():
    for seed in range(12):
        df, _ = tape(seed)
        r = TP.run_symbol(df)
        prev_exit = -1
        for t in r['trades']:
            assert t['entry_i'] == t['b'] + 1 and t['b'] >= prev_exit
            assert t['R_unit'] >= 0.01 * t['entry'] - 1e-9
            if t['exit_i'] is not None:
                assert t['entry_i'] <= t['exit_i'] <= t['b'] + 31
                assert t['R_net'] < t['R_gross']
                prev_exit = t['exit_i']
        s = TP.summarize(r['trades'])
        assert s['total']['n'] == s['long']['n'] + s['short']['n']


# ---------------------------------------------------------------- summary
def test_summary_counts():
    trs = [one(frame(EXITS['gap_stop'][0])), one(flip(frame(EXITS['target_intrabar'][0]))),
           one(frame(EXITS['invalidation_gap_below_stop'][0]))]
    s = TP.summarize(trs)
    assert (s['total']['n'], s['long']['n'], s['short']['n']) == (3, 2, 1)
    assert s['total']['gross_R'] == pytest.approx(-24 / 22 + 2.5 - 34 / 22)
    assert s['total']['lt_minus1'] == 2 and s['total']['lt_minus1_by_label']['stop'] == 2
    assert s['total']['net_R'] == pytest.approx(s['total']['gross_R'] - s['total']['fee_R'] - s['total']['slip_R']
                                                - s['total']['fund_R'])
