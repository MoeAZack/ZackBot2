"""Engine lot quantity vs exchange position with a coarse step (2026-10-07).

reconcile() and the leverage preflight used to accept a gap of step * (lots + 1) between the engine's lots and Binance's
position. Both are whole multiples of the step, so with a lot of only a few steps (DOGE / 1000PEPE at step 1, or a 0.002 BTC
lot at step 0.001) an exchange stop-out (0 held vs 2 expected) sat "inside the tolerance": the stop was never booked, the
lot stayed open in the engine and every later exit failed. Found by the BT02 replay with step-1 rules."""
import os, sys, time
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_safety import mk_engine, opened, ambiguous_once                # noqa: E402
import binance_client as BC                                              # noqa: E402
from test_leverage_auto import refusing, add_lot, check                  # noqa: E402
from test_causality import synth, SYMS                                   # noqa: E402
import engine as E                                                       # noqa: E402
import replay                                                            # noqa: E402


def small_lot(e, q):
    """A real opened BTC lot, resized to q (the fake's step is 0.001) on both the engine and the exchange."""
    k = opened(e); l = e.state['lots'][k]
    l['qty'] = l['q0'] = l['qty_max'] = q; e.trade.pos[('BTCUSDT', 'LONG')] = q
    e.trade.stops[l['stop_id']] = ('BTCUSDT', 'LONG', q, l['stop'])
    return k, l


@pytest.mark.parametrize('q', [0.001, 0.002, 0.005])
def test_stop_out_of_a_few_step_lot_is_booked(q):
    e, _ = mk_engine(); k, l = small_lot(e, q)
    e.trade.pos[('BTCUSDT', 'LONG')] = 0.0; e.trade.stops.pop(l['stop_id'])        # the exchange stop filled
    e.reconcile(500)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'stop'


def test_one_step_shrink_with_the_stop_still_open_is_resized():
    e, _ = mk_engine(); k, l = small_lot(e, 0.003)
    l['last_order_t'] = 0
    e.trade.pos[('BTCUSDT', 'LONG')] = 0.002                                    # one step gone, the stop still open
    e.reconcile(500); assert l['qty'] == 0.003                                  # one reading could be lag
    e.reconcile(500); assert e.state['lots'][k]['qty'] == 0.002


def test_float_noise_below_a_step_is_not_a_mismatch():
    e, _ = mk_engine(); k, l = small_lot(e, 0.002); l['last_order_t'] = 0
    e.trade.pos[('BTCUSDT', 'LONG')] = 0.0020000000000000005
    errs = []; e.err = lambda msg, key=None: errs.append(msg)
    e.reconcile(500); e.reconcile(500)
    assert not errs, errs                                       # no resize, no untracked alarm
    assert e.state['lots'][k]['qty'] == 0.002 and not e.untracked


def test_float_noise_below_the_expected_size_is_not_a_mismatch():
    e, _ = mk_engine(); k, l = small_lot(e, 0.002); l['last_order_t'] = 0
    e.trade.pos[('BTCUSDT', 'LONG')] = 0.0019999999999999996
    errs = []; e.err = lambda msg, key=None: errs.append(msg)
    e.reconcile(500); e.reconcile(500)
    assert not errs, errs                                       # no resize, no untracked alarm
    assert e.state['lots'][k]['qty'] == 0.002 and not e.history


# ---- adversarial review (2026-10-07): what the old multi-step tolerance was also hiding
def with_dust(e, k):
    e.trade.pos[('BTCUSDT', 'LONG')] += 0.001                     # one step of dust (0.1 USDT, below the 5 USDT minimum)
    e.reconcile(500); e.reconcile(500)
    assert k in e.state['lots'] and not e.untracked             # reconcile tolerates dust silently


@pytest.mark.parametrize('mark_known', [True, False])
def test_lost_close_answer_with_dust_on_the_exchange_is_booked_as_the_exit(mark_known):
    e, _ = mk_engine(); k = opened(e); with_dust(e, k)
    e.trade.mark['BTCUSDT'] = 110.0
    ambiguous_once(e, 'close')
    with pytest.raises(BC.AmbiguousOrder): e.close_lot(k, 'exit_signal', 110.0)   # closes lot + dust; the answer is lost
    if not mark_known: e.marks = {}                             # no price: the dust size is unknown, the empty position decides
    e.reconcile(500)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'exit_signal' and e.history[-1]['pnl'] > 0


def test_lost_add_answer_with_dust_on_the_exchange_is_booked():
    e, _ = mk_engine(); k = opened(e); l = e.state['lots'][k]; q0 = l['qty']; with_dust(e, k)
    ambiguous_once(e, 'open')
    with pytest.raises(BC.AmbiguousOrder): e._add_qty(l, q0, 100.0, 'pyramid_add')
    e.reconcile(500); e.reconcile(500)
    assert not l.get('pending') and l['qty'] == 2 * q0 and not e.untracked


def test_lost_close_that_never_filled_with_dust_keeps_the_lot():
    e, _ = mk_engine(); k = opened(e); l = e.state['lots'][k]; q0 = l['qty']; with_dust(e, k)
    ambiguous_once(e, 'close', fill=False)
    with pytest.raises(BC.AmbiguousOrder): e.close_lot(k, 'exit_signal', 100.0)
    l['pending']['t'] = time.time() - 30
    e.reconcile(500)
    assert k in e.state['lots'] and not l.get('pending') and l['qty'] == q0 and not e.history


@pytest.mark.parametrize('qtys, have', [([0.007] * 4, 0.025), ([0.003, 0.003], 0.005), ([0.005, 0.002, 0.004], 0.009)])
def test_resync_of_several_lots_adds_up_to_the_exchange(qtys, have):
    e, _ = mk_engine(); e.trade.mark['BTCUSDT'] = 60000.0; e.marks = e.trade.marks()
    ks = [add_lot(e, sym='BTCUSDT', qty=q, avg=60000.0, stop=59000.0)[0] for q in qtys]
    for k in ks: e.state['lots'][k]['last_order_t'] = 0
    e.trade.pos[('BTCUSDT', 'LONG')] = have                     # steps left the position, every stop still open
    for _ in range(3): e.reconcile(500)
    left = [e.state['lots'][k]['qty'] for k in ks if k in e.state['lots']]
    assert round(sum(left), 9) == have and not e.untracked, (left, e.untracked)


def test_preflight_tolerates_dust_like_reconcile():
    e = refusing(); add_lot(e, sym='ETHUSDT', qty=10.0, on_exchange=10.001)     # one dust step above a lot
    assert check(e)[0], check(e)[1]
    e = refusing(); e.trade.pos[('ETHUSDT', 'LONG')] = 0.001                    # orphan dust, no lot
    assert check(e)[0], check(e)[1]


def test_preflight_rejects_a_stop_one_step_short():
    e = refusing(); k, l = add_lot(e, sym='ETHUSDT', qty=0.002, avg=50.0, stop=45.0)
    s_ = e.trade.stops[l['stop_id']]; e.trade.stops[l['stop_id']] = (s_[0], s_[1], 0.001, s_[3])
    ok, why, n = check(e); assert not ok and n['check'] == 'stops', why
    e = refusing(); k1, l1 = add_lot(e, sym='ETHUSDT', qty=0.001, avg=50.0, stop=45.0)
    k2, l2 = add_lot(e, sym='ETHUSDT', qty=0.001, avg=50.0, stop=45.0, with_stop=False); l2['stop_id'] = l1['stop_id']
    ok, why, n = check(e); assert not ok and n['check'] == 'stops', why


@pytest.mark.parametrize('on_exchange', [0.0, 0.001])
def test_preflight_rejects_a_one_step_gap_on_a_small_lot(on_exchange):
    e = refusing()
    add_lot(e, qty=0.002, on_exchange=on_exchange)
    ok, why, n = check(e)
    assert not ok and n['check'] == 'reconcile', why


@pytest.mark.parametrize('on_exchange', [0.0, 1.0, 3.0])
def test_preflight_rejects_a_one_step_gap_on_a_step_1_coin(on_exchange):
    e = refusing(); e.rules['ETHUSDT'].update(step=1.0, min_qty=1.0)      # one step = 50 USDT: never dust
    add_lot(e, qty=2.0, on_exchange=on_exchange)
    ok, why, n = check(e)
    assert not ok and n['check'] == 'reconcile', why


@pytest.mark.slow
def test_replay_with_step_1_rules_keeps_engine_and_exchange_in_step(monkeypatch):
    """The original finding: partial TP + breakeven on step-1 / min-qty-1 rules. Before the fix: 0 engine trades vs 10
    backtest trades, 3 position mismatches and hundreds of 'exit failed' lines."""
    orig = E.Engine.connect
    def connect(self):
        orig(self)
        for s in SYMS: self.rules[s] = dict(step=1.0, min_qty=1.0, min_notional=5.0, tick=0.001)
    monkeypatch.setattr(E.Engine, 'connect', connect)
    errs = []
    monkeypatch.setattr(E.Engine, 'err', lambda self, msg, key=None: errs.append(msg))
    r = replay.run_replay(synth(n=520, seed=23), [E.sleeve('Q', 'squeeze_tp', 1.0, .02, 3, 'core8', sides='both')], 260, steps=6)
    assert r['mismatch'] == {} and r['metrics']['trades_engine'] > 0, (r['mismatch'], r['metrics'])
    assert not [m for m in errs if 'failed' in m], errs[:3]
    assert r['metrics']['stops'] == r['metrics']['lots']
