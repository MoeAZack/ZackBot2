"""Classic -> algo protective-stop fallback (STEP0_INTERFACE r2, grammar G6) in the Runner:
- a classic STOP_MARKET refused with a code that names the algo service (-4120 / -1116 / -1102 / -4136, as the
  transport's ALGO_FALLBACK_CODES) closes that intent REJECTED; the next protect intent of the lot is the lineage child
  from next_child_intent_id with the ALGO client id;
- a refusal with another code is a stop failure (reduce-only close), never an algo attempt;
- never a fallback after an UNKNOWN: the classic attempt is resolved first (query, same-id re-send);
- a restart between the routes still falls back once (the code is lost: protection outranks); no duplicate route send."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, IntentState, Purpose
from newcore.ports import keys as K
from slice_helpers import Crash, World, flat_bars
from test_runner_e2e import ENTRY_BAR, signals


def stop_routes(w):
    lot = w.runner.fold.lots()[0]
    return [(K.route_of(p.intent_id, p.intent.client_order_id), p.state) for p in lot.protects]


def stop_sends(w):
    return [e for e in w.port.effects if e == 'stop']


@pytest.mark.parametrize('code', [-4120, -1116, -1102, -4136])
def test_classic_refusal_naming_the_algo_service_falls_back_once(code):
    w = World(flat_bars(20), signals(exit_=None))
    w.venue.refuse_classic_stops(code)
    w.run(ENTRY_BAR + 2)
    r = w.runner
    assert stop_routes(w) == [('classic', IntentState.REJECTED), ('algo', IntentState.WORKING)]
    lot = r.fold.open_lots()[0]
    algo = lot.protects[1]
    assert algo.intent_id == K.derive_child_intent_id(r.acct, lot.lot_id, Purpose.PROTECT, 1)
    assert algo.intent.client_order_id == K.client_id_for(algo.intent_id, 'algo')
    o, = w.venue.open_orders().value
    assert o.ref.route == 'algo' and o.ref.client_id == algo.intent.client_order_id
    assert r.fold.mode is EntriesMode.ACTIVE and not [x for x in w.venue.orders_submitted() if x.reduce and
                                                       x.order_type == 'MARKET']
    assert len(stop_sends(w)) == 2


def test_another_refusal_code_is_a_stop_failure_not_a_fallback():
    w = World(flat_bars(20), signals(exit_=None))
    w.venue.refuse_classic_stops(-2010)                            # e.g. "insufficient margin": not a route problem
    w.run(ENTRY_BAR + 1)
    assert [rt for rt, _ in stop_routes(w)] == ['classic']
    assert w.runner.fold.open_lots() == []                         # closed at market: exit.stop_failed
    assert w.runner.trades()[0].exit_code == 'STOP_FAILED'


def test_no_fallback_after_an_unknown_until_it_is_resolved():
    """The classic answer is lost and the order never landed: the same classic id is re-sent (and refused with
    -4120); only after that classic attempt CLOSED rejected does the algo intent exist."""
    w = World(flat_bars(20), signals(exit_=None))
    w.venue.refuse_classic_stops(-4120)
    w.port.lose('stop')
    w.run(ENTRY_BAR)
    kinds = [type(e).__name__ + ':' + getattr(getattr(e, 'to_state', None), 'value', '')
             for e in w.journal.read()]
    lot = w.runner.fold.open_lots()[0]
    classic, algo = lot.protects
    closed_at = next(i for i, e in enumerate(w.journal.read())
                     if getattr(e, 'intent_id', None) == classic.intent_id and kinds[i].endswith('rejected'))
    recorded_at = next(i for i, e in enumerate(w.journal.read())
                       if getattr(getattr(e, 'intent', None), 'intent_id', None) == algo.intent_id)
    assert closed_at < recorded_at                                 # never a fallback while the classic one was open
    assert algo.state is IntentState.WORKING and len(stop_sends(w)) == 3   # lost, re-sent (same id), algo


def test_an_unresolvable_unknown_never_falls_back():
    """The answer is lost and the venue cannot be read for that id: the attempt stays UNKNOWN (HOLD, surfaced as
    naked), and no algo attempt is ever made while its classic sibling may still be live."""
    w = World(flat_bars(20), signals(exit_=None), strict=False)
    acct = w.config.account.account_id
    key = K.decision_key('injected', 'v1', '4h', 'SOLUSDT', 'LONG', w.close_ms(ENTRY_BAR), 'entry')
    lot_id = K.derive_lot_id(acct, K.derive_intent_id(acct, key))
    first_stop = K.derive_child_intent_id(acct, lot_id, Purpose.PROTECT, 0)
    w.venue.refuse_classic_stops(-4120)
    w.port.lose('stop')
    w.port.blind(K.client_id_for(first_stop, 'classic'))
    w.run(ENTRY_BAR + 3)
    lot = w.runner.fold.open_lots()[0]
    assert [(p.intent_id, p.state) for p in lot.protects] == [(first_stop, IntentState.UNKNOWN)]
    assert w.runner.fold.mode is EntriesMode.HOLD and len(stop_sends(w)) == 1
    assert w.runner.counters.unprotected_cycles == 4                # visible every cycle, never hidden


def test_restart_between_the_routes_still_falls_back_once():
    w = World(flat_bars(20), signals(exit_=None))
    w.venue.refuse_classic_stops(-4120)
    w.run(ENTRY_BAR - 1)
    w.journal.fail_writes(1, after=10, error=Crash)                # 5 entry + 5 classic-attempt events, then dies
    with pytest.raises(Crash):                                     # ... before the algo decision is written
        w.run(ENTRY_BAR)
    assert stop_routes(w) == [('classic', IntentState.REJECTED)]
    w.restart()                                                    # the refusal code is gone with the process
    w.runner.cycle(w.close_ms(ENTRY_BAR))
    w.run(ENTRY_BAR + 2)
    assert stop_routes(w) == [('classic', IntentState.REJECTED), ('algo', IntentState.WORKING)]
    assert len(stop_sends(w)) == 2


@pytest.mark.parametrize('after', [11, 12, 13])
def test_crash_around_the_algo_send_never_sends_the_route_twice(after):
    """11: algo decision written; 12: algo intent written; 13: 'sent' written (venue call next)."""
    w = World(flat_bars(20), signals(exit_=None))
    w.venue.refuse_classic_stops(-4120)
    w.run(ENTRY_BAR - 1)
    w.journal.fail_writes(1, after=after, error=Crash)
    with pytest.raises(Crash):
        w.run(ENTRY_BAR)
    w.restart()
    w.runner.cycle(w.close_ms(ENTRY_BAR))
    w.restart()
    w.runner.cycle(w.close_ms(ENTRY_BAR))
    w.run(ENTRY_BAR + 2)
    assert stop_routes(w) == [('classic', IntentState.REJECTED), ('algo', IntentState.WORKING)]
    algo_orders = [o for o in w.venue.orders_submitted() if o.ref.route == 'algo']
    assert len(algo_orders) == 1 and w.runner.counters.unprotected_cycles == 0


pytestmark = pytest.mark.usefixtures('journal_kind')            # every test: MemoryJournal and FileJournal
