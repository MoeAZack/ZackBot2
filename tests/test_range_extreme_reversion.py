"""C4 range_extreme_reversion.v0 synthetic fixtures (Build draft 6232236186, Codex FREEZE 6232261614).
Synthetic in-memory bars only; no cache, store or research data is read.

Hand-built fixtures inject the indicators so every gate boundary is an exact integer / binary-fraction comparison:
EMA50 = 1000 flat, ATR14 = 10 everywhere except ATR14[b] = 20 (so the ATR[b-1] regime/trigger filters and the ATR[b]
stop buffer / R cap are distinguishable). The planted range at b = 230:
  background (1030, 1036, 1024, 1030) to b-31; box b-30..b-1 alternates UP (1025, 1036, 1024, 1035) /
  DN (1035, 1036, 1024, 1025), with the box high 1060 planted at b-20 and the box low 1000 at b-9:
  RH 1060, RL 1000, W 60 (4..12 ATR = 40..120), M 1030, ER = 5 / 295.
  trigger 230 = (1000, 1010, 990, 1006): probe 10 (1..15), close 1006 > RL, location 0.8, 1006 < M.
  entry open 231 = 1006; stop 990 - 0.25 x 20 = 985; R 21 (cap 50); room 24 >= 21; R/E 2.09%; target 1030.
  after-bars (1006, 1010, 1002, 1006): time exit decided at close 254, filled at open 255.
Shorts use the affine reflection K - p with high/low swapped, K = 2012 so the mirrored entry is also 1006.
The random-tape oracle and causality tests use the computed indicators (and an injected-ATR boundary tape family)."""
import os
import sys
from fractions import Fraction as F

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import range_extreme_reversion as RX                                                        # noqa: E402

B = 230
K = 2012.0
T = 0.125                                  # one exact binary tick
BG = (1030, 1036, 1024, 1030)
UP = (1025, 1036, 1024, 1035)
DN = (1035, 1036, 1024, 1025)
TRIG = (1000, 1010, 990, 1006)
AFT = (1006, 1010, 1002, 1006)


def frame(ov=None, N=270, b=B, a_b=20.0, e50_ov=None, atr_ov=None, shift=0.0):
    rows = []
    for i in range(N):
        if i < b - 30:
            r = BG
        elif i < b:
            r = UP if (i - (b - 30)) % 2 == 0 else DN
            if i == b - 20:
                r = (r[0], 1060, r[2], r[3])
            if i == b - 9:
                r = (r[0], r[1], 1000, r[3])
        elif i == b:
            r = TRIG
        else:
            r = AFT
        rows.append(tuple(float(x) for x in r))
    for i, r in (ov or {}).items():
        rows[i] = tuple(float(x) for x in r)
    d = pd.DataFrame(np.array(rows, dtype=float), columns=['o', 'h', 'l', 'c'])
    d['v'] = 1.0
    e50 = np.full(N, 1000.0)
    A = np.full(N, 10.0)
    if b < N:
        A[b] = a_b
    for arr, o in ((e50, e50_ov), (A, atr_ov)):
        for j, v in (o or {}).items():
            arr[j] = v
    for k in 'ohlc':
        d[k] = d[k] + shift
    d['e50'], d['atr'] = e50 + shift, A
    return d


def flip(df, k=K):
    f = df.copy()
    f['o'], f['h'], f['l'], f['c'] = k - df['o'], k - df['l'], k - df['h'], k - df['c']
    if 'e50' in df:
        f['e50'] = k - df['e50']
    return f


def run(df):
    return RX.run_symbol(df)


def sigs(df):
    return [(g['side'], g['b']) for g in run(df)['signals']]


def one(df):
    r = run(df)
    assert len(r['trades']) == 1, r
    return r['trades'][0]


def both(df, ok, b=B):
    """Long tape and its K - p reflection: the same gate verdict, long resp. short."""
    assert sigs(df) == ([('long', b)] if ok else [])
    assert sigs(flip(df)) == ([('short', b)] if ok else [])


def skips(df):
    return [s['skip'] for s in run(df)['skips']]


def cands(df, side='long'):
    return [g['b'] for g in RX.detect(df, side)]


def entry(o):
    return {231: (o, o + 1, o - 1, o)}


# ---------------------------------------------------------------- planted range, long and short, by hand
def test_base_long_exact_signal_and_trade():
    r = run(frame())
    g, = r['signals']
    assert (g['side'], g['b'], g['box_hi'], g['box_lo'], g['mean'], g['extreme'], g['stop']) == (
        'long', 230, 1060.0, 1000.0, 1030.0, 990.0, 985.0)
    assert (g['atr_prev'], g['atr_b']) == (10.0, 20.0)
    t, = r['trades']
    assert (t['entry_i'], t['entry'], t['stop'], t['R_unit'], t['target']) == (231, 1006.0, 985.0, 21.0, 1030.0)
    assert (t['label'], t['exit_i'], t['exit'], t['R_gross'], t['gap_exit'], t['hold_bars']) == (
        'time', 255, 1006.0, 0.0, False, 24)
    assert r['blocked'] == [] and r['skips'] == []


def test_base_short_exact_signal_and_trade():
    r = run(flip(frame()))
    g, = r['signals']
    assert (g['side'], g['b'], g['box_hi'], g['box_lo'], g['mean'], g['extreme'], g['stop']) == (
        'short', 230, 1012.0, 952.0, 982.0, 1022.0, 1027.0)
    t, = r['trades']
    assert (t['entry_i'], t['entry'], t['stop'], t['R_unit'], t['target']) == (231, 1006.0, 1027.0, 21.0, 982.0)
    assert (t['label'], t['exit_i'], t['exit'], t['R_gross']) == ('time', 255, 1006.0, 0.0)


def test_short_direct_pinned_rules_without_reflection():
    """A short written directly in real prices: box 1000..1060 (M 1030), probe high 1070 >= 1061 and <= 1075,
    C 1052 < RH, location (1052 - 1046) / 24 = 0.25 <= 0.40, C > M; stop 1070 + 5. Entry 1052: R 23, room 22 < 23;
    entry 1052.5: R 22.5 = room 22.5 (passes), target 1030 hit by the gap open 1006 at 232."""
    trig = (1060, 1070, 1046, 1052)
    df = frame({230: trig, 231: (1052, 1053, 1051, 1052)})
    g, = run(df)['signals']
    assert (g['side'], g['box_hi'], g['box_lo'], g['mean'], g['extreme'], g['stop']) == (
        'short', 1060.0, 1000.0, 1030.0, 1070.0, 1075.0)
    assert skips(df) == ['room']
    t = one(frame({230: trig, 231: (1052.5, 1053, 1052, 1052.5)}))
    assert (t['side'], t['entry'], t['R_unit'], t['target']) == ('short', 1052.5, 22.5, 1030.0)
    assert (t['label'], t['exit_i'], t['exit'], t['gap_exit'], t['R_gross']) == ('target', 232, 1006.0, True,
                                                                                46.5 / 22.5)


def test_cost_constants_are_the_runner_ones():
    import backtest as BT
    assert (RX.FEE, RX.SLIP, RX.FUND_PER_BAR) == (BT.FEE, BT.SLIP, BT.FUND_PER_BAR) == (0.0005, 0.0002, 0.00005)


def test_frozen_parameters():
    assert (RX.WARM, RX.BOX, RX.SLOPE_LAG, RX.CAP_BARS, RX.COOLDOWN) == (220, 30, 12, 24, 7)
    assert (RX.W_MIN_ATR, RX.W_MAX_ATR, RX.SLOPE_ATR, RX.ER_MAX) == (4, 12, F(1, 2), F(3, 10))
    assert (RX.PROBE_MIN_ATR, RX.PROBE_MAX_ATR, RX.CLOC_MIN, RX.STOP_ATR) == (F(1, 10), F(3, 2), F(3, 5), F(1, 4))
    assert (RX.RCAP_ATR, RX.ROOM_R, RX.COST_GUARD) == (F(5, 2), 1, F(1, 100))


def test_indicator_convention_is_strategies_ema_and_wilder_atr():
    df = tape(5)
    e50, A = RX.indicators(df)
    c, h, l = (df[k].to_numpy() for k in 'chl')
    a, y, ref = 2 / 51, c[0], []
    for x in c:                                                     # seed EMA[0] = close[0], adjust=False
        y = y + a * (x - y)
        ref.append(y)
    assert np.allclose(e50, ref, rtol=0, atol=1e-9)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    y, ref = tr[0], []
    for x in tr:
        y = y + (x - y) / 14
        ref.append(y)
    assert np.allclose(A, ref, rtol=0, atol=1e-9)
    import strategies as ST
    assert np.array_equal(e50, ST.indicators(df)['e50'].to_numpy())


# ---------------------------------------------------------------- warm-up and finiteness
@pytest.mark.parametrize('b,ok', [(220, True), (219, False)])
def test_warmup_earliest_b_220(b, ok):
    assert sigs(frame(N=260, b=b)) == ([('long', b)] if ok else [])


@pytest.mark.parametrize('kw,ok', [
    (dict(atr_ov={229: 0.0}), False), (dict(atr_ov={229: -1.0}), False), (dict(atr_ov={229: np.nan}), False),
    (dict(atr_ov={230: np.nan}), False), (dict(atr_ov={230: 0.0}), False), (dict(atr_ov={228: np.nan}), True),
    (dict(e50_ov={217: np.nan}), False), (dict(e50_ov={229: np.nan}), False), (dict(e50_ov={218: np.nan}), True),
    (dict(e50_ov={216: np.nan}), True), (dict(e50_ov={230: np.nan}), True)])
def test_referenced_values_finite_and_atr_positive(kw, ok):
    both(frame(**kw), ok)


# ---------------------------------------------------------------- regime: box, width, slope, ER
@pytest.mark.parametrize('h,ok', [(1040.0, True), (1040.0 - T, False), (1040.0 + T, True),
                                  (1120.0, True), (1120.0 + T, False), (1120.0 - T, True)])
def test_width_4_and_12_atr_boundaries(h, ok):                    # W = h - 1000 within 40..120 (ATR[b-1] = 10)
    both(frame({210: (1025, h, 1024, 1035)}), ok)


@pytest.mark.parametrize('a,ok', [(15.0, True), (15.0 + T, False)])
def test_regime_uses_atr_b_minus_1(a, ok):                         # W 60 >= 4 x ATR[229]
    both(frame(atr_ov={229: a}), ok)


def test_stop_uses_atr_b_not_b_minus_1():
    g, = run(frame(a_b=8.0))['signals']
    assert g['stop'] == 988.0


@pytest.mark.parametrize('i,row,ok', [
    (200, (1025, 1200, 1024, 1035), False), (199, (1030, 1200, 1024, 1030), True),     # high at b-30 in, b-31 out
    (200, (1025, 1036, 900, 1035), False), (199, (1030, 1036, 900, 1030), True),       # low at b-30 in, b-31 out
    (229, (1035, 1200, 1024, 1025), False)])                                            # b-1 in
def test_box_window_b30_to_b1(i, row, ok):
    both(frame({i: row}), ok)


def test_box_excludes_bar_b():
    """The trigger low 990 is below every box low; were bar b in the box, RL would be 990 and the probe would fail.
    A trigger high above the box high is equally invisible to the box (short side)."""
    both(frame(), True)
    g, = run(frame({230: (1000, 1010, 990, 1006)}))['signals']
    assert g['box_lo'] == 1000.0


@pytest.mark.parametrize('c,ok', [(1019.5, True), (1020.0, False)])
def test_box_excludes_bar_b_high_on_a_long_trigger(c, ok):
    """Box 1000..1040 (W 40, M 1020); the trigger bar's own high 1042 sits above RH. With bar b excluded M stays 1020
    and C 1019.5 < M fires (location 34.5 / 57 >= 0.6) while C 1020 = M does not; were bar b's high in the box, M
    would be 1021 and C 1020 would fire. Mirrored: a short trigger whose low undercuts RL."""
    both(frame({210: (1025, 1040, 1024, 1035), 230: (1000, 1042, 985, c)}), ok)


@pytest.mark.parametrize('v,ok', [(995.0, True), (995.0 - T, False), (1005.0, True), (1005.0 + T, False)])
def test_ema50_slope_boundary_b13_to_b1(v, ok):                    # |EMA50[229] - EMA50[217]| <= 0.5 x 10
    both(frame(e50_ov={217: v}), ok)


@pytest.mark.parametrize('j,ok', [(229, False), (218, True), (216, True), (230, True)])
def test_slope_reference_bars_are_b1_and_b13(j, ok):
    both(frame(e50_ov={j: 1005.0 + T}), ok)


def er_frame(c229=1010.0):
    """Closes 199..229 hand-set: 10 zero moves, 3 x -5, 7 x (-5, +5), 3 x -5: num |1010 - 1040| = 30, den 100."""
    cs = [1040.0] * 11
    for m in [-5] * 3 + [-5, 5] * 7 + [-5] * 3:
        cs.append(cs[-1] + m)
    cs[-1] = c229
    ov, prev = {}, 1030.0
    for j, c in zip(range(199, 230), cs):
        ov[j] = (prev, max(prev, c) + 1, min(prev, c) - 1, c)
        prev = c
    ov[210] = (ov[210][0], 1060.0, ov[210][2], ov[210][3])
    ov[221] = (ov[221][0], ov[221][1], 1000.0, ov[221][3])
    return frame(ov)


@pytest.mark.parametrize('c,ok', [(1010.0, True), (1010.0 - T, False), (1010.0 + T, True)])
def test_er_030_boundary(c, ok):
    both(er_frame(c), ok)


@pytest.mark.parametrize('bump,ok', [(0.0, False), (T, True)])
def test_er_zero_denominator_fails(bump, ok):
    ov = {j: (1030, 1036, 1024, 1030) for j in range(199, 230)}
    ov[210], ov[221] = (1030, 1060, 1024, 1030), (1030, 1036, 1000, 1030)
    ov[215] = (1030, 1036, 1024, 1030 + bump)                      # den 2T, num 0: ER 0 passes
    both(frame(ov), ok)


@pytest.mark.parametrize('i,c,ok', [(199, 1300.0, False), (198, 1300.0, True), (230, 1006.0, True)])
def test_er_window_closes_b31_to_b1(i, c, ok):                     # C[b-31] in the numerator; C[b-32] unused
    df = frame({i: (1030, max(c, 1036) + 1, 1024, c)} if i < 230 else None)
    both(df, ok)


def test_false_trend_wide_box_high_er():
    """Box 1000..1062 (W 62, slope flat by injection) but closes climb 2 per bar: ER = 60 / 60 = 1 -> no signal."""
    ov, prev = {}, 1000.0
    for j in range(199, 230):
        c = 1000.0 + 2 * (j - 199)
        ov[j] = (prev, c + 2, min(prev, c) - 0.0, c)
        prev = c
    ov[200] = (ov[200][0], ov[200][1], 1000.0, ov[200][3])
    df = frame(ov)
    assert run(df)['signals'] == [] and run(flip(df))['signals'] == []
    rh = max(df['h'][200:230])
    assert 40 <= rh - 1000.0 <= 120                                # width alone would pass


# ---------------------------------------------------------------- trigger gates (long tape; short via reflection)
@pytest.mark.parametrize('lo,ok', [(999.0, True), (999.0 + T, False), (999.0 - T, True),
                                   (985.0, True), (985.0 - T, False), (985.0 + T, True)])
def test_probe_010_and_150_atr(lo, ok):
    both(frame({230: (1000, 1010, lo, 1006)}), ok)


@pytest.mark.parametrize('c,ok', [(1000.0, False), (1000.0 + T, True)])
def test_close_back_above_rl_strict(c, ok):
    both(frame({230: (1000, 1002, 990, c)}), ok)


@pytest.mark.parametrize('c,ok', [(1002.0, True), (1002.0 - T, False), (1002.0 + T, True)])
def test_close_location_060_long_040_short(c, ok):                  # (C - 990) / 20 >= 0.60; short (C - L)/(H - L) <= 0.40
    both(frame({230: (1000, 1010, 990, c)}), ok)


def test_high_eq_low_trigger_fails():
    """Injection (close outside [l, h]): without the H > L guard the location test would read 16 >= 0."""
    both(frame({230: (1006, 990, 990, 1006)}), False)
    for row in ((990, 990, 990, 990), (1006, 1006, 1006, 1006)):
        df = frame({230: row})
        assert RX.detect(df, 'long') == [] and RX.detect(df, 'short') == []


@pytest.mark.parametrize('c,ok', [(1030.0, False), (1030.0 - T, True)])
def test_close_below_mean_strict(c, ok):
    both(frame({230: (1000, 1032, 990, c)}), ok)


# ---------------------------------------------------------------- entry, skips and skip order
@pytest.mark.parametrize('o,why', [(985.0, 'gap_stop'), (980.0, 'gap_stop'), (985.0 + T, 'cost_guard')])
def test_entry_vs_stop(o, why):
    for df in (frame(entry(o)), flip(frame(entry(o)))):
        r = run(df)
        assert r['trades'] == [] and [s['skip'] for s in r['skips']] == [why] and len(r['signals']) == 1


@pytest.mark.parametrize('o,ok', [(1007.5, True), (1007.5 + T, False), (1007.5 - T, True)])
def test_room_10R_equality_passes(o, ok):                          # 1030 - E >= E - 985
    for df, e in ((frame(entry(o)), o), (flip(frame(entry(o))), K - o)):
        r = run(df)
        if ok:
            t, = r['trades']
            assert (t['R_unit'], t['entry']) == (o - 985.0, e)
        else:
            assert r['trades'] == [] and [s['skip'] for s in r['skips']] == ['room']


@pytest.mark.parametrize('o,ok', [(1008.0, True), (1008.0 + T, False), (1008.0 - T, True)])
def test_r_cap_25_atr_b_equality_passes(o, ok):                    # a_b 8: stop 988, R <= 20
    for df in (frame(entry(o), a_b=8.0), flip(frame(entry(o), a_b=8.0))):
        r = run(df)
        if ok:
            assert r['trades'][0]['R_unit'] == o - 988.0
        else:
            assert r['trades'] == [] and [s['skip'] for s in r['skips']] == ['quality']


@pytest.mark.parametrize('shift,ok', [(1094.0, True), (1094.0 + T, False), (1094.0 - T, True)])
def test_cost_guard_1pct_on_actual_entry(shift, ok):               # R 21 / E (1006 + shift) >= 1%
    for df in (frame(shift=shift), flip(frame(shift=shift), 2 * (1006.0 + shift))):
        r = run(df)
        if ok:
            t, = r['trades']
            assert (t['R_unit'], t['entry']) == (21.0, 1006.0 + shift)
        else:
            assert r['trades'] == [] and [s['skip'] for s in r['skips']] == ['cost_guard']


def test_cost_guard_short_uses_real_short_entry():
    df = frame(shift=1094.0)                                        # long: 21 / 2100 passes exactly
    assert skips(flip(df, 2 * 2100.0 + T)) == ['cost_guard']        # short entry 2100.125
    assert run(flip(df, 4200.0))['trades'][0]['side'] == 'short'


@pytest.mark.parametrize('kw,why', [
    (dict(ov=entry(985.0)), 'gap_stop'),                                     # gap (cost would also fail)
    (dict(ov=entry(1031.0), a_b=8.0), 'quality'),                            # quality and room fail
    (dict(ov=entry(1006.0), a_b=2.0, shift=1000.0), 'quality'),              # quality and cost fail
    (dict(ov=entry(1008.0), shift=1500.0), 'room'),                          # room and cost fail
    (dict(ov=entry(1035.0)), 'room'),                                        # gap entry beyond the mean: R 50 = cap
    (dict(ov=entry(1030.0)), 'room')])                                       # gap entry at the mean
def test_skip_order_gap_quality_room_cost(kw, why):
    df = frame(**kw)
    k = 2 * (1006.0 + kw.get('shift', 0.0))
    for d in (df, flip(df, k)):
        r = run(d)
        assert r['trades'] == [] and [s['skip'] for s in r['skips']] == [why] and len(r['signals']) == 1


def test_no_next_bar_still_signals():
    r = run(frame(N=231))
    assert [(g['side'], g['b']) for g in r['signals']] == [('long', 230)]
    assert [s['skip'] for s in r['skips']] == ['no_next_bar']


def test_target_is_frozen_mean_not_rebased():
    t = one(frame(entry(1000.0)))
    assert (t['entry'], t['R_unit'], t['target']) == (1000.0, 15.0, 1030.0)


# ---------------------------------------------------------------- exits and priority (long; mirrored below)
INV = (1006, 1008, 988, 989)                                       # close < low[b] 990
EXITS = {
    'gap_stop': ({235: (984, 985, 983, 984)}, ('stop', 235, 984.0, True, -22 / 21)),
    'gap_target': ({235: (1031, 1032, 1030, 1031)}, ('target', 235, 1031.0, True, 25 / 21)),
    'open_eq_stop': ({235: (985, 986, 984.5, 985)}, ('stop', 235, 985.0, True, -1.0)),
    'open_eq_target': ({235: (1030, 1031, 1029, 1030)}, ('target', 235, 1030.0, True, 24 / 21)),
    'stop_beats_target': ({235: (1006, 1031, 984, 1006)}, ('stop', 235, 985.0, False, -1.0)),
    'target_intrabar': ({235: (1006, 1030, 1000, 1010)}, ('target', 235, 1030.0, False, 24 / 21)),
    'entry_bar_stop': ({231: (1006, 1008, 984, 1000)}, ('stop', 231, 985.0, False, -1.0)),
    'entry_bar_target': ({231: (1006, 1031, 1004, 1020)}, ('target', 231, 1030.0, False, 24 / 21)),
    'low_eq_stop': ({235: (1006, 1008, 985, 1000)}, ('stop', 235, 985.0, False, -1.0)),
    'stop_beats_invalidation': ({245: (1006, 1008, 984, 989)}, ('stop', 245, 985.0, False, -1.0)),
    'invalidation': ({245: INV, 246: (1000, 1002, 998, 1000)}, ('invalidation', 246, 1000.0, False, -6 / 21)),
    'invalidation_equality_holds': ({245: (1006, 1008, 988, 990)}, ('time', 255, 1006.0, False, 0.0)),
    'pending_before_intrabar': ({245: INV, 246: (1000, 1040, 980, 1000)},
                                ('invalidation', 246, 1000.0, False, -6 / 21)),
    'gap_stop_over_pending': ({245: INV, 246: (980, 981, 979, 980)}, ('stop', 246, 980.0, True, -26 / 21)),
    'gap_target_over_pending': ({245: INV, 246: (1035, 1036, 1034, 1035)}, ('target', 246, 1035.0, True, 29 / 21)),
    'invalidation_beats_time': ({254: INV}, ('invalidation', 255, 1006.0, False, 0.0)),
    'time_when_close_eq_low_b': ({254: (1006, 1008, 988, 990)}, ('time', 255, 1006.0, False, 0.0)),
    'time_pending_gap_stop': ({255: (980, 981, 979, 980)}, ('stop', 255, 980.0, True, -26 / 21)),
    'time_pending_gap_target': ({255: (1031, 1032, 1030, 1031)}, ('target', 255, 1031.0, True, 25 / 21)),
    'invalidation_b23_exits_b24': ({253: INV, 254: (1010, 1012, 1008, 1010)},
                                   ('invalidation', 254, 1010.0, False, 4 / 21)),
}


@pytest.mark.parametrize('name', list(EXITS))
def test_exit_cases_long(name):
    ov, want = EXITS[name]
    t = one(frame(ov))
    assert (t['label'], t['exit_i'], t['exit'], t['gap_exit']) == want[:4]
    assert t['R_gross'] == pytest.approx(want[4], abs=1e-15)
    assert t['lt_minus1'] == (want[4] < -1)


def test_time_cap_decided_at_b24_exit_open_b25_with_25_funding_bars():
    t = one(frame(N=256))
    assert (t['label'], t['exit_i'], t['hold_bars']) == ('time', 255, 24)
    assert t['fund_R'] == pytest.approx(0.00005 * 25 * 1006.0 / 21.0, abs=1e-15)


def test_open_end_when_b25_missing():
    r = run(frame(N=255))
    t, = r['trades']
    assert (t['label'], t['exit_i'], t['R_net']) == ('open_end', None, None)
    assert RX.summarize(r['trades'])['open_end'] == 1


# ---------------------------------------------------------------- mirror parity (affine K - p, high/low swapped)
@pytest.mark.parametrize('name', list(EXITS) + ['base', 'gap_down', 'a_b8'])
def test_long_short_mirror_hand(name):
    ov = {} if name in ('base', 'a_b8') else entry(1000.0) if name == 'gap_down' else EXITS[name][0]
    df = frame(ov, a_b=8.0 if name == 'a_b8' else 20.0)
    lr, sr = run(df), run(flip(df))
    lg, = lr['signals']
    sg, = sr['signals']
    assert (sg['side'], sg['b']) == ('short', lg['b'])
    for k, kk in (('box_hi', 'box_lo'), ('box_lo', 'box_hi'), ('mean', 'mean'), ('extreme', 'extreme'),
                  ('stop', 'stop')):
        assert sg[k] == K - lg[kk], k
    lt, = lr['trades']
    st, = sr['trades']
    for k in ('b', 'entry_i', 'exit_i', 'label', 'gap_exit', 'R_unit', 'R_gross', 'hold_bars', 'lt_minus1'):
        assert lt[k] == st[k], k
    for k in ('entry', 'stop', 'target', 'exit'):
        assert st[k] == K - lt[k], k


# ---------------------------------------------------------------- exact costs
@pytest.mark.parametrize('flipped', [False, True])
def test_exact_net_R_costs_known_trade(flipped):
    df = frame({235: (1006, 1008, 984, 1006)})
    if flipped:
        df = flip(df)
    t = one(df)
    E, R, closes = 1006.0, 21.0, 5 * 1006.0
    if not flipped:
        X, ef, xf = 985.0, E * 1.0002, 985.0 * 0.9998
    else:
        X, ef, xf = 1027.0, E * 0.9998, 1027.0 * 1.0002
    assert (t['entry'], t['exit'], t['R_unit'], t['R_gross']) == (E, X, R, -1.0)
    assert t['entry_fill'] == pytest.approx(ef, abs=1e-12) and t['exit_fill'] == pytest.approx(xf, abs=1e-12)
    assert t['slip_R'] == pytest.approx((E + X) * 0.0002 / R, abs=1e-15)
    assert t['fee_R'] == pytest.approx((ef + xf) * 0.0005 / R, abs=1e-15)
    assert t['fund_R'] == pytest.approx(0.00005 * closes / R, abs=1e-15)
    net = -1.0 - ((E + X) * 0.0002 + (ef + xf) * 0.0005 + 0.00005 * closes) / R
    assert t['R_net'] == pytest.approx(net, abs=1e-12)


def test_funding_sums_real_closes_entry_to_exit_inclusive_both_sides():
    ov = {231: (1006, 1010, 1004, 1008), 232: (1008, 1012, 1006, 1010), 233: (1010, 1014, 1008, 1012),
          234: (1012, 1016, 1010, 1014), 235: (1014, 1015, 984, 1000)}
    for df, closes in ((frame(ov), 5044.0), (flip(frame(ov)), 5 * K - 5044.0)):
        t = one(df)
        assert (t['label'], t['entry_i'], t['exit_i']) == ('stop', 231, 235)
        assert t['fund_R'] == pytest.approx(0.00005 * closes / 21, abs=1e-15)
        assert t['fund_R'] > 0


@pytest.mark.parametrize('col,v', [('o', 0.0), ('h', -1.0), ('l', 0.0), ('c', -5.0)])
def test_non_positive_prices_rejected(col, v):
    df = frame()
    df.loc[50, col] = v
    with pytest.raises(ValueError):
        RX.run_symbol(df)
    with pytest.raises(ValueError):
        RX.detect(df, 'short')


# ---------------------------------------------------------------- cooldown and consumption
S2 = (1040, 1068, 1036, 1045)                                      # short probe of RH 1060 at 236/237


def follow(df, b2):
    """Long follow-on row at b2 built from the current box: probe 1.5 below RL (stays above the 985 stop of the
    base trade when RL is 990), close RL + 2, location 3.5 / 5.5."""
    rl = float(df['l'][b2 - 30:b2].min())
    df.loc[b2, ['o', 'h', 'l', 'c']] = [rl, rl + 4, rl - 1.5, rl + 2]
    return df


@pytest.mark.parametrize('b2,ok', [(236, False), (237, True)])
def test_cooldown_b_plus_7_long_long(b2, ok):
    df = follow(frame(), b2)
    assert b2 in cands(df)
    r = run(df)
    assert [g['b'] for g in r['signals']] == ([230, b2] if ok else [230])
    assert [(x['side'], x['b'], x['consumed_by']) for x in r['blocked']] == ([] if ok else [('long', b2, 230)])


@pytest.mark.parametrize('b2,ok', [(236, False), (237, True)])
def test_cooldown_across_sides_long_then_short(b2, ok):
    df = frame({b2: S2})
    assert b2 in cands(df, 'short')
    r = run(df)
    assert [(g['side'], g['b']) for g in r['signals']] == ([('long', 230), ('short', b2)] if ok else [('long', 230)])
    assert [(x['side'], x['b']) for x in r['blocked']] == ([] if ok else [('short', b2)])


@pytest.mark.parametrize('b2,ok', [(236, False), (237, True)])
def test_cooldown_across_sides_short_then_long(b2, ok):
    df = flip(frame({b2: S2}))
    r = run(df)
    assert [(g['side'], g['b']) for g in r['signals']] == ([('short', 230), ('long', b2)] if ok else [('short', 230)])


def test_blocked_candidate_does_not_extend_cooldown():
    df = follow(frame({236: S2}), 237)                              # short 236 blocked, long 237 = 230 + 7
    assert 236 in cands(df, 'short') and 237 in cands(df)
    r = run(df)
    assert [(g['side'], g['b']) for g in r['signals']] == [('long', 230), ('long', 237)]
    assert [(x['side'], x['b'], x['consumed_by']) for x in r['blocked']] == [('short', 236, 230)]


def test_failed_candidate_does_not_consume():
    df = follow(frame(e50_ov={217: 990.0}), 233)                     # 230 fails the slope gate
    r = run(df)
    assert [g['b'] for g in r['signals']] == [233] and r['blocked'] == []
    df = follow(frame(), 233)
    assert [x['b'] for x in run(df)['blocked']] == [233]


@pytest.mark.parametrize('kw,why', [
    (dict(ov={231: (980, 981, 979, 980)}), 'gap_stop'), (dict(a_b=5.0), 'quality'),
    (dict(ov=entry(1007.625)), 'room'), (dict(shift=1094.125), 'cost_guard')])
def test_every_skip_consumes(kw, why):
    df = follow(frame(**kw), 233)
    assert 233 in cands(df)
    r = run(df)
    assert [s['skip'] for s in r['skips']] == [why] and r['trades'] == []
    assert [g['b'] for g in r['signals']] == [230] and [x['b'] for x in r['blocked']] == [233]
    df = follow(frame(**kw), 237)
    r = run(df)
    assert [g['b'] for g in r['signals']] == [230, 237] and r['blocked'] == []


def test_overlap_ignored_signal_consumes():
    df = follow(follow(follow(frame(), 240), 243), 247)
    assert {240, 243, 247} <= set(cands(df))
    r = run(df)
    assert [g['b'] for g in r['signals']] == [230, 240, 247]
    assert [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 240), ('overlap', 247)]
    assert [(x['b'], x['consumed_by']) for x in r['blocked']] == [(243, 240)]
    assert [t['b'] for t in r['trades']] == [230]


# ---------------------------------------------------------------- slot
def test_slot_signal_at_x_minus_1_ignored():
    df = frame({240: (1000, 1004, 986, 1000), 241: (1000, 1002, 980, 984)})   # trade 230 stopped intrabar at 241
    r = run(df)
    t1 = r['trades'][0]
    assert (t1['b'], t1['label'], t1['exit_i']) == (230, 'stop', 241)
    assert [g['b'] for g in r['signals']] == [230, 240] and len(r['trades']) == 1
    assert [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 240)]


def test_slot_signal_at_x_taken():
    df = frame({240: (1000, 1004, 982, 1000), 241: (990, 994, 986, 990)}, atr_ov={240: 20.0})
    r = run(df)
    assert [(t['b'], t['label'], t['entry_i'], t['exit_i']) for t in r['trades']][0] == (230, 'stop', 231, 240)
    assert [(t['b'], t['entry_i'], t['entry']) for t in r['trades']][1] == (240, 241, 990.0)
    assert r['skips'] == []


def test_open_end_owns_slot():
    r = run(follow(frame(N=255), 247))
    t, = r['trades']
    assert (t['b'], t['label']) == (230, 'open_end')
    assert [(s['skip'], s['b']) for s in r['skips']] == [('overlap', 247)]


def test_same_bar_long_short_conflict_is_asserted(monkeypatch):  # injection only: unreachable (close location)
    real = RX.detect
    monkeypatch.setattr(RX, 'detect', lambda df, side, ind=None: [dict(g, side=side) for g in real(df, 'long', ind)])
    with pytest.raises(AssertionError):
        RX.run_symbol(frame())


# ---------------------------------------------------------------- random tapes biased toward planted ranges
def tape(seed, N=420, inject=None, flat_e50=False):
    """Ranges with planted probes of the trailing 30-bar box (some exactly on a boundary), plus trend legs.
    inject=A: an injected constant ATR A and boundary-exact probes / closes / locations on the 0.25 grid.
    flat_e50: also inject a flat EMA50 (slope gate always passes) so stalled trades meet re-probes: overlaps."""
    rng = np.random.default_rng(seed)
    q = 0.25
    qz = (lambda v: round(v / q) * q)
    rows, c, phase, left, m, hw, s, quiet, last = [], 600.0, 'range', 0, 600.0, 25.0, 1, 0, 'long'
    atr = 6.0 if inject is None else inject
    for i in range(N):
        if left <= 0:
            phase = 'trend' if (phase == 'range' and rng.random() < 0.5) else 'range'
            left = int(rng.integers(12, 30)) if phase == 'trend' else int(rng.integers(45, 110))
            s = 1 if rng.random() < 0.5 else -1
            m, hw = c, float(rng.uniform(18, 45))
        left -= 1
        quiet -= 1
        o = c
        hist = rows[-30:]
        if phase == 'range' and len(hist) == 30 and rng.random() < (0.1 if quiet > 0 else 0.15):
            sd = last if quiet > 0 else ('long' if rng.random() < 0.5 else 'short')
            last = sd
            lo_ = min(r[2] for r in hist)
            hi_ = max(r[1] for r in hist)
            mid = (lo_ + hi_) / 2
            dep = float(rng.choice([0.1 * atr, 0.1 * atr - q, 1.5 * atr, 1.5 * atr + q,
                                    rng.uniform(0.1, 1.5) * atr, rng.uniform(0.0, 2.0) * atr]))
            if quiet > 0:                                           # shallow re-probe while a trade may be open
                dep = float(rng.choice([0.1 * atr, rng.uniform(0.1, 0.24) * atr]))
            rl = qz(float(rng.choice([1.25, 2.5, 5.0, 7.5, 10.0, 12.5]) + rng.integers(0, 3) * 1.25))
            pick = int(rng.integers(0, 6))
            if sd == 'long':
                l = qz(lo_ - dep)
                h = l + rl
                cc = [lo_, lo_ + q, l + 0.6 * rl, l + 0.6 * rl - q, mid, qz(l + rng.uniform(0.6, 1.0) * rl)][pick]
            else:
                h = qz(hi_ + dep)
                l = h - rl
                cc = [hi_, hi_ - q, h - 0.6 * rl, h - 0.6 * rl + q, mid, qz(h - rng.uniform(0.6, 1.0) * rl)][pick]
            c = qz(cc)
            o = qz(min(max(o, l), h))
            h, l = max(h, o, c), min(l, o, c)
            rows.append((o, h, l, c))
            if quiet <= 0 and rng.random() < 0.6:
                quiet = int(rng.integers(8, 40))                    # a stall after the probe: time exits, overlaps
            continue
        if quiet > 0:
            c = qz(o + rng.normal(0, 0.75))
            rows.append((o, max(o, c) + 0.5, min(o, c) - 0.5, c))
            continue
        if phase == 'range':
            mv = 0.35 * (m - c) + rng.normal(0, hw / 3)
        else:
            mv = s * rng.uniform(1.0, 6.0) + rng.normal(0, 1.0)
        c = qz(o + mv)
        h = qz(max(o, c) + abs(rng.normal(0, 2.0)))
        l = qz(min(o, c) - abs(rng.normal(0, 2.0)))
        if l <= 50:
            c, l = o, min(o, c)
        rows.append((o, h, l, c))
    d = pd.DataFrame(rows, columns=['o', 'h', 'l', 'c'])
    d['v'] = 1.0
    if inject is not None:
        d['atr'] = float(inject)
    if flat_e50:
        d['e50'] = 600.0
    return d


def oracle(df):
    """Independent Fraction re-statement in REAL prices with explicit long / short branches (no negation trick):
    detector, cooldown consumption, slot, skips, exits and costs. Indicators come from RX.indicators (pinned above)."""
    e50f, Af = RX.indicators(df)
    o, h, l, c = ([F(float(x)) for x in df[k]] for k in 'ohlc')
    e50 = [F(float(x)) if np.isfinite(x) else None for x in e50f]
    A = [F(float(x)) if np.isfinite(x) else None for x in Af]
    n = len(c)
    cands = []
    for b in range(220, n):
        if e50[b - 1] is None or e50[b - 13] is None or A[b - 1] is None or A[b] is None:
            continue
        if A[b - 1] <= 0 or A[b] <= 0:
            continue
        a, ab = A[b - 1], A[b]
        RH, RL = max(h[b - 30:b]), min(l[b - 30:b])
        W, M = RH - RL, (RH + RL) / 2
        den = sum(abs(c[i] - c[i - 1]) for i in range(b - 30, b))
        reg = (4 * a <= W <= 12 * a and abs(e50[b - 1] - e50[b - 13]) <= a / 2 and den != 0
               and abs(c[b - 1] - c[b - 31]) / den <= F(3, 10))
        if not reg or not h[b] > l[b]:
            continue
        loc = (c[b] - l[b]) / (h[b] - l[b])
        if RL - F(3, 2) * a <= l[b] <= RL - a / 10 and c[b] > RL and loc >= F(3, 5) and c[b] < M:
            cands.append(('long', b, l[b] - ab / 4, M, l[b], ab))
        if RH + a / 10 <= h[b] <= RH + F(3, 2) * a and c[b] < RH and loc <= F(2, 5) and c[b] > M:
            cands.append(('short', b, h[b] + ab / 4, M, h[b], ab))
    bs = [x[1] for x in cands]
    assert len(bs) == len(set(bs))
    sig, blk, skp, trs = [], [], [], []
    last, free = None, 0
    for side, b, stop, M, ext, ab in cands:
        if last is not None and b - last < 7:
            blk.append((side, b))
            continue
        sig.append((side, b, float(stop), float(M)))
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
        if R > F(5, 2) * ab:
            skp.append(('quality', side, b))
            continue
        if ((M - E) if lng else (E - M)) < R:
            skp.append(('room', side, b))
            continue
        if R < F(1, 100) * E:
            skp.append(('cost_guard', side, b))
            continue
        pend, x = None, None
        for kk in range(e, n):
            if (o[kk] <= stop) if lng else (o[kk] >= stop):
                x, X, lab, gp = kk, o[kk], 'stop', True
                break
            if (o[kk] >= M) if lng else (o[kk] <= M):
                x, X, lab, gp = kk, o[kk], 'target', True
                break
            if pend:
                x, X, lab, gp = kk, o[kk], pend, False
                break
            if (l[kk] <= stop) if lng else (h[kk] >= stop):
                x, X, lab, gp = kk, stop, 'stop', False
                break
            if (h[kk] >= M) if lng else (l[kk] <= M):
                x, X, lab, gp = kk, M, 'target', False
                break
            if (c[kk] < ext) if lng else (c[kk] > ext):
                pend = 'invalidation'
            elif kk - b >= 24:
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
    sig = [(g['side'], g['b'], g['stop'], g['mean']) for g in r['signals']]
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


def oracle_tape(seed):
    return tape(seed, inject=10.0, flat_e50=seed % 4 == 3) if seed % 2 else tape(seed)


def test_oracle_differential_300_plus_tapes_zero_mismatches():
    mism, n_sig, n_tr, n_blk, labels, skp, sides = [], 0, 0, 0, set(), set(), set()
    for seed in ORACLE_SEEDS:
        df = oracle_tape(seed)
        got = module_view(RX.run_symbol(df))
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
        skp |= {s[0] for s in got[2]}
        sides |= {g[0] for g in got[0]}
    assert mism == []
    assert n_sig >= 500 and n_tr >= 280 and n_blk >= 80, (n_sig, n_tr, n_blk)   # the bias produces real coverage
    assert {'stop', 'target', 'invalidation', 'time'} <= labels, labels
    assert {'overlap', 'gap_stop', 'quality', 'room', 'cost_guard', 'no_next_bar'} <= skp, skp
    assert sides == {'long', 'short'}


def test_oracle_tapes_hit_exact_boundaries():
    """The injected-ATR family lands candidates and near-candidates exactly on the written equalities."""
    hits = set()
    for seed in range(1, 120, 2):
        df = oracle_tape(seed)
        h, l, c = (df[k].to_numpy() for k in 'hlc')
        for b in range(220, len(c)):
            rl, rh = l[b - 30:b].min(), h[b - 30:b].max()
            if l[b] == rl - 1.0 or h[b] == rh + 1.0:
                hits.add('probe_min')
            if l[b] == rl - 15.0 or h[b] == rh + 15.0:
                hits.add('probe_max')
            if c[b] == rl or c[b] == rh:
                hits.add('close_eq_box')
            if h[b] > l[b] and (c[b] - l[b]) * 5 in ((h[b] - l[b]) * 3, (h[b] - l[b]) * 2):
                hits.add('location')
            if c[b] == (rl + rh) / 2:
                hits.add('close_eq_mean')
    assert hits == {'probe_min', 'probe_max', 'close_eq_box', 'location', 'close_eq_mean'}


@pytest.mark.parametrize('seed', [0, 3, 4, 7, 8, 10, 11, 15])
def test_random_tape_mirror_exact(seed, monkeypatch):
    """Pre-cost mirror on random tapes: the long indicators are reflected and injected (K - EMA; ATR is invariant) so a
    second float EMA pass cannot move a boundary. Cost guard disabled here: it is evaluated on each real entry."""
    monkeypatch.setattr(RX, 'COST_GUARD', F(0))
    df = oracle_tape(seed)
    e50, A = RX.indicators(df)
    df = df.assign(e50=e50, atr=A)
    k = 4096.0
    a, m = RX.run_symbol(df), RX.run_symbol(flip(df, k))
    sw = {'long': 'short', 'short': 'long'}
    assert [(sw[g['side']], g['b']) for g in a['signals']] == [(g['side'], g['b']) for g in m['signals']]
    assert [(sw[x['side']], x['b']) for x in a['blocked']] == [(x['side'], x['b']) for x in m['blocked']]
    assert [(s['skip'], s['b']) for s in a['skips']] == [(s['skip'], s['b']) for s in m['skips']]
    for g, h in zip(a['signals'], m['signals']):
        assert h['stop'] == pytest.approx(k - g['stop'], abs=1e-9)
        assert h['mean'] == pytest.approx(k - g['mean'], abs=1e-9)
    assert len(a['trades']) == len(m['trades']) and len(a['signals']) > 0
    for t, u in zip(a['trades'], m['trades']):
        for f in ('b', 'entry_i', 'exit_i', 'label', 'R_unit', 'R_gross', 'hold_bars'):
            assert t.get(f) == u.get(f), f
        assert u['entry'] == k - t['entry']


@pytest.mark.parametrize('seed', [0, 3, 11, 14])
def test_causality_truncate_and_mutate_after_b(seed):
    df = tape(seed)
    full = RX.run_symbol(df)
    assert full['signals']
    rng = np.random.default_rng(99)
    for g in full['signals']:
        b = g['b']
        key = (g['side'], b, g['stop'], g['mean'])
        cut = [(x['side'], x['b'], x['stop'], x['mean']) for x in RX.detect(df.iloc[:b + 1].copy(), g['side'])]
        assert cut[-1] == key
        m = df.copy()
        kk = np.arange(b + 1, len(m))
        if len(kk):
            oo = m['o'].to_numpy()[kk] + rng.normal(0, 20, len(kk))
            cc = oo + rng.normal(0, 20, len(kk))
            m.loc[kk, 'o'], m.loc[kk, 'c'] = oo, cc
            m.loc[kk, 'h'], m.loc[kk, 'l'] = np.maximum(oo, cc) + 1, np.minimum(oo, cc) - 1
        before = [(x['side'], x['b'], x['stop'], x['mean']) for x in RX.run_symbol(m)['signals'] if x['b'] <= b]
        assert before == [(x['side'], x['b'], x['stop'], x['mean']) for x in full['signals'] if x['b'] <= b]


def test_planted_generator_invariants():
    for seed in range(12):
        r = RX.run_symbol(oracle_tape(seed))
        prev_exit = -1
        for t in r['trades']:
            assert t['entry_i'] == t['b'] + 1 and t['b'] >= prev_exit
            assert t['R_unit'] >= 0.01 * t['entry'] - 1e-9
            if t['exit_i'] is not None:
                assert t['entry_i'] <= t['exit_i'] <= t['b'] + 25
                assert t['R_net'] < t['R_gross']
                prev_exit = t['exit_i']
        bs = [g['b'] for g in r['signals']]
        assert all(y - x >= 7 for x, y in zip(bs, bs[1:]))
        s = RX.summarize(r['trades'])
        assert s['total']['n'] == s['long']['n'] + s['short']['n']


# ---------------------------------------------------------------- summary
def test_summary_counts():
    trs = [one(frame(EXITS['gap_stop'][0])), one(flip(frame(EXITS['target_intrabar'][0]))),
           one(frame(EXITS['gap_stop_over_pending'][0]))]
    s = RX.summarize(trs)
    assert (s['total']['n'], s['long']['n'], s['short']['n']) == (3, 2, 1)
    assert s['total']['gross_R'] == pytest.approx(-22 / 21 + 24 / 21 - 26 / 21)
    assert s['total']['lt_minus1'] == 2 and s['total']['lt_minus1_by_label']['stop'] == 2
    assert s['total']['net_R'] == pytest.approx(s['total']['gross_R'] - s['total']['fee_R'] - s['total']['slip_R']
                                                - s['total']['fund_R'])
