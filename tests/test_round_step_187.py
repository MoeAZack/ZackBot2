"""ROUND-STEP-01 (#187, Codex 6385078314). feasibility.round_step - the engine's _rd - floored on-grid quantities one
step short: its absolute +1e-9 guard is below float spacing at ~1e7+ steps (254.37124 at step 1e-5 is
25437123.999999996 steps). Float sums/differences of on-grid lot quantities had the same defect one level up
(2345.61989 - 1172.80994 = 1172.8099499999998). Market exits, TP remainders and adds then left a real one-step position
that the replay exchange keeps, and the execution packet saw two engine trades as one (SUI/range, XRP/exp p3diag1).
The fix floors on DECIMAL values and never rounds up."""
import math
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tests')]
import feasibility as F                                   # noqa: E402

STEPS = [1e-8, 1e-6, 1e-5, 1e-4, 1e-3, 0.01, 0.1, 1.0, 10.0, 0.5, 0.005, 0.25, 2.5]


def _dec(step):
    return max(0, -int(math.floor(math.log10(step)))) + 2 if step < 1 else 1


def _grid(k, step):
    return round(k * step, _dec(step))


# ------------------------------------------------------------------ the real artifacts
@pytest.mark.parametrize('x', [254.37124, 196.44617, 227.87239, 188.69615, 299.27826])      # SUI/range p3diag1
def test_the_real_sui_quantities_round_to_themselves(x):
    assert F.round_step(x, 1e-5) == x


def test_the_real_xrp_tp_remainder_is_exact():                  # XRP/exp p3diag1: open, TP1 banks the floored half
    total = 2345.61989
    half = F.round_step(total / 2, 1e-5)
    assert half == 1172.80994
    assert F.step_add(total, -half, 1e-5) == 1172.80995           # float difference is 1172.8099499999998
    assert F.step_add(half, 1172.80995, 1e-5) == total


# ------------------------------------------------------------------ properties
@pytest.mark.parametrize('step', STEPS)
def test_decimal_grid_values_stay_exact_including_large_tick_counts(step):
    rnd = random.Random(187)
    top = min(10 ** 12, int(1e14 * step) if step < 1 else 10 ** 12)
    for _ in range(5000):
        x = _grid(rnd.randint(1, max(2, top)), step)
        assert F.round_step(x, step) == x, (x, step)


@pytest.mark.parametrize('step', STEPS)
def test_below_grid_values_floor_and_never_exceed_the_input(step):
    rnd = random.Random(7)
    for _ in range(5000):
        k = rnd.randint(1, 10 ** 6)
        for frac in (0.5, 0.999, 0.001):
            x = (k - frac) * step                      # a fraction of a step below the grid point k
            r = F.round_step(x, step)
            assert r <= x and r == pytest.approx((k - 1) * step, rel=1e-12, abs=step * 1e-9), (x, step)


@pytest.mark.parametrize('step', STEPS)
def test_a_float_just_below_the_grid_floors_to_the_previous_step(step):
    # representation policy: the decimal value of nextafter(grid, -inf) is below the grid, so it floors - never up
    for k in (3, 1234, 25437124):
        g = _grid(k, step)
        r = F.round_step(math.nextafter(g, -math.inf), step)
        assert r < g and r == pytest.approx(g - step, rel=1e-12)


@pytest.mark.parametrize('step', STEPS)
def test_monotone_and_idempotent(step):
    rnd = random.Random(11)
    xs = sorted(rnd.uniform(0, 10 ** 4) * step for _ in range(3000))
    rs = [F.round_step(x, step) for x in xs]
    assert rs == sorted(rs)
    assert all(F.round_step(r, step) == r for r in rs)


@pytest.mark.parametrize('step', [1e-5, 1e-3, 0.1, 1.0, 0.005])
def test_step_add_of_on_grid_quantities_is_exact(step):
    rnd = random.Random(3)
    for _ in range(20000):
        a, b = rnd.randint(0, 10 ** 9), rnd.randint(0, 10 ** 9)
        lo, hi = min(a, b), max(a, b)
        assert F.step_add(_grid(hi, step), -_grid(lo, step), step) == _grid(hi - lo, step)
        assert F.step_add(_grid(a, step), _grid(b, step), step) == _grid(a + b, step)


def test_step_add_never_rounds_a_genuinely_below_grid_result_up():
    assert F.step_add(254.37124, -0.000001, 1e-5) == 254.37123
    assert F.step_add(1.0, -0.5000001, 0.5) == 0.0


# ------------------------------------------------------------------ engine lifecycle on a 1e-5-step fake exchange
@pytest.fixture
def eng():
    import engine as E
    import test_safety as TS

    class Fine(TS.FakeX):                                  # positions held as exact decimals, as Binance does
        def open(self, s, ps, q):
            r = super().open(s, ps, q); self.pos[(s, ps)] = F.qty_sum(self.pos[(s, ps)]); return r

        def close(self, s, ps, q):
            self._f('close')
            self.pos[(s, ps)] = F.qty_sum(self.pos.get((s, ps), 0), -float(q)); return {'avgPrice': str(self.mark[s])}

        def exchange_info(self):
            return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
                {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.00001', 'minQty': '0.00001'},
                {'filterType': 'PRICE_FILTER', 'tickSize': '0.0001'},
                {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]} for s in self.mark]}
    saved = TS.FakeX
    TS.FakeX = Fine
    try:
        e, _ = TS.mk_engine()
    finally:
        TS.FakeX = saved
    k = TS.opened(e)
    return E, e, k


def _hold(e, k, qty):
    lot = e.state['lots'][k]
    lot['qty'] = lot['qty_max'] = qty
    e.trade.pos[(lot['symbol'], lot['side'])] = qty
    return lot


def test_a_market_close_sends_the_whole_on_grid_lot(eng):
    _, e, k = eng
    lot = _hold(e, k, 254.37124)
    e.close_lot(k, 'exit_signal')
    assert abs(e.trade.pos[(lot['symbol'], lot['side'])]) < 1e-9          # no one-step remnant on the exchange
    assert k not in e.state['lots']


def test_a_tp1_remainder_then_its_stop_close_leave_nothing(eng):
    _, e, k = eng
    lot = _hold(e, k, 2345.61989)
    half = F.round_step(lot['qty'] / 2, 1e-5)
    e._market_close(lot, half, 'take_profit_1')
    assert lot['qty'] == 1172.80995 and e.trade.pos[(lot['symbol'], lot['side'])] == pytest.approx(1172.80995, abs=1e-9)
    e.close_lot(k, 'stop')
    assert abs(e.trade.pos[(lot['symbol'], lot['side'])]) < 1e-9


@pytest.mark.parametrize('step', STEPS)
def test_ceil_step_covers_the_input_and_is_idempotent_on_the_grid(step):
    rnd = random.Random(5)
    for _ in range(3000):
        x = rnd.uniform(0, 10 ** 4) * step
        c = F.ceil_step(x, step)
        assert c >= x - 1e-12 and F.ceil_step(c, step) == c and c - x < step * (1 + 1e-9)
    for k in (1, 77, 25437124):
        assert F.ceil_step(_grid(k, step), step) == _grid(k, step)


def test_replay_exchange_positions_stay_exact_after_partial_closes():
    assert F.qty_sum(2345.61989, -1172.80994) == 1172.80995
    assert F.qty_sum(1172.80995, -1172.80995) == 0.0


# ------------------------------------------------------------------ callers of "one more step" (Codex 6385148287 item 2)
# Evidence: with the exact floor, round_step(q + step, step) returns q itself for 3-21% of on-grid q (the float sum
# lands a hair below the grid), so every "add one step" caller must use the exact step_add.
@pytest.mark.parametrize('q,step', [(17.02773, 1e-5), (382.986, 0.001), (75183.73, 0.01), (0.08312022, 1e-8),
                                    (8620.64, 0.005)])
def test_one_more_step_always_advances_by_exactly_one_step(q, step):
    assert F.step_add(q, step, step) == _grid(round(q / step) + 1, step)


def test_short_tp_price_is_on_the_grid_at_or_above_its_level():
    import engine as E
    from types import SimpleNamespace
    me = SimpleNamespace(rules={'X': dict(tick=0.01)}, _rd=E.Engine._rd)
    lot = dict(symbol='X', side='SHORT', e0=75183.735 + 2.0, R=1.0, mgmt=dict(tp_r=2.0))   # level 75183.735
    px = E.Engine._tp_price(me, lot)
    assert px >= 75183.735 and px == 75183.74


# ------------------------------------------------------------------ float-accumulated counters (Cowork 6385494817, Codex 6385527903)
PARTS = {1e-5: [6.17502, 42.68018, 42.51135, 40.66615], 0.01: [35.23, 8.12, 8.76, 26.31]}   # float sums land BELOW the grid


def _exact(xs, step):
    return _grid(round(sum(round(x / step) for x in xs)), step)


@pytest.mark.parametrize('step', [1e-5, 0.01])
def test_grid_qty_normalises_only_binary_noise(step):
    acc = 0.0
    for x in PARTS[step]:
        acc += x                                          # the old engine counter
    assert F.grid_qty(acc, step) == _exact(PARTS[step], step)
    with pytest.raises(ValueError, match='not on the'):
        F.grid_qty(_exact(PARTS[step], step) + step * 0.001, step)      # a fraction of a step is not noise: fail closed


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_non_finite_quantities_raise_everywhere(bad):
    for call in (lambda: F.round_step(bad, 1e-5), lambda: F.step_add(1.0, bad, 1e-5), lambda: F.qty_sum(1.0, bad),
                 lambda: F.grid_qty(bad, 1e-5), lambda: F.round_step(1.0, bad)):
        with pytest.raises(ValueError):
            call()
    for step in (0.0, -1e-5):
        with pytest.raises(ValueError, match='invalid step'):
            F.round_step(1.0, step)


def _step(e, step):
    for r in e.rules.values():
        r['step'] = step


@pytest.mark.parametrize('step', [1e-5, 0.01])
def test_maker_filled_counter_and_remainder_stay_exact_over_partial_fills(eng, step):
    _, e, _k = eng
    _step(e, step)
    total = _exact(PARTS[step], step) + 5 * step
    rec = dict(symbol='BTCUSDT', side='LONG', price=100.0, qty=total, filled=0.0, cost=0.0)
    for x in PARTS[step]:
        e._maker_done_order(rec, {'executedQty': repr(x), 'avgPrice': '100'})
    assert rec['filled'] == _exact(PARTS[step], step)
    assert F.step_add(rec['qty'], -F.grid_qty(rec['filled'], step), step) == _grid(5, step)


@pytest.mark.parametrize('step', [1e-5, 0.01])
@pytest.mark.parametrize('restart', [False, True])
def test_resting_tp_partials_book_exact_deltas(eng, monkeypatch, step, restart):
    _, e, k = eng
    _step(e, step)
    lot = _hold(e, k, _exact(PARTS[step], step) + 10 * step)
    start = lot['qty']
    lot.update(tp_id='o:9', tp_px=101.0, tp_qty=start, tp_done=0.0)
    cum = 0.0
    for i, x in enumerate(PARTS[step]):
        cum = F.qty_sum(cum, x)                         # the exchange reports the cumulative executed qty as a decimal
        if restart and i == 2:                          # a persisted pre-fix counter, one ulp of float noise high
            lot['tp_done'] = math.nextafter(_exact(PARTS[step][:2], step), math.inf)
        monkeypatch.setattr(e, '_tp_record', lambda l, c=cum: dict(state='ok', exec=c, px=101.0, final=False))
        e._tp_settle(k, {'o:9'}, replace=False)
        assert lot['tp_done'] == _exact(PARTS[step][:i + 1], step), i               # exact after EVERY partial
        assert lot['qty'] == F.step_add(start, -_exact(PARTS[step][:i + 1], step), step), i
    assert lot['tp_done'] == _exact(PARTS[step], step)
    assert lot['qty'] == F.step_add(start, -_exact(PARTS[step], step), step) == _grid(10, step)


@pytest.mark.parametrize('step', [1e-5, 0.01])
def test_risk_trim_counter_stays_exact_over_partials_and_restart(eng, step):
    _, e, k = eng
    _step(e, step)
    lot = _hold(e, k, _exact(PARTS[step], step) + 3 * step)
    noisy = 0.0
    for y in PARTS[step][:2]:
        noisy += y                                      # persisted pre-fix float counter
    d = dict(trimmed=noisy, measured_px=100.0, fee_unit=None, planned=1.0)
    before = lot['qty']
    lot['qty'] = F.step_add(before, -PARTS[step][2], step)
    e._risk_fill_done(lot, d, before)
    assert d['trimmed'] == _exact(PARTS[step][:3], step)
    trim_qty = _exact(PARTS[step], step)
    assert F.step_add(trim_qty, -F.grid_qty(d['trimmed'], step), step) == _grid(round(PARTS[step][3] / step), step)


@pytest.mark.parametrize('x,step', [(1400.00001, 1e-5), (140000.001, 1e-3), (99999999.99, 0.01)])
def test_grid_qty_accepts_exact_on_grid_values_of_any_size(x, step):
    assert F.grid_qty(x, step) == x                       # Cowork 6385780710: the noise cap must not reject exact values


# ------------------------------------------------------------------ Cowork 6385840296 / Codex 6385852917
@pytest.mark.parametrize('px', [719.42446043, 809.06148867, 561.16722783])
def test_required_qty_terminates_and_meets_the_minimums(px):
    import threading
    rule = dict(step=1e-5, min_qty=1e-5, min_notional=5.0)
    out = []
    t = threading.Thread(target=lambda: out.append(F.required_qty(px, rule)), daemon=True)
    t.start(); t.join(5.0)
    assert out, f'required_qty hung at px={px}'
    q = out[0]
    assert q >= rule['min_qty'] and q * px >= rule['min_notional'] and F.round_step(q, 1e-5) == q
    assert (q - 1e-5) * px < rule['min_notional'] + 1e-9                  # the smallest such quantity


def test_required_qty_fails_closed_instead_of_spinning(monkeypatch):
    monkeypatch.setattr(F, 'step_add', lambda q, s, st: q)                # a step that never advances
    with pytest.raises(ValueError, match='did not converge'):
        F.required_qty(719.42446043, dict(step=1e-5, min_qty=1e-5, min_notional=5.0))


def test_apply_add_sums_lot_quantities_exactly(eng):
    _, e, k = eng
    lot = _hold(e, k, 93.12374)
    e._apply_add(lot, 25.04914, 100.0, 'add')                             # float sum is 118.17287999999999
    assert lot['qty'] == 118.17288


SITES = [                                    # Codex 6385852917 item 3: required-site assertions for paths without a cheap hook
    ('engine', 'Engine._risk_fill_run', "F.step_add(d['trim_qty'], -F.grid_qty(d.get('trimmed', 0.0), r['step']), r['step'])"),
    ('engine', 'Engine._risk_fill_run', "F.grid_qty(F.qty_sum(d.get('trimmed', 0.0), d['qty_before'], -lot['qty'])"),
    ('engine', 'Engine.reconcile', "newqs[k] = F.step_add(newqs[k], step, step)"),
    ('engine', 'Engine._maker_place', "rem = F.step_add(rec['qty'], -F.grid_qty(rec['filled'], r['step']), r['step'])"),
    ('engine', 'Engine._maker_poll', "rem = F.step_add(rec['qty'], -F.grid_qty(rec['filled'], r['step']), r['step'])"),
    ('replay', 'run_replay', "q = F.qty_sum(*(l['q'] for l in ls))"),
    ('replay', 'run_replay', "l['q'] = F.qty_sum(l['q'], -q)"),
]


@pytest.mark.parametrize('mod,fn,code', SITES, ids=[f'{m}.{f}:{i}' for i, (m, f, _c) in enumerate(SITES)])
def test_required_exact_arithmetic_sites(mod, fn, code):
    import importlib
    import inspect
    obj = importlib.import_module(mod)
    for part in fn.split('.'):
        obj = getattr(obj, part)
    assert code in inspect.getsource(obj)
