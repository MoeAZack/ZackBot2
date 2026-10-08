"""TNET-01: the classic -> algo stop route fallback (journal rule G6) in the ManagementDriver. Binance testnet refuses a
classic STOP_MARKET with -4120 (algo route required): the core must never see that refusal; the same protection goes
out as a NEW child intent on the algo route."""
import itertools
from decimal import Decimal as D

import pytest

from mg_factories import LONG, SHORT, T0, ZERO_COSTS, plan, px
from newcore.domain import Purpose, make_id
from newcore.management import Stage
from newcore.management import driver as DR
from newcore.ports.keys import client_id_for, derive_child_intent_id
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind

ACCT, LOT = make_id('acct', 31), make_id('lot', 32)
SIDES = (LONG, SHORT)
_n = itertools.count(1)


def boot(side):
    p = plan(side, costs=ZERO_COSTS)
    return p, DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0))


def ref(p, d):
    return OrderRef(symbol=p.symbol, client_id=d.client_id, route=d.route)


def refused(p, d, code=-4120, detail=None):
    return OrderOutcome(kind=OutcomeKind.REJECTED, ref=ref(p, d), observed_at_ms=T0, error_code=code, detail=detail)


def known(p, d):
    return OrderOutcome(kind=OutcomeKind.KNOWN, ref=ref(p, d), observed_at_ms=T0, status='NEW',
                        exchange_order_id=f'x{next(_n)}')


def stops(drv):
    return [d for d in drv.submits if d.purpose is Purpose.PROTECT]


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('code,detail', [(-4120, None), (-1116, None), (-1102, None), (-4136, None), (-1, 'algo_route')])
def test_classic_refused_goes_algo_then_confirmed_and_protected(side, code, detail):
    p, drv = boot(side)
    classic = stops(drv)[0]
    assert classic.route == 'classic'
    r = DR.on_outcome(drv.state, refused(p, classic, code, detail), submit=True)
    algo = stops(r)
    assert len(algo) == 1 and algo[0].route == 'algo'
    assert (algo[0].stop_price, algo[0].qty) == (classic.stop_price, classic.qty)
    assert algo[0].intent_id == derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 1)     # a NEW lineage child
    assert algo[0].client_id == client_id_for(algo[0].intent_id, 'algo')
    assert not r.state.pos.stop_locked and r.state.pos.stop is not None             # the core never saw a refusal
    assert ('route_fallback', classic.intent_id, algo[0].intent_id) in r.reconcile
    ok = DR.on_outcome(r.state, known(p, algo[0]), submit=True)
    assert DR.protected(ok.state) and ok.state.pos.stage is Stage.ACTIVE


@pytest.mark.parametrize('side', SIDES)
def test_algo_also_refused_takes_the_core_stop_failure_path(side):
    p, drv = boot(side)
    r = DR.on_outcome(drv.state, refused(p, stops(drv)[0]), submit=True)
    algo = stops(r)[0]
    r = DR.on_outcome(r.state, refused(p, algo, -2021), submit=True)
    closes = [d for d in r.submits if d.purpose is Purpose.CLOSE]
    assert len(closes) == 1 and closes[0].reason.value == 'exit.stop_failed' and r.state.pos.stop_locked
    assert not stops(r)                                             # no third route


@pytest.mark.parametrize('side', SIDES)
def test_an_unknown_classic_answer_is_resolved_before_any_fallback(side):
    p, drv = boot(side)
    classic = stops(drv)[0]
    lost = OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref(p, classic), observed_at_ms=T0)
    r = DR.on_outcome(drv.state, lost, submit=True)
    assert not stops(r)                                             # no algo while the classic may be live
    nf = OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref(p, classic), observed_at_ms=T0, error_code=-2013)
    r = DR.on_outcome(r.state, nf, submit=False)
    assert not stops(r)                                             # NOT_FOUND alone resolves nothing
    r = DR.route_fallback(r.state, classic.intent_id)               # the runner resolved it as refused (G6)
    algo = stops(r)
    assert len(algo) == 1 and algo[0].route == 'algo'
    again = DR.route_fallback(r.state, classic.intent_id)           # idempotent: never two algo stops
    assert not stops(again) and ('route_fallback_refused', classic.intent_id) in again.reconcile
    live = [b for b in r.state.bindings if b.leg.value == 'stop']
    assert [b.route for b in live] == ['algo']                     # never both routes live


@pytest.mark.parametrize('side', SIDES)
def test_a_working_classic_stop_is_never_fallen_back(side):
    p, drv = boot(side)
    classic = stops(drv)[0]
    ds = DR.on_outcome(drv.state, known(p, classic), submit=True).state
    r = DR.route_fallback(ds, classic.intent_id)
    assert not stops(r) and ('route_fallback_refused', classic.intent_id) in r.reconcile


@pytest.mark.parametrize('side', SIDES)
def test_restart_between_the_routes_folds_to_the_same_state(side):
    p, drv = boot(side)
    classic = stops(drv)[0]
    log = [('outcome', refused(p, classic), True)]
    r = DR.on_outcome(drv.state, refused(p, classic), submit=True)
    replay = DR.fold(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0), events=log)
    assert replay[-1].state == r.state and stops(replay[-1]) == stops(r)        # crash right after the fallback
    algo = stops(r)[0]
    log.append(('outcome', known(p, algo), True))
    live = DR.on_outcome(r.state, log[-1][1], submit=True)
    replay = DR.fold(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0), events=log)
    assert replay[-1].state == live.state and DR.protected(live.state)
    lost = OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref(p, classic), observed_at_ms=T0)
    p2, drv2 = boot(side)
    log2 = [('outcome', lost, True), ('route_fallback', classic.intent_id)]
    replay2 = DR.fold(p2, account_id=ACCT, lot_id=LOT, entry_fee=D(0), events=log2)
    assert [d.route for d in stops(replay2[-1])] == ['algo']
    _ = px


def _resize_after_fallback(side, policy):
    """Classic refused -> algo confirmed -> TP1 fills -> the core resizes the stop: the drafts of that resize."""
    p = plan(side, costs=ZERO_COSTS, tp1_frac='0.5', tp1_off='1', tp2_off='2')
    drv = DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0), stop_route_policy=policy)
    r = DR.on_outcome(drv.state, refused(p, stops(drv)[0]), submit=True)
    algo = stops(r)[0]
    ds = DR.on_outcome(r.state, known(p, algo), submit=True).state
    r = DR.on_mark(ds, ds.pos.tp1.price)                              # TP1 fires (a market reduce)
    tp = next(d for d in r.submits if d.purpose is Purpose.REDUCE)
    fin = OrderOutcome(kind=OutcomeKind.FINAL, ref=ref(p, tp), observed_at_ms=T0, status='FILLED',
                       exchange_order_id='x77', executed_qty=tp.qty, avg_price=ds.pos.tp1.price)
    r = DR.on_outcome(r.state, fin, submit=True)
    from newcore.ports.venue import VenueFill
    f = VenueFill(trade_id='t77', exchange_order_id='x77', symbol=p.symbol, position_side=p.side.value, qty=tp.qty,
                  price=ds.pos.tp1.price, fee=D(0), fee_asset='USDT', realized_pnl=D(0), maker=False, at_ms=T0)
    return p, ds, DR.on_fills(r.state, (f,))


@pytest.mark.parametrize('side', SIDES)
def test_sticky_policy_sends_every_later_stop_algo_directly(side):
    """stop_route_policy='sticky_after_refusal' (only if Codex amends G6): after one refusal a later replacement
    never tries classic again."""
    _, ds, r = _resize_after_fallback(side, 'sticky_after_refusal')
    assert ds.stop_route == 'algo' and ds.stop_route_policy == 'sticky_after_refusal'
    resized = stops(r)
    assert resized and all(d.route == 'algo' for d in resized)


@pytest.mark.parametrize('side', SIDES)
def test_per_attempt_policy_is_the_default_and_tries_classic_first_again(side):
    """The default (journal rule G6): every NEW stop is tried classic first; algo only after THAT attempt is refused."""
    p, ds, r = _resize_after_fallback(side, 'per_attempt')
    assert ds.stop_route == 'classic' and ds.stop_route_policy == 'per_attempt'
    (resized,) = stops(r)
    assert resized.route == 'classic'
    r2 = DR.on_outcome(r.state, refused(p, resized), submit=True)
    (algo,) = stops(r2)
    assert algo.route == 'algo' and (algo.stop_price, algo.qty) == (resized.stop_price, resized.qty)
    assert DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0)).state.stop_route_policy == 'per_attempt'


def test_unknown_policy_is_refused():
    p = plan(LONG, costs=ZERO_COSTS)
    with pytest.raises(Exception):
        DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=D(0), stop_route_policy='always_algo')
