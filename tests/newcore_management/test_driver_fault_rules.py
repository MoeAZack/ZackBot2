"""Minimal repros of every failure the driver fault fuzz found (test_driver_faults.py), each pinned to the rule that fixed
it. Core-level rules are built by hand; the driver-level ones are the exact fuzz seeds that first exposed them (each
re-runs the whole injected scenario and its invariants)."""
from decimal import Decimal as D

import pytest

import test_driver_faults as F
from mg_factories import LONG, SHORT, ZERO_COSTS, plan, px
from newcore.domain import DomainError, ReasonCode as R
from newcore.management import ActionKind as AK, Cancelled, ConfirmedFill, Leg, Rejected, initial_state, step

SIDES = (LONG, SHORT)
_n = [0]


def fill(leg, qty, price):
    _n[0] += 1
    return ConfirmedFill(fill_id=f'r{_n[0]}', leg=leg, qty=D(qty), price=D(price), fee=D(0))


def start(p):
    return step(p, initial_state(p, D(0))).state


# ------------------------------------------------------------------------------------------- core racing on shrink
@pytest.mark.parametrize('side', SIDES)
def test_a_shrunk_target_keeps_the_old_size_racing_until_confirmed(side):
    """seed 7: TP1 fired as a market order for 2.5; a TP2 fill shrinks the position, so the core shrinks TP1 - the
    in-flight TP1 market still executes its full size and must be booked, not refused."""
    p = plan(side, tp1_frac='0.5', tp1_off='1', tp2_off='2', costs=ZERO_COSTS)
    st = start(p)
    r = step(p, st, (fill(Leg.TP2, '2.5', st.tp2.price),))
    assert r.state.tp1.qty == D('1.25') and r.state.racing_qty(Leg.TP1) == D('1.25')   # 0.5 x 2.5; 2.5 - 1.25 races
    r = step(p, r.state, (fill(Leg.TP1, '2.5', st.tp1.price),))
    assert r.state.qty == 0
    with pytest.raises(DomainError):                                           # never beyond order + racing
        step(p, step(p, st, (fill(Leg.TP2, '2.5', st.tp2.price),)).state, (fill(Leg.TP1, '2.6', st.tp1.price),))


@pytest.mark.parametrize('side', SIDES)
def test_a_replaced_stop_keeps_the_old_stop_racing_and_cancelled_clears_it(side):
    """seeds 2116 / 2366: the old stop stays live until its replacement is confirmed, so BOTH may execute (each in
    several trades); every trade is booked, bounded by the position, until the venue confirms the old one is gone."""
    p = plan(side, tp1_frac='0.5', tp1_off='1', tp2_off='2', costs=ZERO_COSTS)
    st = start(p)
    st = step(p, st, (fill(Leg.TP1, '2.5', st.tp1.price),)).state            # stop 5 -> 2.5 (old 5 still live)
    assert st.racing_qty(Leg.STOP) == D(5)
    ok = step(p, st, (fill(Leg.STOP, '1', st.stop.price), fill(Leg.STOP, '1.5', st.stop.price)))
    assert ok.state.qty == 0
    with pytest.raises(DomainError):
        step(p, st, (fill(Leg.STOP, '3', st.stop.price),))                     # never beyond the position
    gone = step(p, st, (Cancelled(leg=Leg.STOP),)).state
    assert gone.racing_qty(Leg.STOP) == 0


# ------------------------------------------------------------------------------------------- core close retries
@pytest.mark.parametrize('side', SIDES)
def test_a_short_partial_reduce_is_retried_as_a_reduce_of_the_rest(side):
    """seed 398: a stop replacement is refused after an add, the core flattens the add with a REDUCE; that reduce
    fills short - the retry is a REDUCE of the rest, never a CLOSE of the whole position."""
    p = plan(side, add='99.02', cap='20', costs=ZERO_COSTS, tp2_off='3')
    st = start(p)
    st = step(p, st, (fill(Leg.ADD, '5', px(side, '99.02')),)).state
    r = step(p, st, (Rejected(leg=Leg.STOP),))
    red = [a for a in r.actions if a.kind is AK.REDUCE]
    assert red and red[0].qty == D(5)
    st = step(p, r.state, (fill(Leg.CLOSE, '2', px(side, '99')),)).state       # 2 of 5 executed, the rest expired
    r = step(p, st, (Rejected(leg=Leg.CLOSE),))
    again = [a for a in r.actions if a.kind in (AK.REDUCE, AK.CLOSE)]
    assert len(again) == 1 and again[0].kind is AK.REDUCE and again[0].qty == D(3)
    assert again[0].reason is R.EXIT_STOP_FAILED


@pytest.mark.parametrize('side', SIDES)
def test_a_close_fill_of_an_earlier_request_is_booked_while_a_close_is_requested(side):
    """seed 464: closes were re-requested; a fill of the earlier, larger close request arrives - booked (bounded by
    the position); with NO close requested at all it is still refused."""
    p = plan(side, add='99.02', cap='20', costs=ZERO_COSTS, tp2_off='3')
    st = start(p)
    st = step(p, st, (fill(Leg.ADD, '5', px(side, '99.02')),)).state
    st = step(p, st, (Rejected(leg=Leg.STOP),)).state                          # REDUCE 5 requested
    st = step(p, st, (fill(Leg.CLOSE, '3', px(side, '99')),)).state           # 2 still requested
    assert st.closing == D(2) and st.qty == D(7)
    late = step(p, st, (fill(Leg.CLOSE, '3', px(side, '99')),))                 # an earlier request's 3 > the 2 asked
    assert late.state.qty == D(4) and late.state.closing == 0
    with pytest.raises(DomainError):
        step(p, start(p), (fill(Leg.CLOSE, '1', px(side, '100')),))             # no close requested at all
    with pytest.raises(DomainError):
        step(p, st, (fill(Leg.CLOSE, '8', px(side, '99')),))                    # never beyond the position


# ------------------------------------------------------------------------------------------- driver scenarios
# seed -> the rule it exposed (fixed in newcore/management/driver.py unless noted)
FUZZ_SEEDS = {
    1: 'confirmed_coverage counts what a FINAL stop executed and is not booked yet',
    7: 'core: a shrunk target keeps the old size racing (the fired market fills its full size)',
    25: 'a refused stop replacement is forwarded only while the core still requests that stop',
    27: 'a reduce-only close that found NOTHING (EXPIRED, 0) is reported, never retried forever',
    94: 'the old stop filled its part while the replacement is in flight',
    143: 'a stop / close fill beyond the booked position is deferred until the add fill arrives',
    278: 'deferred reducing fills keep venue order (FIFO barrier)',
    280: 'a leg keeps its racing allowance while a deferred fill of that leg waits',
    284: 'a binding counts a fill only once the core booked it (no second trigger fire)',
    398: 'core: a short partial REDUCE is retried as a REDUCE of the rest',
    464: 'core: a close fill of an earlier request is booked while a close is requested',
    2116: 'core: an old stop and its replacement may both execute (stop racing until Cancelled)',
    2366: 'core: every trade of the old stop is booked after the new one is exhausted',
    4293: 'a refused replacement whose old stop already executed: the rest is closed (stop failed)',
    4555: 'a short close re-requests only the part that died; other close orders keep their part',
}


@pytest.mark.parametrize('seed', sorted(FUZZ_SEEDS))
def test_fuzz_regression(seed):
    F.run_faults(seed)
