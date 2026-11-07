"""Bounded stop-safe full exits through the existing journal/NC-04 path only."""
import dataclasses
import threading
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from test_rt02_journaling import A, KEY, Harness, FailingJournal, _exit_plan, h, risk_evaluation
from test_rt01_runtime import _evaluation
from test_rt02_review_fixes import KEY2
from test_rt02_paper_e2e import paper
from rt01_fixtures import account_view
from newcore import risk, management
from newcore.domain import (Action, Authority, Decision, DecisionRecorded, ExchangeStatus, IntentState,
                            IntentRecorded, IntentStateChanged, Purpose, ReasonCode, ResultObserved, Side, make_id)
from newcore.ports import keys as K
from newcore.ports.venue import OrderOutcome, OutcomeKind
from newcore.reconciliation import PlanStatus, VenueSnapshot
from newcore.runtime import SinkProblem
from newcore.runtime import runtime as RT


def protected(h):
    accepted, decision = h.enter()
    assert accepted and h.drive() == ()
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    return lot, decision.quantity


def cancel_answers(h, answer='cancelled'):
    def cancel(ref):
        h.venue.calls.append(('cancel_stop', ref.route, ref.client_id, h.journal.last_sequence()))
        if answer == 'cancelled':
            return OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=h.wall(),
                                status='CANCELED', exchange_order_id='9003', executed_qty=D(0))
        return h.venue._answer(ref, answer)
    h.venue.cancel = cancel


def test_full_exit_preserves_stop_then_cancels_durably_and_waits_for_exact_proof(h):
    lot, qty = protected(h); cancel_answers(h)
    plan = _exit_plan(lot, qty)
    assert h.sink.accept_management(A, plan)
    seq = h.journal.last_sequence()
    assert h.sink.accept_management(A, plan)               # duplicate pending plan sends nothing
    assert h.journal.last_sequence() == seq
    assert len(h.sink._watched) == 1
    assert h.drive() == ()
    calls = h.venue.calls
    assert [call[0] for call in calls] == ['submit_market', 'submit_stop', 'submit_market', 'cancel_stop']
    assert calls[2][2:4] == (True, qty)
    events = tuple(h.journal.read())
    close_id = next(ev.intent.intent_id for ev in events if isinstance(ev, IntentRecorded)
                    and ev.intent.purpose is Purpose.CLOSE)
    close_final = next(i for i, ev in enumerate(events) if isinstance(ev, ResultObserved)
                       and ev.result.intent_id == close_id)
    cancelling = next(i for i, ev in enumerate(events) if isinstance(ev, IntentStateChanged)
                      and ev.to_state is IntentState.CANCELLING)
    assert close_final < cancelling and events[cancelling].sequence <= calls[-1][3]
    assert h.sink.snapshot().lots == ((lot, '0'),) and not h.journal.state().intents
    assert h.sink.accept_management(A, plan) is False
    assert h.enter(key=KEY2)[0] is False
    current = h.journal.last_sequence()
    assert not h.sink.acknowledge_reconciliation(current - 1)
    assert not h.sink.acknowledge_reconciliation(current + 1)
    assert h.enter(key=KEY2)[0] is False
    assert h.sink.acknowledge_reconciliation(current)
    assert h.enter(key=KEY2)[0] is True


@pytest.mark.parametrize('answer', ['partial', 'unknown', 'known'])
def test_partial_or_unresolved_close_preserves_stop_and_refuses_duplicate_new_risk(h, answer):
    lot, qty = protected(h); cancel_answers(h)
    h.venue.market = answer
    plan = _exit_plan(lot, qty)
    assert h.sink.accept_management(A, plan)
    assert SinkProblem.ORDER_UNRESOLVED in h.drive()
    assert not any(call[0] == 'cancel_stop' for call in h.venue.calls)
    assert len(h.sink._watched) == 1
    assert h.sink.snapshot().lots == ((lot, str(qty / 2 if answer == 'partial' else qty)),)
    assert not h.sink.accept_management(A, plan) and not h.enter(key=KEY2)[0]
    assert not h.sink.acknowledge_reconciliation(h.journal.last_sequence())


@pytest.mark.parametrize('answer', ['unknown', 'known', 'rejected', 'not_found'])
def test_unproven_stop_cancel_never_fabricates_terminal_stop_or_allows_entry(h, answer):
    lot, qty = protected(h); cancel_answers(h, answer)
    assert h.sink.accept_management(A, _exit_plan(lot, qty))
    assert SinkProblem.ORDER_UNRESOLVED in h.drive()
    assert len([call for call in h.venue.calls if call[0] == 'cancel_stop']) == 1
    assert not h.enter(key=KEY2)[0]
    assert not h.sink.acknowledge_reconciliation(h.journal.last_sequence())
    assert any(view.intent.purpose is Purpose.PROTECT and view.state is IntentState.UNKNOWN
               for view in h.journal.state().intents)


def test_stop_wins_before_close_ack_without_reversal_or_double_booking(h):
    lot, qty = protected(h); cancel_answers(h)
    stop = next(iter(h.sink._watched.values()))
    assert h.sink.accept_management(A, _exit_plan(lot, qty))
    # A completed owned stop is learned before the pending close's answer.
    h.sink._journal_stop_final(stop, '9003', ExchangeStatus.FILLED, qty, D(95), h.wall())
    h.venue.market = 'rejected'                              # hedge-side reduce close sees no remaining position
    assert h.drive() == ()
    assert h.sink.snapshot().lots == ((lot, '0'),)
    assert all(net >= 0 for _, net in h.journal.state().booked)
    assert not any(call[0] == 'cancel_stop' for call in h.venue.calls)


def test_partial_stop_and_partial_close_sum_only_the_actual_exposure(h):
    lot, qty = protected(h); cancel_answers(h)
    stop = next(iter(h.sink._watched.values()))
    assert h.sink.accept_management(A, _exit_plan(lot, qty))
    h.sink._journal_stop_final(stop, '9003', ExchangeStatus.EXPIRED, qty / 2, D(95), h.wall())
    h.venue.market = 'partial'
    h.drive()
    assert h.sink.snapshot().lots == ((lot, '0'),)
    assert all(net >= 0 for _, net in h.journal.state().booked)
    assert not h.enter(key=KEY2)[0]                          # protection/race uncertainty is sticky


def test_racing_close_cannot_book_more_than_the_remaining_lot(h):
    lot, qty = protected(h); cancel_answers(h)
    stop = next(iter(h.sink._watched.values()))
    assert h.sink.accept_management(A, _exit_plan(lot, qty))
    h.sink._journal_stop_final(stop, '9003', ExchangeStatus.EXPIRED, qty / 2, D(95), h.wall())
    # Hostile full-fill echo would double-book the stop's already recorded half.
    # A real hedge-side reduce order must reject or report its actual residual.
    assert SinkProblem.ORDER_UNRESOLVED in h.drive()
    assert all(net >= 0 for _, net in h.journal.state().booked)
    assert h.sink.snapshot().lots == ((lot, str(qty / 2)),)
    assert not h.enter(key=KEY2)[0]


def test_stop_cancel_answer_must_echo_exact_symbol_and_route(h):
    lot, qty = protected(h); cancel_answers(h)
    original_cancel = h.venue.cancel
    h.venue.cancel = lambda ref: dataclasses.replace(original_cancel(ref),
                                                    ref=dataclasses.replace(ref, symbol='ETHUSDT'))
    assert h.sink.accept_management(A, _exit_plan(lot, qty))
    assert SinkProblem.ORDER_UNRESOLVED in h.drive()
    assert not h.enter(key=KEY2)[0]


def test_stale_partial_quantity_plan_cannot_masquerade_as_a_full_close(h):
    lot, qty = protected(h)
    before = h.journal.last_sequence()
    assert not h.sink.accept_management(A, _exit_plan(lot, qty / 2))
    assert h.journal.last_sequence() == before and len(h.sink._watched) == 1


def test_flatten_required_uses_same_stop_safe_path(h):
    lot, qty = protected(h); cancel_answers(h)
    evaluation = _evaluation()
    evaluation = dataclasses.replace(evaluation,
        lot=dataclasses.replace(evaluation.lot, lot_id=lot, quantity=qty, initial_quantity=qty),
        market=dataclasses.replace(evaluation.market, low=D(90), mark=D(90)),
        protection=dataclasses.replace(evaluation.protection, confirmed_quantity=qty))
    plan = management.evaluate(evaluation)
    assert type(plan.actions[0]) is management.FlattenRequired
    assert h.sink.accept_management(A, plan) and h.drive() == ()
    assert h.sink.snapshot().lots == ((lot, '0'),)
    assert h.venue.calls[-1][0] == 'cancel_stop'


def test_stop_cancel_is_never_sent_before_its_cancelling_event_is_durable(h):
    lot, qty = protected(h); cancel_answers(h)
    assert h.sink.accept_management(A, _exit_plan(lot, qty))
    # The close's FINAL and terminal state succeed; the next CANCELLING append fails.
    h.sink._b = dataclasses.replace(h.sink._b, journal=FailingJournal(h.journal, 3))
    assert SinkProblem.ORDER_JOURNAL_FAILED in h.drive()
    assert not any(call[0] == 'cancel_stop' for call in h.venue.calls)
    assert len(h.sink._watched) == 1 and not h.enter(key=KEY2)[0]


def test_journal_mutation_after_ack_invalidates_cached_entry_clearance(h):
    lot, qty = protected(h); cancel_answers(h)
    assert h.sink.accept_management(A, _exit_plan(lot, qty)) and h.drive() == ()
    acknowledged = h.journal.last_sequence()
    assert h.sink.acknowledge_reconciliation(acknowledged)
    record = Decision(decision_id=make_id('dec', 999), account_id=A, at_ms=h.wall(), action=Action.WAIT,
        reason=ReasonCode.ENTRY_SIGNAL, authority=Authority.STRATEGY, key=None, evidence=(), symbol='BTCUSDT',
        side=Side.LONG, subject_id=None, detail='', intents=(), policy_version='test')
    h.journal.append(h.sink._event(DecisionRecorded, at_ms=h.wall(), reason=record.reason, decision=record))
    assert not h.sink.acknowledge_reconciliation(acknowledged)
    assert not h.enter(key=KEY2)[0]
    assert h.sink.acknowledge_reconciliation(h.journal.last_sequence())
    assert h.enter(key=KEY2)[0]


@pytest.mark.parametrize('crash', ['close_pending', 'cancel_pending', 'cancel_confirmed'])
def test_restart_never_resends_close_or_cancel_and_requires_new_flat_proof(h, tmp_path, crash):
    lot, qty = protected(h); cancel_answers(h)
    assert h.sink.accept_management(A, _exit_plan(lot, qty))
    if crash != 'close_pending':
        while h.context.run_next() is not None:
            pass
        h.sink.poll(h.mono())                             # close filled, stop cancel durably queued
    if crash == 'cancel_confirmed':
        h.drive()
    h.close()
    again = Harness(tmp_path, create=False)
    try:
        assert again.submitted == []
        assert not again.enter(key=KEY2)[0]
        if crash == 'cancel_confirmed':
            assert not again.sink.snapshot().problems
            assert again.sink.acknowledge_reconciliation(again.journal.last_sequence())
        else:
            assert SinkProblem.RECOVERED_LIVE_INTENTS in again.sink.snapshot().problems
            assert not again.sink.acknowledge_reconciliation(again.journal.last_sequence())
    finally:
        again.close()


def fold_fixture():
    state = {'sequence': 7}
    acknowledgements = []
    journal = SimpleNamespace(last_sequence=lambda: state['sequence'])
    actions = SimpleNamespace(acknowledge_reconciliation=acknowledgements.append)
    evidence = SimpleNamespace(assemble=lambda *args: VenueSnapshot(()))
    row = SimpleNamespace(journal=journal, ports=SimpleNamespace(actions=actions, evidence=evidence),
                          lock=threading.Lock(), spec=SimpleNamespace(account_id=A), proof_mono=10,
                          proof_sequence=6)
    config = SimpleNamespace(max_snapshot_age_s=10)
    return state, acknowledgements, row, config


@pytest.mark.parametrize('mutation', ['none', 'load', 'plan', 'ack', 'old_durable', 'hold'])
def test_reconciliation_ack_is_bound_to_unchanged_durable_sequence(monkeypatch, mutation):
    state, acknowledgements, row, config = fold_fixture()
    def load(*args):
        if mutation == 'load': state['sequence'] += 1
        return SimpleNamespace(portfolio_generation=6 if mutation == 'old_durable' else 7), None
    def plan(*args):
        if mutation == 'plan': state['sequence'] += 1
        return SimpleNamespace(status=PlanStatus.HOLD if mutation == 'hold' else PlanStatus.FLAT_CONFIRMED,
                               reconciliation_id='proof')
    if mutation == 'ack':
        def acknowledge(sequence):
            acknowledgements.append(sequence)
            state['sequence'] += 1
        row.ports.actions.acknowledge_reconciliation = acknowledge
    monkeypatch.setattr(RT, '_load_durable', load)
    monkeypatch.setattr(RT, 'plan_reconciliation', plan)
    result = RT._fold(config, row, (), 1, 20, lambda: 1000)
    assert acknowledgements == ([7] if mutation in ('none', 'ack') else [])
    if mutation in ('load', 'plan', 'ack', 'hold'):
        assert result is RT.HoldCode.RECONCILE_HOLD and row.proof_mono is None
    else:
        assert result is None


def test_paper_close_cancels_owned_stop_and_rec02_proves_flat_before_new_entry(paper):
    w, venue, sink, marks = paper()
    w.boot()
    w.settle(lambda snapshot: account_view(snapshot, A).last_plan_status == 'protected_confirmed', advance_ms=1000)
    lot, qty = next(iter(sink.snapshot().lots)); qty = D(qty)
    assert sink.accept_management(A, _exit_plan(lot, qty))
    w.settle(lambda snapshot: not w.runtime._accounts[A].journal.state().intents
             and account_view(snapshot, A).last_plan_status == 'flat_confirmed'
             and sink._reconciled_sequence == w.runtime._accounts[A].journal.last_sequence(), advance_ms=1000)
    assert all(row.qty == 0 for row in venue.positions().value) and venue.open_orders('BTCUSDT').value == ()
    evaluation = risk_evaluation()
    assert sink.accept_entry(A, risk.evaluate(evaluation), evaluation, key=KEY2)
