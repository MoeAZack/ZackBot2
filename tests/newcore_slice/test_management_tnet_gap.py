"""TNET-01 (was a strict xfail; flipped by the driver route fallback, 8fe37ae / 35a3af8): on a venue that refuses classic
STOP_MARKET (Binance -4120: use the algo service, as testnet does) a MANAGED lot stays open, protected by a NEW
algo-route PROTECT child (G6: the fallback of the refused classic attempt), through the runner:
classic refused -> route marker journaled ('mg fallback') -> driver.route_fallback -> algo stop sent on its route ->
confirmed -> protected. A refusal that does not name the algo service ('mg refused') still reaches the core (stop
failed -> close). The marker makes it restart-exact: a crash at any journal boundary, or a restart after every cycle,
folds to the same driver state; after a restart an unknown refusal code takes the algo route (protection outranks)."""
import pytest

from newcore.domain import Action, EntriesMode, IntentState, Purpose, ReasonCode
from newcore.ports.keys import route_of
from newcore.runner.managed import SyntheticPlans
from mg_helpers import MgWorld, ZERO_COSTS, intents_of, path, signals
from slice_helpers import Crash

pytestmark = pytest.mark.usefixtures('journal_kind')
SIDES = ('LONG', 'SHORT')
PLANS = SyntheticPlans(costs=ZERO_COSTS, time_exit_candles=8)
# long frame: the add level at bar 6, beyond TP1 at bar 8, then the time exit (bar 13)
FLOW = {6: (100, 100.2, 98.9, 99), 7: (99, 99.2, 98.9, 99), 8: (99, 102.2, 98.9, 102)}


def world(side, **kw):
    w = MgWorld(path(24, FLOW, side), signals(side), plans=PLANS, **kw)
    w.venue.refuse_classic_stops(-4120)
    return w


def routes(r):
    return [route_of(s.intent_id, s.intent.client_order_id) for s in intents_of(r, Purpose.PROTECT)]


def markers(r, kind):
    return [d for d in r.fold.decisions.values() if d.action is Action.WAIT and d.detail.startswith(kind)]


def run_safely(w, upto):
    start = max(0, (w.venue.now_ms - w.t0) // w.config.tf_ms)
    for i in range(start, upto + 1):
        t = w.close_ms(i)
        w.venue.advance_to(t)
        try:
            w.runner.cycle(t)
        except Crash:
            w.port.disarm()
            w.restart()
            w.runner.cycle(t)
    return w.runner


@pytest.mark.parametrize('side', SIDES)
def test_tnet01_a_refused_classic_plan_stop_falls_back_to_an_algo_protect_child(side):
    w = world(side)
    w.run(5)
    r = w.runner
    lot, = r.fold.open_lots()                                       # still open: no stop-failed close
    classic, algo = intents_of(r, Purpose.PROTECT)
    assert routes(r) == ['classic', 'algo'] and classic.state is IntentState.REJECTED
    assert lot.carrier is algo and algo.state is IntentState.WORKING and algo.intent.qty >= lot.qty
    assert algo.intent.stop_price == classic.intent.stop_price       # the same protection, on the algo route
    assert len(markers(r, 'mg fallback')) == 1 and not intents_of(r, Purpose.CLOSE)
    assert r.counters.unprotected_cycles == 0 and r.fold.mode is EntriesMode.ACTIVE


@pytest.mark.parametrize('side', SIDES)
def test_tnet01_the_whole_flow_runs_on_algo_stops(side):
    """Every later plan stop (resize after the add, break-even after TP1) is refused classic and re-placed algo; the
    old stop is cancelled on ITS route only after the new one is confirmed; the time exit flattens; nothing naked."""
    w = world(side)
    r = run_safely(w, 16)
    assert all(p.qty == 0 for p in w.venue.positions().value) and not w.venue.open_orders().value
    rs = routes(r)
    assert rs == ['classic', 'algo'] * (len(rs) // 2) and len(rs) >= 6
    assert all(s.state is IntentState.REJECTED for s in intents_of(r, Purpose.PROTECT)[0::2])
    assert intents_of(r, Purpose.ADD) and r.trades()[0].exit_reason is ReasonCode.EXIT_TIME
    assert r.counters.unprotected_cycles == 0 and r.fold.mode is EntriesMode.ACTIVE


@pytest.mark.parametrize('side', SIDES)
def test_a_refusal_that_does_not_name_the_algo_service_still_closes_the_lot(side):
    w = MgWorld(path(24, FLOW, side), signals(side))
    w.port.refuse('stop', -2021)                                    # would trigger immediately: no algo fallback
    w.run(5)
    r = w.runner
    assert routes(r) == ['classic'] and len(markers(r, 'mg refused')) == 1
    close, = intents_of(r, Purpose.CLOSE)
    assert close.intent.reason is ReasonCode.EXIT_STOP_FAILED and not r.fold.open_lots()
    assert r.counters.unprotected_cycles == 0


@pytest.mark.parametrize('side', SIDES)
def test_restart_after_every_cycle_folds_the_same_driver_state_through_the_fallbacks(side):
    w = world(side)
    for i in range(17):
        t = w.close_ms(i)
        w.venue.advance_to(t)
        w.runner.cycle(t)
        old = w.runner
        new = w.restart()
        assert new.mg == old.mg and new.unmanaged == old.unmanaged


def _ref_events(side):
    w = world(side)
    run_safely(w, 16)
    return w


REF = {s: _ref_events(s) for s in SIDES}


@pytest.mark.parametrize('side,k', [(s, k) for s in SIDES for k in range(len(REF[s].journal.read()))])
def test_crash_at_every_journal_boundary_of_the_algo_flow(side, k):
    """A crash at any write - including between the classic refusal and its route marker, where the restart no longer
    knows the refusal code and takes the algo route - ends with the same routes, the same trade, never naked."""
    ref = REF[side]
    w = world(side)
    w.journal.fail_writes(1, after=k, error=Crash)
    r = run_safely(w, 16)
    assert r.counters.unprotected_cycles == 0 and r.fold.mode is EntriesMode.ACTIVE
    assert routes(r) == routes(ref.runner)
    assert [(t.exit_reason, t.entry_price, t.exit_price) for t in r.trades()] == \
        [(t.exit_reason, t.entry_price, t.exit_price) for t in ref.runner.trades()]
    assert all(p.qty == 0 for p in w.venue.positions().value)
