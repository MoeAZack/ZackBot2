"""BT02 P1-C (Codex review): partial exits in the backtester follow the live engine's close rules when a real exchange rule
is supplied for the symbol - ONE shared helper set in feasibility.py (close_qty / remaining_qty / ladder_qty), used by
engine._market_close / _apply_close / the take-profit ladder AND by backtest.close():
  - tp1, ladder levels and the runner's basket part are floored to the step (never rounded up);
  - a partial that floors to 0 sends nothing and leaves the position unchanged (the partial is still marked done);
  - the ladder closes the whole remainder instead of leaving dust below minQty / minNotional (tp1 / runner part: no such
    rule in the engine, so none in the backtest);
  - without a rule (exchange_rules=None, or a symbol missing from the snapshot) every exit is the raw fraction, unchanged;
  - full closes (stop, signal, tp) are unchanged; a partial that closes everything removes the position (no zero-qty
    position can survive to produce a second trade record).
Hand-built candles (signals and ATR injected): every number is checkable on paper.
"""
import copy, math, random
import numpy as np, pandas as pd, pytest
import backtest as B
import feasibility as F

SYM = 'TESTUSDT'
N = 30
SIG = 10                                  # signal on candle 10 -> entry at the open of candle 11
E_L = 100 * (1 + B.SLIP)                  # long entry 100.02
E_S = 100 * (1 - B.SLIP)                  # short entry 99.98
RULE = dict(step=0.1, min_qty=0.1, min_notional=5.0)


def book(candles, side=1, exit_at=None):
    """Flat at 100 with the given candles {index: (o, h, l, c)} (later candles stay flat at the last close); ATR 1."""
    t = pd.date_range('2024-01-01', periods=N, freq='4h')
    o, h, l, c = (np.full(N, 100.0) for _ in range(4))
    last = 100.0
    for k in range(N):
        if k in candles: o[k], h[k], l[k], c[k] = candles[k]; last = c[k]
        elif k > min(candles or [N]): o[k] = h[k] = l[k] = c[k] = last
    bk = B.Book({SYM: pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=1000.0))})
    bk.arr[SYM]['atr'] = np.full(N, 1.0)
    z = np.zeros(N, bool)
    le, se, lx, sx = z.copy(), z.copy(), z.copy(), z.copy()
    (le if side == 1 else se)[SIG] = True
    if exit_at is not None: (lx if side == 1 else sx)[exit_at] = True
    for key in ('ema_mom', 'dca_dip'):
        bk._sig[(key, SYM, 'None')] = dict(le=le, se=se, lx=lx, sx=sx)
    return bk


def run(bk, mgmt, risk, side=1, rule=RULE, key='ema_mom'):
    sl = [dict(key=key, share=1.0, risk=risk, max_pos=1, sides='long' if side == 1 else 'short', mgmt=mgmt, symbols=[SYM])]
    xr = None if rule is None else {SYM: dict(rule)}
    tr, cv = B.run(bk, sl, start=500.0, warmup=5, maint_margin=0, fund_per_bar=0, exchange_rules=xr)
    return tr, cv


def leg(side, e, px, q):
    """P&L of closing q at trigger px (slippage + taker fee), entry e."""
    x = px * (1 - side * B.SLIP)
    return side * (x - e) * q - q * x * B.FEE


TP1 = {'stop_atr': 2.0, 'tp1_r': 1.0, 'tp1_frac': 0.5}       # R = 2: tp1 at entry +- 2


# ---------------------------------------------------------------- tp1 near a step boundary
@pytest.mark.parametrize('side', [1, -1])
def test_tp1_floored_to_the_step_long_and_short(side):
    """q0 = 0.3 (risk 0.6 USDT / R 2), tp1 half = 0.15 -> 0.1 is closed at tp1 (floor), 0.2 at the signal exit.
    Legacy (no rule): 0.15 / 0.15 as before."""
    e = E_L if side == 1 else E_S
    cnd = {12: (100.0, 102.5, 99.5, 102.3)} if side == 1 else {12: (100.0, 100.5, 97.5, 97.7)}
    tp1, ex = (e + 2.0, 102.3) if side == 1 else (e - 2.0, 97.7)
    tr, _ = run(book(cnd, side, exit_at=13), copy.deepcopy(TP1), 0.0012, side)
    q0 = F.round_step(500.0 * 0.0012 / 2.0, 0.1)
    assert q0 == 0.3 and len(tr) == 1 and tr.why[0] == 'signal'
    want = -q0 * e * B.FEE + leg(side, e, tp1, 0.1) + leg(side, e, ex, 0.2)
    assert tr.pnl[0] == pytest.approx(want, rel=1e-12)
    trl, _ = run(book(cnd, side, exit_at=13), copy.deepcopy(TP1), 0.0012, side, rule=None)
    ql = 500.0 * 0.0012 / 2.0
    assert trl.pnl[0] == pytest.approx(-ql * e * B.FEE + leg(side, e, tp1, ql * 0.5) + leg(side, e, ex, ql - ql * 0.5), rel=1e-12)
    assert trl.pnl[0] != pytest.approx(tr.pnl[0], rel=1e-9)


def test_tp1_exactly_on_a_step_is_not_floored_one_step_down():
    """q0 = 0.4, half = 0.2 (a step multiple in float terms): 0.2 is closed, not 0.1 (the engine's 1e-9 tolerance)."""
    cnd = {12: (100.0, 102.5, 99.5, 102.3)}
    tr, _ = run(book(cnd, exit_at=13), copy.deepcopy(TP1), 0.0016)
    assert tr.pnl[0] == pytest.approx(-0.4 * E_L * B.FEE + leg(1, E_L, E_L + 2, 0.2) + leg(1, E_L, 102.3, 0.2), rel=1e-12)


def test_partial_that_floors_to_zero_closes_nothing_and_is_marked_done():
    """q0 = 0.1 (one step), tp1 half = 0.05 -> 0: no order (engine: `if qty <= 0: return 0.0`), the whole 0.1 stays and
    leaves at the signal exit; tp1 is not retried on the next candle (marked done like the engine's lot['tp1'] = True)."""
    cnd = {12: (100.0, 102.5, 99.5, 102.3), 13: (102.3, 102.6, 102.1, 102.4)}     # 13 reaches tp1 again
    tr, _ = run(book(cnd, exit_at=14), copy.deepcopy(TP1), 0.0004)
    assert len(tr) == 1 and tr.why[0] == 'signal' and tr.i_out[0] == 14
    assert tr.pnl[0] == pytest.approx(-0.1 * E_L * B.FEE + leg(1, E_L, 102.4, 0.1), rel=1e-12)


def test_tp1_closing_everything_removes_the_position_no_zero_qty_survivor():
    """F2 guard: tp1_frac 1 with a rule floors to the whole position -> close() returns True, walk_path returns True and
    the position is deleted. A zero-qty survivor would write a SECOND trade record at the signal exit of candle 13."""
    cnd = {12: (100.0, 102.5, 99.5, 102.3)}
    tr, cv = run(book(cnd, exit_at=13), dict(TP1, tp1_frac=1.0), 0.0012)
    assert len(tr) == 1 and tr.why[0] == 'tp1' and tr.i_out[0] == 12
    assert tr.pnl[0] == pytest.approx(-0.3 * E_L * B.FEE + leg(1, E_L, E_L + 2, 0.3), rel=1e-12)
    assert cv.iloc[-1] == pytest.approx(500.0 + tr.pnl[0], rel=1e-12) and cv.iloc[-1] == cv.iloc[-5]


# ---------------------------------------------------------------- ladder: no dust below the venue minimum
LAD = lambda f: {'stop_atr': 2.0, 'tps': [[1.0, f]]}


@pytest.mark.parametrize('rule,whole', [
    (dict(step=0.1, min_qty=0.1, min_notional=25.0), True),     # rest 0.2 x 102 = 20.4 < 25 -> whole lot at the level
    (dict(step=0.1, min_qty=0.3, min_notional=5.0), True),      # rest 0.2 < minQty 0.3 -> whole lot
    (dict(step=0.1, min_qty=0.1, min_notional=5.0), False),     # rest 0.2 tradable -> stays, closed at the signal
])
def test_ladder_remainder_below_venue_minimum_closes_the_whole_lot(rule, whole):
    cnd = {12: (100.0, 102.5, 99.5, 102.3)}
    tr, _ = run(book(cnd, exit_at=13), LAD(0.6), 0.002, rule=rule)          # q0 = 0.5, level 0.6 x 0.5 = 0.3
    lv = E_L + 2.0
    if whole:
        assert len(tr) == 1 and tr.why[0] == 'tp_ladder' and tr.i_out[0] == 12
        assert tr.pnl[0] == pytest.approx(-0.5 * E_L * B.FEE + leg(1, E_L, lv, 0.5), rel=1e-12)
    else:
        assert len(tr) == 1 and tr.why[0] == 'signal' and tr.i_out[0] == 13
        assert tr.pnl[0] == pytest.approx(-0.5 * E_L * B.FEE + leg(1, E_L, lv, 0.3) + leg(1, E_L, 102.3, 0.2), rel=1e-12)


def test_ladder_level_floored_and_dust_judged_on_the_unfloored_rest_like_the_engine():
    """q0 0.5, level 55 % -> 0.275 wanted, rest 0.225 is tradable -> floor(0.275) = 0.2 closed, 0.3 left."""
    cnd = {12: (100.0, 102.5, 99.5, 102.3)}
    tr, _ = run(book(cnd, exit_at=13), LAD(0.55), 0.002)
    assert tr.why[0] == 'signal'
    assert tr.pnl[0] == pytest.approx(-0.5 * E_L * B.FEE + leg(1, E_L, E_L + 2, 0.2) + leg(1, E_L, 102.3, 0.3), rel=1e-12)
    trl, _ = run(book(cnd, exit_at=13), LAD(0.55), 0.002, rule=None)       # legacy: raw 0.275 / 0.225
    ql = 500.0 * 0.002 / 2.0
    assert trl.pnl[0] == pytest.approx(-ql * E_L * B.FEE + leg(1, E_L, E_L + 2, ql * 0.55) + leg(1, E_L, 102.3, ql - ql * 0.55), rel=1e-12)


def test_short_ladder_dust_rule():
    cnd = {12: (100.0, 100.5, 97.5, 97.7)}
    tr, _ = run(book(cnd, side=-1, exit_at=13), LAD(0.6), 0.002, side=-1, rule=dict(step=0.1, min_qty=0.1, min_notional=25.0))
    assert len(tr) == 1 and tr.why[0] == 'tp_ladder' and tr.i_out[0] == 12      # rest 0.2 x 98 < 25
    assert tr.pnl[0] == pytest.approx(-0.5 * E_S * B.FEE + leg(-1, E_S, E_S - 2.0, 0.5), rel=1e-12)


# ---------------------------------------------------------------- runner basket part
RUNNER = {'runner': {'dca_frac': 0.5}}


def test_runner_basket_part_floored_to_zero_still_switches_to_the_runner():
    """DCA q0 = 2.45 / 24.5 ATR = 0.1 (one step): the basket part 0.05 floors to 0 -> nothing closed, but the lot still
    becomes a runner (tp off, no more safety orders, stop to breakeven) - the engine sets these after _market_close
    whatever it sent. The later drop to 99 then stops the whole 0.1 at breakeven; no safety order fills at 99.02."""
    cnd = {12: (100.0, 101.5, 99.5, 101.3), 14: (101.3, 101.3, 99.0, 99.2)}
    tr, _ = run(book(cnd), copy.deepcopy(RUNNER), 0.0049, key='dca_dip')
    be = E_L * (1 + B.BE_BUF)
    assert len(tr) == 1 and tr.why[0] == 'stop' and tr.i_out[0] == 14
    assert tr.pnl[0] == pytest.approx(-0.1 * E_L * B.FEE + leg(1, E_L, be, 0.1), rel=1e-9)
    trl, _ = run(book(cnd), copy.deepcopy(RUNNER), 0.0049, key='dca_dip', rule=None)   # legacy: 0.05 banked at the tp
    ql = 500.0 * 0.0049 / 24.5
    assert trl.pnl[0] == pytest.approx(-ql * E_L * B.FEE + leg(1, E_L, E_L + 1.0, ql * 0.5) + leg(1, E_L, be, ql - ql * 0.5), rel=1e-9)


def test_runner_basket_part_floored_near_a_step():
    """q0 0.3: part 0.15 -> 0.1 banked at the basket tp, 0.2 stopped at breakeven."""
    cnd = {12: (100.0, 101.5, 99.5, 101.3), 14: (101.3, 101.3, 99.0, 99.2)}
    tr, _ = run(book(cnd), copy.deepcopy(RUNNER), 0.0147, key='dca_dip')
    be = E_L * (1 + B.BE_BUF)
    assert len(tr) == 1 and tr.why[0] == 'stop'
    assert tr.pnl[0] == pytest.approx(-0.3 * E_L * B.FEE + leg(1, E_L, E_L + 1.0, 0.1) + leg(1, E_L, be, 0.2), rel=1e-9)


# ---------------------------------------------------------------- full closes unchanged
@pytest.mark.parametrize('cnd,exit_at,why', [({12: (100.0, 100.0, 97.0, 97.5)}, None, 'stop'),
                                             ({12: (100.0, 101.0, 99.5, 100.8)}, 13, 'signal')])
def test_full_stop_and_full_close_identical_with_and_without_rules(cnd, exit_at, why):
    """No partial configured and q0 an exact step multiple: the rules run and the legacy run give the same trade."""
    a, ca = run(book(cnd, exit_at=exit_at), {'stop_atr': 2.0}, 0.0012)
    b, cb = run(book(cnd, exit_at=exit_at), {'stop_atr': 2.0}, 0.0012, rule=None)
    assert len(a) == len(b) == 1 and a.why[0] == b.why[0] == why
    assert a.pnl[0] == pytest.approx(b.pnl[0], rel=1e-12) and np.allclose(ca.values, cb.values, rtol=1e-12, atol=0)


def test_legacy_ladder_has_no_dust_rule_and_no_floor():
    """No rules (legacy): q0 0.05 (5 USDT, the legacy floor), level 60 % -> 0.03 closed, 0.02 (2 USDT, below 5) is
    kept and leaves at the signal exit - byte-for-byte the pre-P1-C backtest."""
    cnd = {12: (100.0, 102.5, 99.5, 102.3)}
    tr, _ = run(book(cnd, exit_at=13), LAD(0.6), 0.0002, rule=None)
    q = 500.0 * 0.0002 / 2.0
    assert len(tr) == 1 and tr.why[0] == 'signal'
    f = min(1.0, q * 0.6 / q)
    assert tr.pnl[0] == pytest.approx(-q * E_L * B.FEE + leg(1, E_L, E_L + 2.0, q * f) + leg(1, E_L, 102.3, q - q * f), rel=1e-12)


def test_symbol_missing_from_the_snapshot_keeps_the_legacy_partials():
    cnd = {12: (100.0, 102.5, 99.5, 102.3)}
    sl = [dict(key='ema_mom', share=1.0, risk=0.0012, max_pos=1, sides='long', mgmt=copy.deepcopy(TP1), symbols=[SYM])]
    a, _ = B.run(book(cnd, exit_at=13), sl, start=500.0, warmup=5, maint_margin=0, fund_per_bar=0,
                 exchange_rules={'OTHERUSDT': dict(RULE)})
    b, _ = B.run(book(cnd, exit_at=13), sl, start=500.0, warmup=5, maint_margin=0, fund_per_bar=0)
    pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------- parity with the engine
def old_rd(x, step):
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(x / step + 1e-9) * step, dec)


def old_engine_ladder_close(lot_qty, qty_max_f, m, rr):
    """engine.py before P1-C, verbatim: ladder quantity -> _market_close floor -> _apply_close remainder.
    -> (qty sent or None when it floored to 0, lot qty after)."""
    q = min(lot_qty, qty_max_f)
    rest = lot_qty - q
    if rest > 0 and (rest < rr['min_qty'] or rest * m < rr['min_notional']): q = lot_qty
    qty = old_rd(q, rr['step'])
    if qty <= 0: return None, lot_qty
    return qty, max(0.0, old_rd(lot_qty - qty, rr['step']))


def old_engine_partial_close(lot_qty, frac, step):
    qty = old_rd(lot_qty * frac, step)
    if qty <= 0: return None, lot_qty
    return qty, max(0.0, old_rd(lot_qty - qty, step))


def test_shared_helpers_equal_the_pre_change_engine_code_on_a_dense_grid():
    rng = random.Random(11)
    for _ in range(20000):
        step = rng.choice([1e-5, 0.001, 0.01, 0.1, 1.0, 10.0])
        rr = dict(step=step, min_qty=step * rng.choice([1, 1, 3]), min_notional=rng.choice([0.0, 5.0, 20.0, 100.0]))
        qty = rng.randint(1, 400) * step
        qty = old_rd(qty, step)
        m = rng.choice([0.05, 1.0, 37.0, 2500.0, 60000.0])
        want = rng.choice([qty * rng.random(), qty * rng.choice([0.25, 0.5, 0.6, 1.0, 1.5]), rng.randint(0, 50) * step])
        q = F.close_qty(F.ladder_qty(qty, want, m, rr), step)
        new = (None, qty) if q <= 0 else (q, F.remaining_qty(qty, q, step))
        assert new == old_engine_ladder_close(qty, want, m, rr), (qty, want, m, rr)
        frac = rng.choice([0.5, 0.33, 0.25, 0.75, 1.0, rng.random()])
        q = F.close_qty(qty * frac, step)
        new = (None, qty) if q <= 0 else (q, F.remaining_qty(qty, q, step))
        assert new == old_engine_partial_close(qty, frac, step), (qty, frac, step)


def test_engine_market_close_floors_and_skips_zero_through_the_fake_exchange():
    import test_safety as TS
    e, _ = TS.mk_engine()
    e.rules['ETHUSDT'] = dict(step=0.1, min_qty=0.1, tick=0.01, min_notional=5.0)
    lot = dict(symbol='ETHUSDT', side='LONG', sleeve='T', qty=0.3, avg=100.0)
    e.trade.pos[('ETHUSDT', 'LONG')] = 0.3; e.trade.mark['ETHUSDT'] = 102.0
    n = e.trade.calls.count('close')
    e._market_close(lot, 0.3 * 0.5, 'take_profit_1', 102.0)
    assert lot['qty'] == 0.2 and e.trade.calls.count('close') == n + 1
    assert e.trade.pos[('ETHUSDT', 'LONG')] == pytest.approx(0.2)
    assert e._market_close(lot, 0.05, 'take_profit_1', 102.0) == 0.0          # floors to 0: nothing sent
    assert lot['qty'] == 0.2 and e.trade.calls.count('close') == n + 1


@pytest.mark.parametrize('rule,seq', [
    (dict(step=0.1, min_qty=0.1, min_notional=5.0), [('close', 0.3), ('rest', 0.2), ('close', 0.2), ('rest', 0.0)]),
    (dict(step=0.1, min_qty=0.1, min_notional=25.0), [('close', 0.5), ('rest', 0.0)]),      # rest 0.2 is dust: whole lot
])
def test_engine_and_backtest_send_the_same_close_quantities(monkeypatch, rule, seq):
    """Same lot (0.5, step 0.1), same ladder (60 % at +1R, then 100 % at +2R), same rule: record every quantity the shared
    helpers hand back in the live engine (manage loop, FakeX) and in the backtester - identical sequences."""
    import test_safety as TS
    rec = []
    real_c, real_r = F.close_qty, F.remaining_qty
    monkeypatch.setattr(F, 'close_qty', lambda q, s: rec.append(('close', real_c(q, s))) or real_c(q, s))
    monkeypatch.setattr(F, 'remaining_qty', lambda q, c, s: rec.append(('rest', real_r(q, c, s))) or real_r(q, c, s))
    tps = [[1.0, 0.6], [2.0, 1.0]]
    # engine
    e, _ = TS.mk_engine()
    e.rules['BTCUSDT'] = dict(rule, tick=0.01)
    sl = dict(TS.SL, mgmt={'stop_atr': 2.0, 'tps': tps})
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', dict(TS.SG, close=100.0, atr=1.0), None, e.equity())
    k, lot = next(iter(e.state['lots'].items()))
    e.trade.pos[('BTCUSDT', 'LONG')] = lot['qty'] = lot['q0'] = lot['qty_max'] = 0.5
    e._replace_stop(lot)
    R = lot['R']
    rec.clear()
    e.trade.mark['BTCUSDT'] = lot['e0'] + 1.2 * R; e.manage(e.trade.marks())
    e.trade.mark['BTCUSDT'] = lot['e0'] + 2.2 * R; e.manage(e.trade.marks())
    eng = list(rec)
    assert k not in e.state['lots'] and eng
    # backtest: q0 0.5, level 1 at +1R (102.02), level 2 at +2R (104.02)
    rec.clear()
    run(book({12: (100.0, 102.5, 99.5, 102.3), 13: (102.3, 104.5, 102.1, 104.3)}), {'stop_atr': 2.0, 'tps': tps}, 0.002, rule=rule)
    bt = [x for x in rec]
    assert bt == eng == seq
