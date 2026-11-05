"""RT-02 journaling sink: every order is NC-02-journaled before NC-04 can send it, and results fold back.

Real NC-02 journals on disk, a real NC-04 AccountContext and budget, real NC-06 approvals and NC-07 plans.  The venue
is scripted per call and records the journal length it saw, so "journaled before sent" is checked at the send itself.
Stops go on the algo route first (journal rule G6 as amended by RT-02; NC-04 has only the algo SUBMIT_STOP row).
"""
from __future__ import annotations

import dataclasses
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'newcore_risk'))

from rt01_fixtures import WALL0, Clock, MemoryRateJournal, acct, limits, pf  # noqa: E402

import newcore.account_context as nc04  # noqa: E402
import test_nc06_risk as risk_fixtures  # noqa: E402
from newcore import management as nc07  # noqa: E402
from newcore import risk as nc06  # noqa: E402
from newcore.account_context import AccountBudget, AccountContext, Operation  # noqa: E402
from newcore.domain import (DecisionRecorded, Evidence, IntentRecorded, IntentState, IntentStateChanged,  # noqa: E402
                            Purpose, ResultObserved, ResultPhase, Side)
from newcore.management import ProtectionStatus  # noqa: E402
from newcore.ports import keys as K  # noqa: E402
from newcore.ports.venue import OrderOutcome, OutcomeKind  # noqa: E402
from newcore.runtime import JournalingActionSink, SinkBinding, SinkProblem  # noqa: E402
from newcore.store import Verdict, create_journal, recover_journal  # noqa: E402
from newcore.store.errors import DurabilityUnavailable  # noqa: E402

from test_rt01_runtime import _evaluation  # noqa: E402

D = Decimal
A = acct(1)
CANDLE = 1_999_987_200_000          # on the 4h grid
KEY = K.decision_key('trend_ema_mom', 'v1', '4h', 'BTCUSDT', Side.LONG, CANDLE, Purpose.ENTRY)


def risk_evaluation(**request_changes):
    f = risk_fixtures
    return nc06.RiskEvaluation(
        account=f.account(account_id=A), portfolio=f.portfolio(account_id=A),
        policy=f.policy(account_id=A), filters=f.filters(), request=f.request(account_id=A, **request_changes),
        followers=())


@pytest.fixture
def no_algo_stop(monkeypatch):
    """NC-04 without its algo SUBMIT_STOP row: no protect route is admissible."""
    monkeypatch.delitem(nc04._ENDPOINTS, (Operation.SUBMIT_STOP, 'algo'))


class OrderVenue:
    """Scripted order answers.  Every write records how many journal events were durable when it was sent."""

    def __init__(self, journal):
        self.journal = journal
        self.calls = []
        self.market = 'filled'
        self.stop = {'classic': 'rejected', 'algo': 'known'}
        self.now = Clock(WALL0)

    def _answer(self, ref, kind, qty=None, status=None):
        at = self.now()
        if kind == 'filled':
            return OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=at, status='FILLED',
                                exchange_order_id='9001', executed_qty=qty, avg_price=D('100'))
        if kind == 'partial':
            return OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=at, status='EXPIRED',
                                exchange_order_id='9002', executed_qty=qty / 2, avg_price=D('100'))
        if kind == 'rejected':
            return OrderOutcome(kind=OutcomeKind.REJECTED, ref=ref, observed_at_ms=at, error_code=-4120)
        if kind == 'known':
            return OrderOutcome(kind=OutcomeKind.KNOWN, ref=ref, observed_at_ms=at, status='NEW',
                                exchange_order_id='9003')
        if kind == 'unknown':
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=at, detail='timeout')
        if kind == 'not_found':
            return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=at, error_code=-2013)
        if kind == 'raise':
            raise RuntimeError('venue down')
        if kind == 'echo':
            from newcore.ports.venue import OrderRef
            other = OrderRef(symbol=ref.symbol, client_id=K.client_id_for('int_' + 'e' * 32), route='classic')
            return OrderOutcome(kind=OutcomeKind.FINAL, ref=other, observed_at_ms=at, status='FILLED',
                                exchange_order_id='9009', executed_qty=qty, avg_price=D('100'))
        raise AssertionError(kind)

    def submit_market(self, order):
        self.calls.append(('submit_market', order.ref.route, order.reduce, order.qty, self.journal.last_sequence()))
        return self._answer(order.ref, self.market, order.qty)

    def submit_stop(self, order):
        self.calls.append(('submit_stop', order.ref.route, order.stop_price, order.qty, self.journal.last_sequence()))
        return self._answer(order.ref, self.stop[order.ref.route], order.qty)

    def cancel(self, ref):
        raise AssertionError('no cancel in RT-02')

    def query(self, ref):
        raise AssertionError('no query in RT-02')

    def positions(self, symbol=None):
        raise AssertionError('no reads here')

    open_orders = fills = positions


class Harness:
    def __init__(self, tmp_path, *, create=True):
        self.dir = str(tmp_path / 'a1')
        self.mono, self.wall = Clock(1_000_000), Clock(WALL0)
        if create:
            create_journal(self.dir, A, pf(1)).close()
        self.open()

    def open(self):
        rec = recover_journal(self.dir, A, pf(1))
        assert rec.verdict in (Verdict.CLEAN, Verdict.REPAIRED)
        self.journal = rec.journal
        self.venue = OrderVenue(self.journal)
        exchange, account = limits()
        budget = AccountBudget(account_id=A, egress_id='e1', exchange_info=exchange, account_order_limits=account,
                               monotonic_clock=self.mono, wall_clock=self.wall, journal=MemoryRateJournal())
        self.context = AccountContext(account_id=A, egress_id='e1', venue=self.venue, budget=budget,
                                      monotonic_clock=self.mono)
        self.submitted = []
        self.sink = JournalingActionSink()
        self.sink.bind(SinkBinding(account_id=A, aggregate_id=pf(1), journal=self.journal, submit=self._submit,
                                   result=self.context.result, cancel=self.context.cancel, wall_clock=self.wall,
                                   order_deadline_ms=10_000), self.mono)

    def _submit(self, work):
        self.submitted.append(work)
        self.context.submit(work)

    def drive(self, rounds=8):
        problems = ()
        for _ in range(rounds):
            while self.context.run_next() is not None:
                pass
            self.mono.advance(10)
            self.wall.advance(10)
            problems = self.sink.poll(self.mono())
        return problems

    def enter(self, key=KEY, **changes):
        evaluation = risk_evaluation(**changes)
        decision = nc06.evaluate(evaluation)
        assert type(decision) is nc06.ApprovedDecision
        return self.sink.accept_entry(A, decision, evaluation, key=key), decision

    def kinds(self):
        out = []
        for ev in self.journal.read():
            if isinstance(ev, DecisionRecorded):
                out.append(('decision', ev.decision.action.value))
            elif isinstance(ev, IntentRecorded):
                out.append(('intent', ev.intent.purpose.value, K.route_of(ev.intent.intent_id,
                                                                          ev.intent.client_order_id)))
            elif isinstance(ev, IntentStateChanged):
                out.append(('state', ev.to_state.value))
            elif isinstance(ev, ResultObserved):
                r = ev.result
                out.append(('result', r.phase.value, r.evidence.value if r.evidence else None))
        return out

    def close(self):
        self.journal.close()


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    try:
        harness.close()
    except Exception:
        pass


ENTRY_SENT = [('decision', 'enter'), ('intent', 'entry', 'classic'), ('state', 'submitted')]


# ------------------------------------------------------------------------------------------------ today's NC-04 table
def test_entry_is_refused_while_nc04_cannot_send_a_stop(h, no_algo_stop):
    accepted, _ = h.enter()
    assert accepted is False
    assert h.journal.last_sequence() == 0 and h.venue.calls == [] and h.submitted == []
    assert 'protect_route_unverified' in h.sink.snapshot().log


def test_entry_without_a_signal_key_is_refused(h):
    evaluation = risk_evaluation()
    decision = nc06.evaluate(evaluation)
    assert h.sink.accept_entry(A, decision, evaluation) is False
    assert h.journal.last_sequence() == 0


@pytest.mark.parametrize('key', [
    K.decision_key('trend_ema_mom', 'v1', '4h', 'ETHUSDT', Side.LONG, CANDLE, Purpose.ENTRY),
    K.decision_key('trend_ema_mom', 'v1', '4h', 'BTCUSDT', Side.SHORT, CANDLE, Purpose.ENTRY),
    K.decision_key('trend_ema_mom', 'v1', '4h', 'BTCUSDT', Side.LONG, CANDLE, Purpose.CLOSE)])
def test_entry_key_must_name_the_requested_symbol_side_and_entry(h, key):
    accepted, _ = h.enter(key=key)
    assert accepted is False and h.journal.last_sequence() == 0


def test_entry_for_another_account_is_refused(h):
    evaluation = risk_evaluation()
    decision = nc06.evaluate(evaluation)
    assert h.sink.accept_entry(acct(2), decision, evaluation, key=KEY) is False
    assert h.journal.last_sequence() == 0


# ------------------------------------------------------------------------------------------------ the G6 path
def test_entry_fill_is_protected_on_the_algo_route(h):
    accepted, decision = h.enter()
    assert accepted is True
    assert h.kinds() == ENTRY_SENT and h.venue.calls == []          # durable first, nothing sent yet
    assert h.drive() == ()
    entry_call, algo = h.venue.calls
    assert entry_call[:4] == ('submit_market', 'classic', False, decision.quantity) and entry_call[4] == 3
    assert algo[:4] == ('submit_stop', 'algo', D('95'), decision.quantity)
    assert algo[4] == 8                                              # the stop was journaled before it was sent
    assert h.kinds() == ENTRY_SENT + [
        ('result', 'final', 'exchange_final'), ('state', 'filled'),
        ('decision', 'protect'), ('intent', 'protect', 'algo'), ('state', 'submitted'),
        ('result', 'known', None), ('state', 'working')]
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    assert h.sink.snapshot().lots == ((lot, str(decision.quantity)),)
    assert h.sink.snapshot().pending == ()


def test_journal_recovers_clean_after_a_protected_entry(h, tmp_path):
    h.enter()
    h.drive()
    h.close()
    again = recover_journal(h.dir, A, pf(1))
    assert again.verdict is Verdict.CLEAN
    state = again.journal.state()
    assert [v.state for v in state.intents] == [IntentState.WORKING]
    # NC-02 asks for a venue check of the resting stop after a restart; nothing is durable-but-unsent.
    assert state.needs_venue_query == (state.intents[0].intent_id,) and state.durable_not_sent == ()
    again.journal.close()


def test_restart_rebuilds_the_lot_without_a_problem_for_a_working_stop(h):
    h.enter()
    h.drive()
    lots = h.sink.snapshot().lots
    h.close()
    h.open()
    snap = h.sink.snapshot()
    assert snap.lots == lots and snap.problems == ()


def test_a_consumed_signal_is_never_sent_twice(h):
    h.enter()
    h.drive()
    accepted, _ = h.enter()
    assert accepted is False and 'signal_consumed' in h.sink.snapshot().log
    assert [c[0] for c in h.venue.calls].count('submit_market') == 1


def test_second_entry_waits_while_an_order_is_in_flight(h):
    h.enter()
    other = K.decision_key('trend_ema_mom', 'v1', '4h', 'BTCUSDT', Side.LONG, CANDLE + 14_400_000, Purpose.ENTRY)
    accepted, _ = h.enter(key=other)
    assert accepted is False and 'entry_busy' in h.sink.snapshot().log


def test_rejected_algo_stop_flattens_the_lot_and_holds(h):
    h.venue.stop = {'algo': 'rejected'}
    accepted, decision = h.enter()
    problems = h.drive()
    assert problems == (SinkProblem.PROTECTION_FAILED,)
    flatten = h.venue.calls[-1]
    assert flatten[:4] == ('submit_market', 'classic', True, decision.quantity)
    assert h.kinds()[-5:] == [('decision', 'flatten'), ('intent', 'close', 'classic'), ('state', 'submitted'),
                              ('result', 'final', 'exchange_final'), ('state', 'filled')]
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    assert h.sink.snapshot().lots == ((lot, '0'),)
    # A held sink accepts nothing new.
    other = K.decision_key('trend_ema_mom', 'v1', '4h', 'BTCUSDT', Side.LONG, CANDLE + 14_400_000, Purpose.ENTRY)
    assert h.enter(key=other)[0] is False


def test_partial_entry_fill_protects_only_the_executed_quantity(h):
    h.venue.market = 'partial'
    _, decision = h.enter()
    h.drive()
    stops = [c for c in h.venue.calls if c[0] == 'submit_stop']
    assert {c[3] for c in stops} == {decision.quantity / 2}
    assert ('state', 'cancelled') in h.kinds()


def test_rejected_entry_opens_no_lot_and_sends_no_stop(h):
    h.venue.market = 'rejected'
    h.enter()
    assert h.drive() == ()
    assert [c[0] for c in h.venue.calls] == ['submit_market']
    assert h.kinds()[-2:] == [('result', 'final', 'exchange_refused'), ('state', 'rejected')]
    assert h.sink.snapshot().lots == ()


# ------------------------------------------------------------------------------------------------ unresolved answers
@pytest.mark.parametrize('answer', ['unknown', 'not_found', 'raise', 'echo'])
def test_an_unresolved_answer_is_journaled_unknown_and_holds(h, answer):
    h.venue.market = answer
    h.enter()
    assert h.drive() == (SinkProblem.ORDER_UNRESOLVED,)
    assert h.kinds()[-2:] == [('result', 'unknown', None), ('state', 'unknown')]
    assert [c[0] for c in h.venue.calls] == ['submit_market']
    events = h.journal.read()
    if answer == 'not_found':
        assert events[-2].result.lookup.value == 'not_found'


def test_work_that_never_answers_is_cancelled_and_held(h):
    h.enter()
    h.context.run_next = lambda: None                    # the worker never runs it
    h.mono.advance(25_000)
    assert h.sink.poll(h.mono()) == (SinkProblem.ORDER_UNRESOLVED,)
    assert h.venue.calls == []
    assert h.kinds()[-1] == ('state', 'unknown')


def test_expired_work_is_never_assumed_unsent(h):
    h.enter()
    h.mono.advance(10_001)                               # NC-04 expires it before dequeue
    assert h.drive() == (SinkProblem.ORDER_UNRESOLVED,)
    assert h.venue.calls == [] and h.kinds()[-1] == ('state', 'unknown')


def test_restart_with_an_unanswered_order_reports_it(h):
    h.enter()
    h.close()
    h.open()
    assert SinkProblem.RECOVERED_LIVE_INTENTS in h.sink.snapshot().problems
    assert h.enter(key=K.decision_key('trend_ema_mom', 'v1', '4h', 'BTCUSDT', Side.LONG, CANDLE + 14_400_000,
                                      Purpose.ENTRY))[0] is False


# ------------------------------------------------------------------------------------------------ journal failures
class FailingJournal:
    def __init__(self, real, fail_at):
        self.real, self.fail_at, self.appends = real, fail_at, 0

    def append(self, event):
        self.appends += 1
        if self.appends == self.fail_at:
            raise DurabilityUnavailable('write', 'x')
        return self.real.append(event)

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.mark.parametrize('fail_at', [1, 2, 3])
def test_a_journal_failure_before_send_queues_nothing(tmp_path, fail_at):
    h = Harness(tmp_path)
    h.sink = JournalingActionSink()
    h.sink.bind(SinkBinding(account_id=A, aggregate_id=pf(1), journal=FailingJournal(h.journal, fail_at),
                            submit=h._submit, result=h.context.result, cancel=h.context.cancel,
                            wall_clock=h.wall, order_deadline_ms=10_000), h.mono)
    accepted, _ = h.enter()
    assert accepted is False and h.submitted == [] and h.venue.calls == []
    assert h.sink.poll(h.mono()) == (SinkProblem.ORDER_JOURNAL_FAILED,)
    h.close()


def test_submit_refusal_after_sent_is_unresolved(tmp_path):
    h = Harness(tmp_path)

    def refuse(work):
        raise RuntimeError('scheduler_stopping')

    h.sink = JournalingActionSink()
    h.sink.bind(SinkBinding(account_id=A, aggregate_id=pf(1), journal=h.journal, submit=refuse,
                            result=h.context.result, cancel=h.context.cancel, wall_clock=h.wall,
                            order_deadline_ms=10_000), h.mono)
    assert h.enter()[0] is False
    assert h.kinds() == ENTRY_SENT and h.sink.poll(h.mono()) == (SinkProblem.ORDER_UNRESOLVED,)
    h.close()


# ------------------------------------------------------------------------------------------------ management
def _plan_for(lot_id, qty, **evaluation_changes):
    row = _evaluation(**evaluation_changes)
    row = dataclasses.replace(row, lot=dataclasses.replace(row.lot, lot_id=lot_id, quantity=qty, initial_quantity=qty))
    if row.protection.status is ProtectionStatus.WORKING:
        row = dataclasses.replace(row, protection=dataclasses.replace(row.protection, confirmed_quantity=qty))
    return nc07.evaluate(row)


def _exit_plan(lot_id, qty):
    row = _evaluation()
    row = dataclasses.replace(row, lot=dataclasses.replace(row.lot, lot_id=lot_id, quantity=qty, initial_quantity=qty),
                              protection=dataclasses.replace(row.protection, confirmed_quantity=qty),
                              strategy_exit=dataclasses.replace(row.strategy_exit, exit_requested=True))
    plan = nc07.evaluate(row)
    assert [type(a) for a in plan.actions] == [nc07.CloseAction], plan
    return plan


def test_management_close_is_refused_while_the_stop_rests(h):
    _, decision = h.enter()
    h.drive()
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    seq = h.journal.last_sequence()
    # Review F2: a close would leave the resting stop larger than the position; it needs the cancel path first.
    assert h.sink.accept_management(A, _exit_plan(lot, decision.quantity)) is False
    assert h.journal.last_sequence() == seq


def test_management_close_is_journaled_and_sent_once_no_stop_rests(h):
    _, decision = h.enter()
    h.drive()
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    h.sink._lots[lot].last_protect = ('algo', True, IntentState.CANCELLED)   # as after a (future) cancel path
    before = len(h.venue.calls)
    assert h.sink.accept_management(A, _exit_plan(lot, decision.quantity)) is True
    assert h.kinds()[-3:] == [('decision', 'close'), ('intent', 'close', 'classic'), ('state', 'submitted')]
    assert h.drive() == ()
    assert h.venue.calls[before][:4] == ('submit_market', 'classic', True, decision.quantity)
    assert h.sink.snapshot().lots == ((lot, '0'),)


def test_management_for_an_unknown_lot_is_refused(h):
    assert h.sink.accept_management(A, _exit_plan('lot_' + 'f' * 32, D('10'))) is False
    assert h.journal.last_sequence() == 0


def test_protection_repair_while_a_stop_is_working_is_refused(h):
    _, decision = h.enter()
    h.drive()
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    plan = _plan_for(lot, decision.quantity, protection_status=ProtectionStatus.MISSING)
    assert [type(a) for a in plan.actions] == [nc07.ProtectionRepair]
    seq = h.journal.last_sequence()
    assert h.sink.accept_management(A, plan) is False and h.journal.last_sequence() == seq


def test_management_while_the_lot_has_an_order_in_flight_sends_nothing_more(h):
    _, decision = h.enter()
    while h.context.run_next() is not None:
        pass
    h.sink.poll(h.mono())                                # entry folded, protect queued, not yet sent
    lot = K.derive_lot_id(A, K.derive_intent_id(A, KEY))
    seq = h.journal.last_sequence()
    assert h.sink.accept_management(A, _exit_plan(lot, decision.quantity)) is True
    assert h.journal.last_sequence() == seq


# ------------------------------------------------------------------------------------------------ through the runtime
class RuntimeVenue(OrderVenue):
    """Scripted reads for REC-02 refreshes plus scripted order answers."""

    def __init__(self):
        super().__init__(None)
        from rt01_fixtures import ScriptedVenue
        self.reads = ScriptedVenue()

    def submit_market(self, order):
        self.calls.append(('submit_market', order.ref.route, order.reduce, order.qty, None))
        return self._answer(order.ref, self.market, order.qty)

    def submit_stop(self, order):
        self.calls.append(('submit_stop', order.ref.route, order.stop_price, order.qty, None))
        return self._answer(order.ref, self.stop[order.ref.route], order.qty)

    def positions(self, symbol=None):
        return self.reads.positions(symbol)

    def open_orders(self, symbol=None):
        return self.reads.open_orders(symbol)


class Keyed:
    def __init__(self):
        self.calls = 0

    def candidates(self, account_id, now_mono):
        self.calls += 1
        if self.calls > 1:
            return ()
        from newcore.runtime import EntryCandidate
        return (EntryCandidate(key=KEY, evaluation=risk_evaluation()),)


@pytest.fixture
def world(tmp_path):
    from rt01_fixtures import World
    made = []

    def make(**kwargs):
        w = World(tmp_path, 1, **kwargs)
        made.append(w)
        return w

    yield make
    for w in made:
        try:
            w.shutdown()
        except Exception:
            pass


def _runtime_world(world):
    from rt01_fixtures import account_view  # noqa: F401
    w = world(entries_enabled=True)
    venue = RuntimeVenue()
    w.venues[A] = venue
    sink = JournalingActionSink()
    w.extra_ports[A] = {'entries': Keyed(), 'actions': sink}
    return w, venue, sink


def test_runtime_routes_a_keyed_entry_and_protects_it(world):
    w, venue, sink = _runtime_world(world)
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    w.settle(lambda s: any(e.code == 'entry_routed' for e in s.events), advance_ms=5_000)
    w.settle(lambda s: sink.snapshot().log[-1:] == ('working:protect',), advance_ms=1_000)
    assert [c[:2] for c in venue.calls] == [('submit_market', 'classic'), ('submit_stop', 'algo')]
    from rt01_fixtures import account_view
    assert account_view(w.runtime.snapshot(), A).holds == ()


def test_runtime_without_a_stop_route_routes_no_entry(world, no_algo_stop):
    w, venue, sink = _runtime_world(world)
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    w.settle(lambda s: any(e.code == 'entry_unrouted' for e in s.events), advance_ms=5_000)
    assert venue.calls == [] and sink.snapshot().pending == ()
    assert w.runtime._accounts[A].journal.last_sequence() == 0


def test_runtime_turns_a_sink_problem_into_a_sticky_hold(world):
    w, venue, sink = _runtime_world(world)
    venue.market = 'unknown'
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    from rt01_fixtures import account_view
    snap = w.settle(lambda s: 'order_unresolved' in account_view(s, A).holds, advance_ms=5_000)
    assert not snap.scheduler.ready
