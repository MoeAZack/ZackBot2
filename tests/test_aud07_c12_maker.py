"""AUD-07 C12: a maker entry filled INSIDE a candle sees only the declared price path (backtest.path_points) after the first
touch of its limit. Before C12 the backtester filled the limit (the signal candle's close) and then walked the whole fill
candle from its OPEN, so a target / ratchet could fire on price movement that happened before the limit could fill.

Hand-built candles, ATR forced to 1, stop 2 ATR (R = 2), target 1 R. Signal on candle 10 -> limit = close of 10 = 100,
filled on candle 11. Every maker run also carries the 'uncalibrated' label (live posts at bid/ask for MAKER_WAIT_S then falls
back to market; that calibration is BT-08, not modelled here).
"""
import numpy as np, pandas as pd
import backtest as B

SYM = 'TESTUSDT'
N = 30
SIG = 10                 # signal on candle 10 -> limit at its close (100), fill candle 11
FILL = SIG + 1
LIM = 100.0


def book(fill_candle, side=1, after=None):
    """Flat market at 100 (o = h = l = c); candle 11 = fill_candle; later candles flat at its close (or at `after`)."""
    t = pd.date_range('2024-01-01', periods=N, freq='4h')
    o, h, l, c = (np.full(N, LIM) for _ in range(4))
    o[FILL], h[FILL], l[FILL], c[FILL] = fill_candle
    rest = fill_candle[3] if after is None else after
    for j in range(FILL + 1, N):
        o[j] = h[j] = l[j] = c[j] = rest
    bk = B.Book({SYM: pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))})
    bk.arr[SYM]['atr'] = np.full(N, 1.0)
    z = np.zeros(N, bool)
    le, se = z.copy(), z.copy()
    (le if side == 1 else se)[SIG] = True
    bk._sig[('ema_mom', SYM, 'None')] = dict(le=le, se=se, lx=z.copy(), sx=z.copy())
    return bk


def run(bk, side=1, mgmt=None, **kw):
    sl = [dict(key='ema_mom', share=1.0, risk=0.02, max_pos=1, sides='long' if side == 1 else 'short',
               mgmt=dict(stop_atr=2, **(mgmt if mgmt is not None else dict(tp_r=1))), symbols=[SYM])]
    return B.run(bk, sl, start=500.0, warmup=5, maint_margin=0, fund_per_bar=0, entry_order='maker', **kw)


def pnl(entry, entry_fee, level, qty=5.0):
    """backtest.close() for a long: exit at level x (1 - SLIP), taker fee on the exit, entry fee on the entry."""
    x = level * (1 - B.SLIP)
    return qty * (x - entry) - qty * entry * entry_fee - qty * x * B.FEE


def labelled(cv, mid):
    fz = cv.attrs['feasibility']
    assert cv.attrs['maker_model'] == fz['maker_model'] == B.MAKER_MODEL and 'uncalibrated' in B.MAKER_MODEL
    assert fz['maker_midcandle_fills'] == mid, fz


def test_repro_long_no_target_from_the_up_leg_before_the_fill():
    """The C12 repro: red candle (101, 106, 99.5, 99.8) = open -> high -> low -> close. The limit 100 is first touched on
    the high -> low leg, AFTER the high; the remainder 100 -> 99.5 -> 99.8 reaches neither the target 102 nor the stop 98.
    Before C12: TP at +0.95 R in the fill candle."""
    tr, cv = run(book((101.0, 106.0, 99.5, 99.8)))
    assert len(tr) == 0, tr                            # still open at the end (about -0.10 R), never a fill-candle target
    labelled(cv, 1)
    assert abs(cv.iloc[-1] - (500 + 5 * (99.8 - LIM) - 5 * LIM * B.FEE_MAKER)) < 1e-9     # marked at 99.8, maker fee paid


def test_repro_short_mirror():
    """Short mirror: green candle (99, 100.5, 94, 100.2) = open -> low -> high -> close. The short's target 98 is passed on
    the open -> low leg; the limit 100 is first touched on low -> high, after it. Remainder 100 -> 100.5 -> 100.2: no stop
    (102), no target."""
    tr, cv = run(book((99.0, 100.5, 94.0, 100.2), side=-1), side=-1)
    assert len(tr) == 0, tr
    labelled(cv, 1)


def test_limit_touched_late_after_the_target_was_passed_doji():
    """Doji (101, 103, 99, 101): the long's worst-case path is open -> high -> low -> close. The target 102 is passed at the
    high, the limit 100 only touched on the way to the low: remainder 100 -> 99 -> 101, no target in candle 11. The target
    is taken later, when price really gets there."""
    tr, cv = run(book((101.0, 103.0, 99.0, 101.0)))
    assert len(tr) == 0, tr
    tr, cv = run(book((101.0, 103.0, 99.0, 101.0), after=102.5))
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_out[0] == FILL + 1, tr
    labelled(cv, 1)


def test_touched_then_stop_later_in_the_same_candle_counts():
    """Red candle (101, 104, 97, 97.5), breakeven at 0.5 R. Remainder after the fill: 100 -> 97 -> 97.5: the stop 98 is hit
    in the fill candle at 98 (-1 R before fees). Before C12 the up-leg 101 -> 104 (before the fill) moved the stop to
    breakeven, so the trade was booked at 100 (about 0 R)."""
    tr, cv = run(book((101.0, 104.0, 97.0, 97.5)), mgmt=dict(be_r=0.5))
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_in[0] == FILL and tr.i_out[0] == FILL, tr
    assert abs(tr.pnl[0] - pnl(LIM, B.FEE_MAKER, 98.0)) < 1e-9 and tr.R[0] < -1.0, tr
    labelled(cv, 1)


def test_never_touched_does_not_fill():
    """Low 100.5 stays above the limit 100: no maker fill. Without fallback nothing is opened; with the (default) market
    fallback the entry is at the open with taker fee and slippage, and the whole candle applies (not a mid-candle fill)."""
    candle = (101.0, 103.5, 100.5, 103.0)
    tr, cv = run(book(candle), maker_fallback=False)
    assert len(tr) == 0 and cv.attrs['feasibility']['executed'] == 0 and cv.iloc[-1] == 500.0
    labelled(cv, 0)
    tr, cv = run(book(candle))
    e = 101.0 * (1 + B.SLIP)
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_out[0] == FILL, tr           # target e + 2 = 103.02 on the open's path
    labelled(cv, 0)
    assert abs(tr.pnl[0] - pnl(e, B.FEE, e + 2.0)) < 1e-9, tr


def test_fill_exactly_at_the_open():
    """The open is already through the limit (99.5 <= 100): filled at the open with the maker fee, and the WHOLE candle path
    applies (green: open -> low -> high -> close): target 99.5 + 2 = 101.5 reached at the high 102. Also the edge open ==
    limit: fill at the open, whole path."""
    tr, cv = run(book((99.5, 102.0, 99.0, 101.8)))
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_in[0] == tr.i_out[0] == FILL, tr
    assert abs(tr.pnl[0] - pnl(99.5, B.FEE_MAKER, 101.5)) < 1e-9, tr
    labelled(cv, 0)
    tr, cv = run(book((100.0, 102.5, 99.0, 102.2)))
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_out[0] == FILL, tr
    labelled(cv, 0)


def test_market_entries_carry_no_maker_label():
    sl = [dict(key='ema_mom', share=1.0, risk=0.02, max_pos=1, sides='long', mgmt=dict(stop_atr=2, tp_r=1), symbols=[SYM])]
    tr, cv = B.run(book((101.0, 106.0, 99.5, 99.8)), sl, start=500.0, warmup=5, maint_margin=0, fund_per_bar=0)
    assert 'maker_model' not in cv.attrs and 'maker_model' not in cv.attrs['feasibility']
    assert len(tr) == 1 and tr.why[0] == 'tp' and tr.i_out[0] == FILL     # a market fill at the open legitimately sees the up-leg


def test_golden_adapter_maps_maker_for_the_backtester_only():
    import os, sys
    import pytest
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden'))
    from goldenlib.adapters.base import NotExpressible, legacy_slot
    c = {'slot': {'sides': 'long', 'stop': {'atr': '2'}, 'entry': {'type': 'maker', 'fallback': 'none'}}}
    assert legacy_slot(c, maker=True)[2] == dict(entry_order='maker', maker_fallback=False)
    with pytest.raises(NotExpressible):
        legacy_slot(c)                                  # the engine adapter: maker is not expressible
    for bad in ({'type': 'maker'}, {'type': 'maker', 'fallback': 'limit'}, {'type': 'maker', 'fallback': 'none', 'px': 1}):
        with pytest.raises(NotExpressible):
            legacy_slot({'slot': dict(c['slot'], entry=bad)}, maker=True)
