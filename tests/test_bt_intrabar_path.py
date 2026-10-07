"""FBL-BT01 (issue #13): the backtester fires intrabar events along ONE declared price path (backtest.path_points):
green open->low->high->close, red open->high->low->close, doji = worst case for the position's side (favourable extreme
first, adverse last). A level set or recomputed at a point of the path (basket TP after a safety order, the next pyramid
level, a raised stop) can only be reached by price moves AFTER that point.

Hand-built candles (no strategy code involved: signals and ATR are injected), so every number below can be checked on paper.
The last test replays the LIVE engine along the same path on red and green candles and compares DCA fills / outcomes.
"""
import copy
import numpy as np, pandas as pd, pytest
import backtest as B
import strategies as S

SYM = 'TESTUSDT'
N = 40
ENTRY_SIG = 10          # signal on candle 10 -> entry at the open of candle 11
ATR = 1.0


def book(candles, side=1, sym=SYM, exit_at=None):
    """Flat market at 100 with the given candles {index: (o, h, l, c)}; one entry signal on ENTRY_SIG; ATR forced to 1.
    exit_at: candle index whose close fires the strategy's signal exit."""
    t = pd.date_range('2024-01-01', periods=N, freq='4h')
    px = np.full(N, 100.0)
    o, h, l, c = px.copy(), px.copy(), px.copy(), px.copy()
    for k, (o_, h_, l_, c_) in candles.items():
        o[k], h[k], l[k], c[k] = o_, h_, l_, c_
        if k + 1 < N and k + 1 not in candles:                  # later candles sit flat at this close
            for j in range(k + 1, N):
                if j in candles: break
                o[j] = h[j] = l[j] = c[j] = c_
    bk = B.Book({sym: pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))})
    bk.arr[sym]['atr'] = np.full(N, ATR)
    z = np.zeros(N, bool)
    le, se, lx, sx = z.copy(), z.copy(), z.copy(), z.copy()
    (le if side == 1 else se)[ENTRY_SIG] = True
    if exit_at is not None: (lx if side == 1 else sx)[exit_at] = True
    for key in ('dca_dip', 'ema_mom'):
        bk._sig[(key, sym, 'None')] = dict(le=le, se=se, lx=lx, sx=sx)
    return bk


def run(bk, key='dca_dip', side=1, mgmt=None, **kw):
    sl = [dict(key=key, share=1.0, risk=0.02, max_pos=1, sides='long' if side == 1 else 'short', mgmt=mgmt or {}, symbols=[SYM])]
    tr, cv = B.run(bk, sl, start=500.0, warmup=5, maint_margin=0, **kw)
    return tr


# DCA dip defaults: 3 safety orders 1 ATR apart (1x, 1.5x, 2.25x, 3.375x), basket TP +1 ATR from the average, stop 2 ATR past the last
E_LONG = 100 * (1 + B.SLIP)                  # 100.02
L1_LONG = E_LONG - 1.0                       # first safety order 99.02
AVG1_LONG = (E_LONG + 1.5 * L1_LONG) / 2.5   # 99.42
TP1_LONG = AVG1_LONG + 1.0                   # basket TP after one safety order: 100.42 (was 101.02 before it)
E_SHORT = 100 * (1 - B.SLIP)                 # 99.98
L1_SHORT = E_SHORT + 1.0                     # 100.98
TP1_SHORT = (E_SHORT + 1.5 * L1_SHORT) / 2.5 - 1.0   # 99.58


def test_path_points_policy():
    assert B.path_points(100, 103, 98, 101, 1) == (100, 98, 103, 101)          # green: low first
    assert B.path_points(100, 103, 98, 101, -1) == (100, 98, 103, 101)         # same path whatever the side
    assert B.path_points(100, 103, 98, 99, 1) == (100, 103, 98, 99)            # red: high first
    assert B.path_points(100, 103, 98, 100, 1) == (100, 103, 98, 100)          # doji, long: favourable (high) first
    assert B.path_points(100, 103, 98, 100, -1) == (100, 98, 103, 100)         # doji, short: favourable (low) first
    assert B.path_points(100, 103, 98, 101, 1, worst=True) == (100, 103, 98, 101)


def test_red_candle_dca_long_cannot_hit_the_recomputed_basket_tp():
    """The reproducer: red candle = open -> high -> low -> close. The high (100.9) comes BEFORE the safety-order fill at the
    low, so the new basket TP (100.42) cannot be reached in this candle. The old code tested it against the same high."""
    tr = run(book({12: (100.0, 100.9, 98.9, 99.5)}))
    assert len(tr) == 0 or not ((tr.i_out == 12) & (tr.why == 'tp')).any(), tr
    # the basket stays open with its safety order filled, and takes profit later when price really gets there
    tr = run(book({12: (100.0, 100.9, 98.9, 99.5), 14: (99.5, 100.6, 99.4, 100.5)}))
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_out[0] == 14, tr
    assert tr.R[0] > 0


def test_green_candle_dca_long_does_hit_the_basket_tp_after_the_fill():
    """Green candle = open -> low -> high -> close: the fill at the low comes first, then the high reaches the new TP."""
    tr = run(book({12: (100.0, 100.9, 98.9, 100.5)}))
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_out[0] == 12, tr
    q0 = 10.0 / (1 * 5 + 1.5 * 4 + 2.25 * 3 + 3.375 * 2)          # risk $10 over the 4 orders down to the stop at 95.02
    q = 2.5 * q0
    px = TP1_LONG * (1 - B.SLIP)
    want = (px - AVG1_LONG) * q - q * px * B.FEE - q0 * E_LONG * B.FEE - 1.5 * q0 * L1_LONG * B.FEE \
        - q0 * 100.0 * B.FUND_PER_BAR - q0 * 100.5 * B.FUND_PER_BAR       # funding is charged before the candle's events
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)


def test_doji_dca_long_is_treated_as_the_worst_path():
    """c == o: no colour to infer the path from -> worst case for the long: high first, low last -> no TP after the fill."""
    tr = run(book({12: (100.0, 100.9, 98.9, 100.0)}))
    assert len(tr) == 0, tr


def test_short_side_symmetry():
    """Short DCA: green = open -> low -> high -> close: the low (99.1) comes BEFORE the safety order at the high, so the
    recomputed TP (99.58) is not reachable; on a red candle (high first, then low) it is."""
    tr = run(book({12: (100.0, 101.1, 99.1, 100.5)}, side=-1), side=-1)
    assert len(tr) == 0, tr
    tr = run(book({12: (100.0, 101.1, 99.1, 99.5)}, side=-1), side=-1)
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_out[0] == 12, tr
    assert tr.R[0] > 0
    tr = run(book({12: (100.0, 101.1, 99.1, 100.0)}, side=-1), side=-1)       # doji short: low first -> no TP
    assert len(tr) == 0, tr


def test_stop_before_target_when_both_existed_at_the_open():
    """Unchanged convention: a stop and a target that both stood at the open and are both inside the candle -> the stop,
    even on a red candle whose path would reach the target first (conservative, as before)."""
    tr = run(book({12: (100.0, 101.5, 94.0, 95.0)}))
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    tr = run(book({12: (100.0, 101.5, 94.0, 101.0)}))       # green too
    assert len(tr) == 1 and tr.why[0] == 'stop', tr


PYR = {'stop_atr': 2.0, 'pyramid': {'n': 1, 'step_r': 1.5, 'frac': 0.5}, 'tp1_r': 1.0, 'tp1_frac': 0.5}


def test_pyramid_add_fires_in_price_order_with_tp1():
    """Pyramid analogue. Long, R = 2: tp1 (50 %) at +1R = 102.02, the add (+0.5 unit) at +1.5R = 103.02. One candle runs
    through both. On the way up tp1 comes FIRST and banks half of the ORIGINAL size; the old code added first and then
    banked half of the enlarged size at the lower tp1 price - a fill sequence the market cannot produce."""
    bk = book({12: (100.0, 103.5, 99.5, 103.2)}, exit_at=13)
    tr = run(bk, key='ema_mom', mgmt=copy.deepcopy(PYR))
    assert len(tr) == 1 and tr.why[0] == 'signal' and tr.i_out[0] == 13, tr
    q0 = 10.0 / 2.0                                         # risk $10, stop 2 ATR
    tp1 = E_LONG + 2.0; add = E_LONG + 3.0
    fees = q0 * E_LONG * B.FEE + 0.5 * q0 * add * B.FEE
    # tp1 first: half of q0 at tp1, then the add, then the rest (q0) out at the close of candle 13 (103.2)
    p1 = tp1 * (1 - B.SLIP); px = 103.2 * (1 - B.SLIP)
    avg_after = (0.5 * q0 * E_LONG + 0.5 * q0 * add) / q0
    want = (p1 - E_LONG) * 0.5 * q0 - 0.5 * q0 * p1 * B.FEE + (px - avg_after) * q0 - q0 * px * B.FEE - fees \
        - q0 * 100.0 * B.FUND_PER_BAR - 2 * q0 * 103.2 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)


def test_pyramid_red_candle_add_then_raised_stop_hit_by_the_later_low():
    """Red candle, long with a trailing stop: the add fills on the way to the high, the trail is raised from that high,
    and the later low hits it - with the enlarged size (unchanged behaviour, now from the path walk)."""
    m = {'stop_atr': 2.0, 'trail_atr': 1.0, 'pyramid': {'n': 1, 'step_r': 1.0, 'frac': 0.5}}
    tr = run(book({12: (100.0, 103.0, 101.0, 101.5)}), key='ema_mom', mgmt=m)
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    q0 = 5.0; add = E_LONG + 2.0; stop = 103.0 - 1.0
    avg = (q0 * E_LONG + 0.5 * q0 * add) / (1.5 * q0); q = 1.5 * q0; px = stop * (1 - B.SLIP)
    want = (px - avg) * q - q * px * B.FEE - q0 * E_LONG * B.FEE - 0.5 * q0 * add * B.FEE \
        - q0 * 100.0 * B.FUND_PER_BAR - q0 * 101.5 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)
    # same candle green (low BEFORE the high): the trail raised at the high is only tested against the close -> no stop
    tr = run(book({12: (100.0, 103.0, 99.9, 102.5)}), key='ema_mom', mgmt=m)
    assert len(tr) == 0, tr


def test_dca_runner_breakeven_raised_at_the_tp_is_hit_only_by_a_later_low():
    """Runner on a DCA basket: part banked at the basket TP, the rest moved to breakeven. On a green candle (low first)
    the breakeven stop set at the high is NOT hit by the earlier low."""
    m = {'runner': {'dca_frac': 0.5, 'be_r': 99, 'step_r': 99, 'gap_r': 99}}
    tr = run(book({12: (100.0, 101.2, 99.6, 101.1)}), mgmt=m)
    assert len(tr) == 0 or (tr.why != 'stop').all(), tr
    # red candle with the TP first, then a drop below the average: the remainder is stopped at breakeven in that candle
    tr = run(book({12: (100.0, 101.2, 99.6, 99.7)}), mgmt=m)
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr


def test_trail_from_the_fresh_atr_ratchets_after_a_zero_length_first_leg():
    """Red candle that opens at its high (first leg has zero length): the trail tightened by the smaller ATR of the candle
    that just closed still ratchets after that leg and is hit by the drop to the low - as before FBL-BT01."""
    m = {'stop_atr': 2.0, 'trail_atr': 2.0}
    bk = book({12: (100.0, 100.0, 99.0, 99.2)})
    bk.arr[SYM]['atr'][11] = 0.5                            # trail 100.02 - 2 x 0.5 = 99.02
    tr = run(bk, key='ema_mom', mgmt=m)
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    px = (E_LONG - 1.0) * (1 - B.SLIP)
    want = (px - E_LONG) * 5 - 5 * px * B.FEE - 5 * E_LONG * B.FEE - 5 * 100.0 * B.FUND_PER_BAR - 5 * 99.2 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)
    bk = book({12: (100.0, 100.5, 100.0, 100.3)})           # green, opens at its low: only the close is after the high
    bk.arr[SYM]['atr'][11] = 0.5
    assert len(run(bk, key='ema_mom', mgmt=m)) == 0
    # green candle dipping to 99.0 first: the fresh-ATR trail (99.02) takes effect after that first leg, not at the bare
    # open (unchanged timing; the engine would already raise it at the open - open question in the review request)
    bk = book({12: (100.0, 100.5, 99.0, 100.3)})
    bk.arr[SYM]['atr'][11] = 0.5
    assert len(run(bk, key='ema_mom', mgmt=m)) == 0


# ---------------------------------------------------------------- Codex round 1: the stop is an event on the adverse leg
Q0 = 10.0 / (1 * 5 + 1.5 * 4 + 2.25 * 3 + 3.375 * 2)          # DCA first order: risk $10 over the 4 orders down to the stop
W = (1.0, 1.5, 2.25, 3.375)


def _basket_stop_pnl(side, fills, stop, c_fill_candle):
    """P&L of a DCA basket whose orders `fills` (prices, entry first) all filled and that was then stopped at `stop`.
    Funding: candle 11 (flat, q0 at 100) and the fill candle (charged on q0 before its events)."""
    q = Q0 * sum(W[:len(fills)]); avg = Q0 * sum(w * f for w, f in zip(W, fills)) / q
    px = stop * (1 - side * B.SLIP)
    return side * (px - avg) * q - q * px * B.FEE - sum(Q0 * w * f * B.FEE for w, f in zip(W, fills)) \
        - Q0 * 100.0 * B.FUND_PER_BAR - Q0 * c_fill_candle * B.FUND_PER_BAR


def test_every_safety_order_on_the_way_down_fills_before_the_basket_stop_long():
    """Codex's fixture: red candle O100 H100.2 L94 C99 (path O->H->L->C). On the way down price meets 99.02, 98.02, 97.02
    (all three safety orders fill) and only then the basket stop 95.02, which closes the ENLARGED basket: about -1R plus
    costs. The old whole-candle stop precheck closed the first unit only (-0.21R)."""
    tr = run(book({12: (100.0, 100.2, 94.0, 99.0)}))
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    want = _basket_stop_pnl(1, [E_LONG, 99.02, 98.02, 97.02], 95.02, 99.0)
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)
    assert -1.1 < tr.R[0] < -1.0, tr.R[0]


def test_every_safety_order_on_the_way_up_fills_before_the_basket_stop_short():
    """Short mirror: green candle O100 L99.8 H106 C101 (O->L->H->C): the low does not reach the TP (98.98); on the way up
    the safety orders 100.98 / 101.98 / 102.98 fill, then the basket stop 104.98 closes the enlarged basket."""
    tr = run(book({12: (100.0, 106.0, 99.8, 101.0)}, side=-1), side=-1)
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    want = _basket_stop_pnl(-1, [E_SHORT, 100.98, 101.98, 102.98], 104.98, 101.0)
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)
    assert -1.1 < tr.R[0] < -1.0, tr.R[0]


def test_gap_through_the_stop_fills_at_the_open_without_adds():
    """Open already below the stop (94.5 < 95.02): filled at the open, no safety order (they were never quoted)."""
    tr = run(book({12: (94.5, 99.5, 94.0, 99.0)}))
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    px = 94.5 * (1 - B.SLIP)
    want = (px - E_LONG) * Q0 - Q0 * px * B.FEE - Q0 * E_LONG * B.FEE - Q0 * 100.0 * B.FUND_PER_BAR - Q0 * 99.0 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)
    tr = run(book({12: (105.5, 106.0, 100.5, 101.0)}, side=-1), side=-1)     # short: open above the stop 104.98
    assert len(tr) == 1 and tr.why[0] == 'stop', tr
    px = 105.5 * (1 + B.SLIP)
    want = (E_SHORT - px) * Q0 - Q0 * px * B.FEE - Q0 * E_SHORT * B.FEE - Q0 * 100.0 * B.FUND_PER_BAR - Q0 * 101.0 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)


def test_safety_level_exactly_at_the_stop_fills_first_then_the_stop():
    """Tie (stop_atr 0: the basket stop sits exactly on the last safety order 97.02): the safety order fills, then the
    stop closes the larger basket - the worse outcome for the account."""
    m = {'dca': {'n': 3, 'step_atr': 1.0, 'scale': 1.5, 'tp_atr': 1.0, 'stop_atr': 0.0}}
    tr = run(book({12: (100.0, 100.2, 96.0, 99.0)}), mgmt=m)
    assert len(tr) == 1 and tr.why[0] == 'stop', tr
    q0 = 10.0 / (1 * 3 + 1.5 * 2 + 2.25 * 1)                  # distances to the stop at 97.02: 3, 2, 1, 0
    q = q0 * sum(W); fills = [E_LONG, 99.02, 98.02, 97.02]
    avg = q0 * sum(w * f for w, f in zip(W, fills)) / q; px = 97.02 * (1 - B.SLIP)
    want = (px - avg) * q - q * px * B.FEE - sum(q0 * w * f * B.FEE for w, f in zip(W, fills)) \
        - q0 * 100.0 * B.FUND_PER_BAR - q0 * 99.0 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)


def test_target_and_stop_both_in_the_candle_the_stop_wins_after_the_adds():
    """Ambiguous case (kept convention): red candle reaching the basket TP (101.02) on its way up and the stop on its way
    down. The stop wins - no TP - and the safety orders met before the stop still fill."""
    tr = run(book({12: (100.0, 101.5, 94.0, 95.0)}))
    assert len(tr) == 1 and tr.why[0] == 'stop', tr
    assert tr.pnl[0] == pytest.approx(_basket_stop_pnl(1, [E_LONG, 99.02, 98.02, 97.02], 95.02, 95.0), rel=1e-9)


def test_trailing_stop_raised_earlier_is_met_before_the_safety_orders_below_it():
    """A DCA basket with a tight trail (0.5 ATR): raised to 99.52 on the entry candle and to 99.7 at this candle's high
    (red: the high comes first), it lies ABOVE the first safety order (99.02), so on the way down the stop closes the
    first unit and no safety order fills."""
    tr = run(book({12: (100.0, 100.2, 98.5, 99.0)}), mgmt={'trail_atr': 0.5})
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    px = (100.2 - 0.5) * (1 - B.SLIP)
    want = (px - E_LONG) * Q0 - Q0 * px * B.FEE - Q0 * E_LONG * B.FEE - Q0 * 100.0 * B.FUND_PER_BAR - Q0 * 99.0 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)


def test_pyramid_add_before_the_stop_on_a_red_candle_and_not_on_a_green_one():
    """Pyramid analogue of the precheck: red candle O100 H102.5 L97.5 C98 - the add at 102.02 fills on the way up, then the
    stop 98.02 closes the enlarged position. Green candle with the same range: the low (stop) comes first, no add."""
    m = {'stop_atr': 2.0, 'pyramid': {'n': 1, 'step_r': 1.0, 'frac': 0.5}}
    tr = run(book({12: (100.0, 102.5, 97.5, 98.0)}), key='ema_mom', mgmt=m)
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 12, tr
    q0 = 5.0; add = E_LONG + 2.0; q = 1.5 * q0; avg = (q0 * E_LONG + 0.5 * q0 * add) / q; px = (E_LONG - 2.0) * (1 - B.SLIP)
    want = (px - avg) * q - q * px * B.FEE - q0 * E_LONG * B.FEE - 0.5 * q0 * add * B.FEE \
        - q0 * 100.0 * B.FUND_PER_BAR - q0 * 98.0 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)
    tr = run(book({12: (100.0, 102.5, 97.5, 102.0)}), key='ema_mom', mgmt=m)
    assert len(tr) == 1 and tr.why[0] == 'stop', tr
    px = (E_LONG - 2.0) * (1 - B.SLIP)
    want = (px - E_LONG) * q0 - q0 * px * B.FEE - q0 * E_LONG * B.FEE - q0 * 100.0 * B.FUND_PER_BAR - q0 * 102.0 * B.FUND_PER_BAR
    assert tr.pnl[0] == pytest.approx(want, rel=1e-9)


# ---------------------------------------------------------------- live engine vs backtester on the same path
def _replay_case(candles, side, steps=8):
    import engine as E
    from replay import run_replay
    syms = ['SOLUSDT']                                     # min notional 5 (BTC's 50 is above this small basket)
    n = 300; e = 280
    t = pd.date_range('2024-01-01', periods=n, freq='4h')
    base = 100 + 0.2 * np.sin(np.arange(n) / 3)            # a gentle wave so ATR is ~1 and steady
    o = np.r_[base[0], base[:-1]]; c = base.copy()
    h = np.maximum(o, c) + 0.45; l = np.minimum(o, c) - 0.45
    for k, (o_, h_, l_, c_) in candles.items():
        o[e + k], h[e + k], l[e + k], c[e + k] = o_, h_, l_, c_
    last = c[e + max(candles)]
    for j in range(e + max(candles) + 1, n):                # flat tail at the last close
        o[j] = c[j] = last; h[j] = last + 0.3; l[j] = last - 0.3
    raw = {'SOLUSDT': pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))}
    sig_t = t[e - 1]                                        # signal on candle e-1 -> entry at the open of candle e

    real = S.signals

    def fake(key, d, ctx, params=None, mask_sides=True):
        out = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
        out['le' if side == 1 else 'se'][:] = (pd.to_datetime(d.t) == sig_t).values
        return out
    S.signals = fake
    try:
        sl = [E.sleeve('C', 'dca_dip', 1.0, .02, 1, syms, sides='long' if side == 1 else 'short')]
        r = run_replay(raw, sl, 260, steps=steps)
        bk = B.Book(raw)                                    # the same backtest, kept with its exit candle / reason columns
        r['bt_full'], _ = B.run(bk, [dict(key='dca_dip', share=1.0, risk=.02, max_pos=1, symbols=syms, sides=sl[0]['sides'], mgmt={})],
                                start=500.0, max_lev=10, t0=t[261])
        r['t'] = t
    finally:
        S.signals = real
    return r


def _cmp(r):
    eh = pd.DataFrame(r['history']); bt = r['bt_full']
    m = r['metrics']
    assert m['mismatches'] == 0 and m['stops'] == m['lots'], m
    assert len(eh) == len(bt) == len(r['bt_trades']) == m['matched'], (eh, bt, m)
    t_close = pd.Timestamp(eh.closed[0]).tz_localize(None).floor('4h')   # the engine closes while walking candle j
    assert t_close == r['t'][int(bt.i_out.iloc[0])], (t_close, bt)
    return eh, bt


@pytest.mark.parametrize('side', [1, -1])
def test_engine_and_backtest_agree_on_dca_fills_and_tp_along_the_path(side):
    """Live engine fed marks along the declared path (replay.py walks o->l->h->c / o->h->l->c in small steps) vs the
    backtester on the same candles. Candle 1 is 'against-path' for the TP (the favourable extreme comes before the
    safety-order fill): neither may take the basket TP there. Candle 3 reaches the TP after the fill (legitimate)."""
    s = side
    # candle 0 (entry candle) quiet; candle 1: favourable extreme FIRST, then the safety order -> no TP;
    # candle 2 quiet; candle 3: price comes back through the recomputed basket TP
    if s == 1: cs = {0: (100.0, 100.3, 99.8, 100.0), 1: (100.0, 100.8, 98.6, 99.2), 2: (99.2, 99.5, 99.0, 99.3), 3: (99.3, 100.9, 99.2, 100.8)}
    else: cs = {0: (100.0, 100.2, 99.7, 100.0), 1: (100.0, 101.4, 99.2, 100.8), 2: (100.8, 101.0, 100.5, 100.7), 3: (100.7, 100.8, 99.1, 99.2)}
    r = _replay_case(cs, side)
    eh, bt = _cmp(r)
    assert len(bt) == 1 and bt.why.iloc[0] == 'tp' and int(bt.i_out.iloc[0]) == 283, bt    # NOT 281 (the fill candle)
    assert eh.exit_reason[0] == 'basket_tp' and int(eh.dca[0]) == 1, eh.to_dict('records')
    assert abs(eh.pnl[0] / eh.risk_usd[0] - bt.R.iloc[0]) <= 0.1, (eh.to_dict('records'), bt)


@pytest.mark.parametrize('side', [1, -1])
def test_engine_and_backtest_agree_on_safety_orders_then_basket_stop_in_one_candle(side):
    """Codex round 1, live engine: one candle runs through every safety order and the basket stop. The engine adds each
    safety order as its mark crosses it and the exchange stop then closes the enlarged basket; the backtester must book
    the same: 3 safety orders, stop, about -1R."""
    if side == 1: cs = {0: (100.0, 100.3, 99.8, 100.0), 1: (100.0, 100.2, 93.5, 99.0)}         # red: high, then down
    else: cs = {0: (100.0, 100.2, 99.7, 100.0), 1: (100.0, 106.5, 99.8, 101.0)}               # green: low, then up
    r = _replay_case(cs, side, steps=40)                    # fine steps: the engine fills each order near its level
    eh, bt = _cmp(r)
    assert len(bt) == 1 and bt.why.iloc[0] == 'stop' and int(bt.i_out.iloc[0]) == 281, bt
    assert int(eh.dca[0]) == 3, eh.to_dict('records')
    assert abs(eh.pnl[0] / eh.risk_usd[0] - bt.R.iloc[0]) <= 0.1, (eh.to_dict('records'), bt)
    assert bt.R.iloc[0] < -0.95, bt


@pytest.mark.parametrize('side', [1, -1])
def test_engine_and_backtest_agree_when_the_path_allows_the_tp_in_the_fill_candle(side):
    """Same, with the fill candle coloured so that the safety order comes FIRST and the TP is reached after it."""
    if side == 1: cs = {0: (100.0, 100.3, 99.8, 100.0), 1: (100.0, 101.0, 98.6, 100.9)}         # green: low then high
    else: cs = {0: (100.0, 100.2, 99.7, 100.0), 1: (100.0, 101.4, 99.0, 99.1)}                 # red: high then low
    r = _replay_case(cs, side)
    eh, bt = _cmp(r)
    assert len(bt) == 1 and bt.why.iloc[0] == 'tp', bt
    assert eh.exit_reason[0] == 'basket_tp' and int(eh.dca[0]) == 1, eh.to_dict('records')
    assert abs(eh.pnl[0] / eh.risk_usd[0] - bt.R.iloc[0]) <= 0.1, (eh.to_dict('records'), bt)
    assert int(bt.i_out.iloc[0]) == 281, bt
