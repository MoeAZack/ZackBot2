"""C1 liquidity_sweep_fvg.v0 synthetic fixtures (draft 6228630862 fixtures 1-10, Codex 6228657686 fix 4, Cowork
6228664249 list). Synthetic in-memory bars only; ATR14 is injected (constant 10 unless a test overrides it) except in the
no-lookahead tests, which use the computed strategies ATR."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import liquidity_sweep_fvg as LS                                                            # noqa: E402

DEF = (100.0, 101.0, 99.0, 100.0)          # o, h, l, c
REST = (92.0, 93.0, 91.0, 92.0)            # after the FVG bar
NEUT = (104.0, 105.0, 103.0, 104.0)        # below P, no body, no gap
PIV = (100.0, 110.0, 99.0, 100.0)          # P = 110
SWEEP = (104.0, 112.0, 103.0, 105.0)       # wick 2 >= 1, close 105 < 110
BEAR = (104.0, 105.0, 94.0, 95.0)          # body 9 >= 8
FVG = (95.0, 96.0, 90.0, 92.0)             # H 96 < L[n-2]


def frame(ov, N=50, off=0.0, atr=10.0, atr_ov=None, rest_from=None, inject=True):
    rows = [DEF] * N
    if rest_from is not None:
        rows = rows[:rest_from] + [REST] * (N - rest_from)
    rows = list(rows)
    for i, r in ov.items():
        rows[i] = r
    a = np.array(rows) + off
    d = pd.DataFrame(a, columns=['o', 'h', 'l', 'c'])
    d['v'] = 1.0
    if inject:
        A = np.full(N, atr)
        for i, v in (atr_ov or {}).items():
            A[i] = v
        d['atr'] = A
    return d


def base(j=10, s=20, d=2, extra=None, N=50, **kw):
    """Pivot at j, sweep at s, FVG at n = s+d (neutral bars between), REST afterwards."""
    ov = {j: PIV, s: SWEEP}
    for k in range(s + 1, s + d - 1):
        ov[k] = NEUT
    ov[s + d - 1], ov[s + d] = BEAR, FVG
    ov.update(extra or {})
    return frame(ov, N=N, rest_from=s + d + 1, **kw)


def sig(df, side='short'):
    return LS.detect(df, side)


def ns(df, side='short'):
    return [g['n'] for g in sig(df, side)]


def trade(df):
    r = LS.run_symbol(df)
    return r['trades'], r['skips']


def flip(df):
    f = df.copy()
    f['o'], f['h'], f['l'], f['c'] = 200 - df['o'], 200 - df['l'], 200 - df['h'], 200 - df['c']
    return f


# ---- fixture 6 + time cap: exact fill bar, stop, target, exit
def test_base_signal_exact_prices_and_time_cap():
    df = base()
    g, = sig(df)
    assert (g['s'], g['n'], g['j'], g['P'], g['fvg_edge']) == (20, 22, 10, 110.0, 103.0)
    assert g['stop'] == 113.0                                       # max H[20..22] 112 + 0.10 x ATR[22] 10
    (t,), sk = trade(df)
    assert sk == []
    assert (t['entry_i'], t['entry'], t['stop'], t['target'], t['R_unit']) == (23, 92.0, 113.0, 50.0, 21.0)
    assert (t['label'], t['exit_i'], t['exit']) == ('time', 35, 92.0)   # cap decided at close n+12, open n+13
    assert t['R_gross'] == 0.0 and t['hold_bars'] == 12
    assert t['entry_fill'] == pytest.approx(92 * (1 - LS.SLIP)) and t['exit_fill'] == pytest.approx(92 * (1 + LS.SLIP))
    assert t['fee_R'] == pytest.approx((t['entry_fill'] + t['exit_fill']) * LS.FEE / 21)
    assert t['fund_R'] == pytest.approx(LS.FUND_PER_BAR * 92 * 13 / 21)
    assert t['R_net'] == pytest.approx(-(t['slip_R'] + t['fee_R'] + t['fund_R']))


def test_entry_is_open_n_plus_1_not_close_n():
    (t,), _ = trade(base(extra={23: (93.0, 94.0, 91.0, 92.0)}))     # close[n] 92, open[n+1] 93
    assert (t['entry_i'], t['entry'], t['R_unit'], t['target']) == (23, 93.0, 20.0, 53.0)


def test_cost_constants_are_the_runner_ones():
    import backtest as BT
    assert (LS.FEE, LS.SLIP, LS.FUND_PER_BAR) == (BT.FEE, BT.SLIP, BT.FUND_PER_BAR) == (0.0005, 0.0002, 0.00005)
    assert LS.FUND_PER_BAR == pytest.approx(3.125e-6 * 16)


# ---- fixture 1 / pivot confirmation lag
@pytest.mark.parametrize('jj', [17, 18])
def test_unconfirmed_left_swing_is_no_pivot(jj):
    df = base(extra={jj: PIV})
    df.loc[10, ['o', 'h', 'l', 'c']] = DEF                          # no confirmed pivot anywhere
    assert sig(df) == []


def test_pivot_at_s_minus_4_is_eligible():
    g, = sig(base(j=16))
    assert g['j'] == 16 and g['n'] == 22


@pytest.mark.parametrize('j,ok', [(10, True), (9, False)])
def test_pivot_window_s_minus_60(j, ok):
    assert ns(base(j=j, s=70, N=100)) == ([72] if ok else [])


# ---- pivot / sweep / wick / close equality (Codex fix 4)
@pytest.mark.parametrize('k', [9, 13])
def test_pivot_equal_neighbour_is_not_pivot(k):
    assert sig(base(extra={k: PIV})) == []


@pytest.mark.parametrize('h,ok', [(110.0, False), (110.5, False), (111.0, True), (111.5, True)])
def test_sweep_and_wick_threshold(h, ok):
    assert ns(base(extra={20: (104.0, h, 103.0, 105.0)})) == ([22] if ok else [])


@pytest.mark.parametrize('c,ok', [(111.0, False), (110.0, False), (109.99, True)])
def test_sweep_close_back_inside_strict(c, ok):                    # fixture 2 + close[s] == P
    assert ns(base(extra={20: (104.0, 112.0, 103.0, c)})) == ([22] if ok else [])


# ---- FVG window / gap / body / close above P
@pytest.mark.parametrize('d,ok', [(2, True), (3, True), (4, False)])
def test_fvg_window(d, ok):                                        # fixture 3: s+3 ok, s+4 none
    assert ns(base(d=d)) == ([20 + d] if ok else [])


@pytest.mark.parametrize('c,ok', [(111.0, False), (110.0, True)])
def test_close_above_P_between_s_and_n(c, ok):                     # fixture 4
    assert ns(base(d=3, extra={21: (104.0, 111.5, 103.0, c)})) == ([23] if ok else [])


@pytest.mark.parametrize('h,ok', [(103.0, False), (102.99, True)])
def test_fvg_gap_strict(h, ok):
    assert ns(base(extra={22: (95.0, h, 90.0, 92.0)})) == ([22] if ok else [])


@pytest.mark.parametrize('c,ok', [(96.0, True), (96.01, False)])
def test_body_threshold_equality(c, ok):
    assert ns(base(extra={21: (104.0, 105.0, 94.0, c)})) == ([22] if ok else [])


def test_body_uses_atr_of_bar_n_minus_1():
    assert ns(base(atr_ov={21: 20.0})) == []                        # 9 < 0.8 x 20
    g, = sig(base(atr_ov={22: 20.0, 20: 10.0}))                     # ATR[n] only moves the stop buffer
    assert g['stop'] == 114.0


def test_wick_uses_atr_of_bar_s():
    assert ns(base(atr_ov={20: 20.1})) == []                        # wick 2 < 0.10 x 20.1
    assert ns(base(atr_ov={20: 20.0})) == [22]


# ---- pivot choice / reuse
def test_newer_pivot_supersedes_older():
    df = base(j=12, extra={5: (100.0, 115.0, 99.0, 100.0)})
    g, = sig(df)
    assert (g['j'], g['P']) == (12, 110.0)


def _failed_sweep(h20, h21):
    ov = {10: PIV, 20: (104.0, h20, 103.0, 105.0), 21: (104.0, h21, 103.0, 104.0)}
    ov.update({k: NEUT for k in range(22, 30)})
    ov.update({30: SWEEP, 31: BEAR, 32: FVG})
    return frame(ov, rest_from=33)


def test_swept_failed_setup_pivot_not_reused():                    # fixture 10
    assert sig(_failed_sweep(112.0, 112.0)) == []
    g, = sig(_failed_sweep(110.0, 110.0))                           # equal high is not a breach
    assert (g['j'], g['n']) == (10, 32)


# ---- entry skips (Cowork a) and cost guard
@pytest.mark.parametrize('o,why', [(113.0, 'gap_stop'), (114.0, 'gap_stop'), (112.99, 'cost_guard'), (100.0, None),
                                   (50.0, None), (49.0, None)])
def test_gap_at_entry(o, why):
    tr, sk = trade(base(extra={23: (o, 115.0, 49.0, 92.0)}))
    if why:
        assert tr == [] and [x['skip'] for x in sk] == [why]
    else:
        assert tr[0]['entry'] == o and tr[0]['R_unit'] == pytest.approx(113.0 - o)


@pytest.mark.parametrize('o,ok', [(2500.0, True), (2500.5, False), (2499.5, True)])
def test_cost_guard_equality(o, ok):                               # fixture 5: R < 0.84% E skips, == passes
    df = base(off=2400.0, extra={20: (104.0, 120.0, 103.0, 105.0), 23: (o - 2400.0, 93.0, 91.0, 92.0)})
    tr, sk = trade(df)
    if ok:
        assert tr[0]['stop'] == 2521.0 and tr[0]['R_unit'] == 2521.0 - o
    else:
        assert tr == [] and sk[0]['skip'] == 'cost_guard'


def test_gap_open_target_rebases_from_actual_entry():
    # Codex 6228710611: a gap down at entry (open 80 vs close[n] 92) is NOT skipped; R = 113 - 80, target = 80 - 2R
    (t,), sk = trade(base(extra={23: (80.0, 81.0, 79.0, 80.0)}))
    assert sk == [] and (t['entry'], t['stop'], t['R_unit'], t['target']) == (80.0, 113.0, 33.0, 14.0)
    (t,), _ = trade(base(extra={23: (80.0, 81.0, 79.0, 80.0), 24: (80.0, 81.0, 14.0, 20.0)}))
    assert (t['label'], t['exit'], t['R_gross']) == ('target', 14.0, 2.0)


def test_stop_side_gap_skips_both_sides():
    df = base(extra={23: (113.5, 115.0, 112.0, 113.0)})
    assert [x['skip'] for x in LS.run_symbol(df)['skips']] == ['gap_stop']
    assert [x['skip'] for x in LS.run_symbol(flip(df))['skips']] == ['gap_stop']


# ---- exits (fixture 7, Codex fix 3, Cowork c/e)
def test_stop_beats_target_same_bar():
    (t,), _ = trade(base(extra={24: (92.0, 114.0, 49.0, 92.0)}))
    assert (t['label'], t['exit_i'], t['exit'], t['R_gross'], t['gap_exit']) == ('stop', 24, 113.0, -1.0, False)


def test_target_hit():
    (t,), _ = trade(base(extra={24: (92.0, 95.0, 49.0, 60.0)}))
    assert (t['label'], t['exit'], t['R_gross']) == ('target', 50.0, 2.0)


def test_entry_bar_intrabar_stop():
    (t,), _ = trade(base(extra={23: (92.0, 113.0, 91.0, 92.0)}))
    assert (t['label'], t['exit_i'], t['R_gross']) == ('stop', 23, -1.0)


def test_gap_through_stop_flagged_below_minus_1():
    (t,), _ = trade(base(extra={24: (120.0, 121.0, 119.0, 120.0)}))
    assert (t['label'], t['exit'], t['gap_exit'], t['lt_minus1']) == ('stop', 120.0, True, True)
    assert t['R_gross'] == pytest.approx(-28 / 21)


def test_invalidation_first_at_close_n_plus_1_fills_open_n_plus_2():
    (t,), _ = trade(base(extra={23: (92.0, 105.0, 91.0, 104.0), 24: (104.0, 105.0, 103.0, 104.0)}))
    assert (t['label'], t['exit_i'], t['exit']) == ('invalidation', 24, 104.0)
    assert t['R_gross'] == pytest.approx(-12 / 21) and not t['lt_minus1']


def test_invalidation_gap_exit_below_minus_1_flagged():
    (t,), _ = trade(base(extra={23: (92.0, 105.0, 91.0, 104.0), 24: (118.0, 119.0, 117.0, 118.0)}))
    assert (t['label'], t['exit'], t['lt_minus1']) == ('invalidation', 118.0, True)
    assert t['R_gross'] == pytest.approx(-26 / 21)


def test_close_at_fvg_edge_is_not_invalidation():
    (t,), _ = trade(base(extra={23: (92.0, 104.0, 91.0, 103.0)}))
    assert t['label'] == 'time'


@pytest.mark.parametrize('k,label,x', [(34, 'invalidation', 35), (33, 'invalidation', 34)])
def test_invalidation_label_beats_time_cap(k, label, x):
    (t,), _ = trade(base(extra={k: (92.0, 105.0, 91.0, 104.0), x: (90.0, 93.0, 89.0, 92.0)}))
    assert (t['label'], t['exit_i'], t['exit']) == (label, x, 90.0)


def test_time_cap_exit_open_n_plus_13():
    (t,), _ = trade(base(extra={35: (90.0, 93.0, 89.0, 92.0)}))
    assert (t['label'], t['exit_i'], t['exit']) == ('time', 35, 90.0)
    assert t['R_gross'] == pytest.approx(2 / 21)


def test_open_at_end():
    (t,), _ = trade(base(N=30))
    assert t['label'] == 'open_end' and t['R_net'] is None


# ---- overlap (Cowork d)
def test_overlap_ignored_but_pivot_consumed():
    ov = {26: (92.0, 100.0, 91.0, 92.0), 31: (96.0, 102.0, 94.0, 95.0), 32: (95.0, 102.0, 85.0, 86.0),
          33: (86.0, 90.0, 84.0, 88.0)}
    ov.update({k: (88.0, 89.0, 87.0, 88.0) for k in range(34, 60)})
    ov.update({40: (96.0, 102.0, 94.0, 95.0), 41: (95.0, 96.0, 85.0, 86.0), 42: (86.0, 90.0, 84.0, 88.0)})
    df = base(extra=ov, N=60)
    assert ns(df) == [22, 33]                                       # second setup sweeps the j=26 pivot
    r = LS.run_symbol(df)
    assert [t['n'] for t in r['trades']] == [22] and r['trades'][0]['exit_i'] == 35
    assert [(x['skip'], x['n']) for x in r['skips']] == [('overlap', 33)]
    # j=26 was breached by bar 31 -> the bar-40 sweep of 100 has no eligible pivot (j=20 at 112 is not swept)
    assert 42 not in ns(df)


def test_signal_on_exit_bar_is_taken():
    df = base(N=70, extra={24: (92.0, 114.0, 49.0, 92.0)})          # stop intrabar at bar 24
    s2 = {30: PIV, 40: SWEEP, 41: BEAR, 42: FVG}
    for i, rrow in s2.items():
        df.loc[i, ['o', 'h', 'l', 'c']] = rrow
    r = LS.run_symbol(df)
    assert [t['n'] for t in r['trades']] == [22, 42]


# ---- fixture 8: L/S mirror
@pytest.mark.parametrize('extra', [{}, {24: (92.0, 114.0, 49.0, 92.0)}, {24: (92.0, 95.0, 49.0, 60.0)},
                                   {23: (92.0, 105.0, 91.0, 104.0), 24: (118.0, 119.0, 117.0, 118.0)},
                                   {35: (90.0, 93.0, 89.0, 92.0)}])
def test_long_short_mirror(extra):
    df = base(extra=extra)
    (s,), _ = trade(df)
    (lg,), _ = trade(flip(df))
    assert lg['side'] == 'long' and s['side'] == 'short'
    for k in ('s', 'n', 'j', 'entry_i', 'exit_i', 'label', 'R_unit', 'lt_minus1'):
        assert lg[k] == s[k], k
    assert lg['R_gross'] == pytest.approx(s['R_gross'], abs=1e-12)
    assert lg['stop'] == 200 - s['stop'] and lg['target'] == 200 - s['target'] and lg['entry'] == 200 - s['entry']
    assert sig(flip(df), 'short') == [] and sig(df, 'long') == []


# ---- fixture 9: no lookahead (computed ATR)
def _strip(gs):
    return [{k: v for k, v in g.items() if not k.startswith('_')} for g in gs]


def test_no_lookahead_truncate_and_mutate():
    df = base(inject=False, N=60)
    full = _strip(sig(df))
    assert [g['n'] for g in full] == [22]
    n = 22
    trunc = _strip(sig(df.iloc[:n + 1].copy()))
    assert trunc == full
    rng = np.random.default_rng(7)
    mut = df.copy()
    mut.loc[n + 1, ['h', 'l', 'c']] = [150.0, 10.0, 140.0]           # bar n+1: only its open is the fill
    for i in range(n + 2, len(mut)):
        o, c = rng.uniform(50, 150, 2)
        mut.loc[i, ['o', 'h', 'l', 'c']] = [o, max(o, c) + 5, min(o, c) - 5, c]
    assert [g for g in _strip(sig(mut)) if g['n'] <= n] == full


def test_summary_counts():
    df = base(extra={24: (120.0, 121.0, 119.0, 120.0)})
    r = LS.run_symbol(df)
    sm = LS.summarize(r['trades'])
    assert sm['total']['n'] == sm['short']['n'] == 1 and sm['total']['lt_minus1'] == 1 and sm['long']['n'] == 0
