"""Adversarial RT-02 repros. Every test asserts the GUARANTEE; a failure is a finding."""
import dataclasses
from decimal import Decimal as D

import pytest

from test_rt02_journaling import (A, CANDLE, KEY, FailingJournal, Harness, _exit_plan, h,  # noqa: F401
                                  risk_evaluation)
from newcore.domain import IntentRecorded, IntentState, IntentStateChanged, Purpose, Side
from newcore.ports import keys as K
from newcore.ports.venue import OrderOutcome, OutcomeKind
from newcore.runtime import JournalingActionSink, SinkBinding
from rt01_fixtures import pf  # noqa: E402

KEY2 = K.decision_key('trend_ema_mom', 'v1', '4h', 'BTCUSDT', Side.LONG, CANDLE + 14_400_000, Purpose.ENTRY)


def states(h):
    """intent_id -> (purpose, qty, last state) from the durable journal."""
    out = {}
    for ev in h.journal.read():
        if isinstance(ev, IntentRecorded):
            out[ev.intent.intent_id] = [ev.intent.purpose.value, ev.intent.qty, ev.intent.state.value]
        elif isinstance(ev, IntentStateChanged):
            out[ev.intent_id][2] = ev.to_state.value
    return out


# F1: a market entry answered KNOWN/NEW (Binance's default ACK for MARKET with newOrderRespType=ACK, or a
# PARTIALLY_FILLED echo) goes WORKING, is dropped from pending and never protected; no problem, entries keep flowing.
def test_F1_entry_answered_known_is_never_protected_and_raises_nothing(h):
    h.venue.market = 'known'
    assert h.enter()[0] is True
    problems = h.drive()
    stops = [c for c in h.venue.calls if c[0] == 'submit_stop']
    second = h.enter(key=KEY2)[0]
    print('problems', problems, 'stops', stops, 'second entry accepted', second, 'calls', [c[:2] for c in h.venue.calls])
    assert problems != () or stops, 'possibly-filled entry has no stop and no problem'
    assert second is False, 'a second entry was accepted while the first may be an unprotected position'


# F2: closing a lot never cancels its resting stop; a later entry gets its own stop -> resting stop qty > position.
def test_F2_close_leaves_resting_stop_and_stops_exceed_position(h):
    _, decision = h.enter()
    h.drive()
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    assert h.sink.accept_management(A, _exit_plan(lot, decision.quantity)) is False   # the stop still rests
    h.drive()
    assert h.enter(key=KEY2)[0] is True
    h.drive()
    working_stop_qty = sum(q for p, q, s in states(h).values() if p == 'protect' and s == 'working')
    open_qty = sum(D(q) for _, q in h.sink.snapshot().lots)
    print('working stop qty', working_stop_qty, 'open lot qty', open_qty, 'lots', h.sink.snapshot().lots)
    assert working_stop_qty <= open_qty, 'resting stops exceed the open position'


# F3: wall clock steps back between send and fold -> NC-01 refuses the ResultObserved (observed < created).
# The FILLED entry is never recorded, never protected, never flattened; intent stays SUBMITTED.
def test_F3_clock_backwards_leaves_a_filled_entry_unprotected(h):
    assert h.enter()[0] is True
    while h.context.run_next() is not None:
        pass
    h.wall.advance(-5_000)          # NTP step back
    problems = h.sink.poll(h.mono())
    while h.context.run_next() is not None:
        pass
    print('problems', problems, 'calls', [c[:3] for c in h.venue.calls], 'journal', h.kinds()[-1],
          'lots', h.sink.snapshot().lots)
    assert any(c[0] == 'submit_stop' for c in h.venue.calls) or \
        any(c[0] == 'submit_market' and c[2] for c in h.venue.calls), 'filled entry neither protected nor flattened'


# F4: crash after the entry FILLED is durable but before its protect is journaled: on restart the lot is rebuilt
# unprotected, no problem is raised, and a new entry is accepted.
def test_F4_restart_with_unprotected_lot_raises_nothing(h, monkeypatch):
    # "crash" = the process dies right after the entry FILLED step is durable, before the protect is journaled
    orig = JournalingActionSink._after_final
    JournalingActionSink._after_final = lambda self, *a: None
    try:
        h.enter()
        h.drive()
    finally:
        JournalingActionSink._after_final = orig
    h.close()
    h.open()
    snap = h.sink.snapshot()
    second = h.enter(key=KEY2)[0]
    print('lots', snap.lots, 'problems', snap.problems, 'second entry accepted', second)
    assert snap.lots and (snap.problems or not second), 'unprotected recovered lot raised nothing'


# F5: a protective stop whose submit answers FINAL EXPIRED/CANCELED with 0 executed leaves the lot unprotected with
# no problem and no flatten.
def test_F5_stop_final_expired_zero_fill_leaves_lot_unprotected(h):
    def submit_stop(order):
        h.venue.calls.append(('submit_stop', order.ref.route, order.stop_price, order.qty, 0))
        return OrderOutcome(kind=OutcomeKind.FINAL, ref=order.ref, observed_at_ms=h.venue.now(), status='EXPIRED',
                            exchange_order_id='77', executed_qty=D('0'))
    h.venue.submit_stop = submit_stop
    h.enter()
    problems = h.drive()
    print('problems', problems, 'calls', [c[:3] for c in h.venue.calls], 'tail', h.kinds()[-2:])
    assert problems != () or any(c[0] == 'submit_market' and c[2] for c in h.venue.calls), \
        'lot left without protection and nothing raised'


# F6: a journal failure between a child DecisionRecorded and its IntentRecorded must not burn the lineage ordinal:
# after a restart the lot is reported unprotected and a protection repair can still be journaled.
def test_F6_half_journaled_child_decision_does_not_block_the_lot(tmp_path):
    from test_rt02_journaling import _plan_for
    from newcore.management import ProtectionStatus
    h = Harness(tmp_path)
    h.sink = JournalingActionSink()
    fj = FailingJournal(h.journal, 7)       # 1-5 entry and its fill, 6 protect decision, 7 protect intent fails
    h.sink.bind(SinkBinding(account_id=A, aggregate_id=pf(1), journal=fj, submit=h._submit, result=h.context.result,
                            cancel=h.context.cancel, wall_clock=h.wall, order_deadline_ms=10_000), h.mono)
    _, decision = h.enter()
    problems = h.drive()
    assert problems == ('order_journal_failed', 'protection_failed')
    assert [c[0] for c in h.venue.calls] == ['submit_market']        # no flatten journaled after a failed append
    h.close()
    h.open()                                # restart
    assert h.sink.snapshot().problems == ('protection_failed',)
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    h.sink._lots[lot].stop_price = D('95')
    assert h.sink.accept_management(A, _plan_for(lot, decision.quantity,
                                                 protection_status=ProtectionStatus.MISSING)) is True
    h.drive()
    assert h.venue.calls[-1][:2] == ('submit_stop', 'algo')
    h.close()


# F7: a management close answered KNOWN (NEW) frees the lot; the next close plan sends a second full-lot close.
def test_F7_close_answered_known_allows_double_close(h):
    _, decision = h.enter()
    h.drive()
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    h.sink._lots[lot].last_protect = ('algo', True, IntentState.CANCELLED)   # no resting stop
    h.venue.market = 'known'
    assert h.sink.accept_management(A, _exit_plan(lot, decision.quantity)) is True
    problems = h.drive()
    again = h.sink.accept_management(A, _exit_plan(lot, decision.quantity))
    h.drive()
    closes = [c for c in h.venue.calls if c[0] == 'submit_market' and c[2]]
    print('problems', problems, 'second close accepted', again, 'reduce sends', [c[3] for c in closes])
    assert sum(c[3] for c in closes) <= decision.quantity, 'closes sent exceed the lot'


# F8: submit refused (scheduler stopping) for the protect: the sink then journals a flatten as SENT that is also
# never queued; the journal claims two orders were sent that never left the process (and none moves to UNKNOWN).
def test_F8_scheduler_stopping_journals_unsent_orders_as_sent(tmp_path):
    h = Harness(tmp_path)
    state = {'n': 0}

    def submit(work):
        state['n'] += 1
        if state['n'] > 1:
            raise RuntimeError('scheduler_stopping')
        h._submit(work)
    h.sink = JournalingActionSink()
    h.sink.bind(SinkBinding(account_id=A, aggregate_id=pf(1), journal=h.journal, submit=submit,
                            result=h.context.result, cancel=h.context.cancel, wall_clock=h.wall,
                            order_deadline_ms=10_000), h.mono)
    h.enter()
    problems = h.drive()
    st = states(h)
    print('problems', problems, 'intents', [(p, s) for p, _, s in st.values()], 'venue', [c[:3] for c in h.venue.calls])
    phantom = [p for p, _, s in st.values() if s == 'submitted']
    # The protect's SENT was durable before the refusal, so only a venue query can prove it unsent: it stays
    # unresolved.  Nothing more (no flatten) is journaled on top of a refused submit.
    assert phantom == ['protect'] and set(problems) == {'order_unresolved', 'protection_failed'}


# P3: a stop that executed part of the lot is subtracted on restart too, so the rebuilt lot matches the live one.
def test_P3_partly_executed_stop_then_flatten_rebuilds_flat(h):
    h.venue.stop = {'algo': 'partial'}
    _, decision = h.enter()
    assert h.drive() == ('protection_failed',)
    closes = [c for c in h.venue.calls if c[0] == 'submit_market' and c[2]]
    assert [c[3] for c in closes] == [decision.quantity / 2]           # the flatten covers only what is left
    lots = h.sink.snapshot().lots
    h.close()
    h.open()
    assert h.sink.snapshot().lots == lots and lots[0][1] == '0'
    assert h.sink.snapshot().problems == ()


# P2: result ids include the aggregate.
def test_P2_result_ids_name_the_aggregate(h):
    from newcore.runtime import journaling as J
    assert J._h32('res', A, pf(1), '4') != J._h32('res', A, pf(2), '4')
