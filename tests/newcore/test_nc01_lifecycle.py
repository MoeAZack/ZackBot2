"""NC-01 lifecycles (contract invariants 5-7): every allowed / forbidden step of the order-intent, result-durability and
binding state machines, and the durable-before-send / durable-before-apply chain rules."""
from decimal import Decimal as D
from itertools import product

import pytest

import nc01_factories as F
from nc01_factories import T0, replace
from newcore.domain import (BindingState, DecisionRecorded, Evidence, IntentRecorded, IntentState, IntentStateChanged,
                            InvalidRecord, ModeChanged, PositionRead, ReasonCode, ResultObserved, ResultStage,
                            check_event_chain, may_apply, may_send, terminal_for)
from newcore.domain.account import BINDING_TRANSITIONS
from newcore.domain.decision import Action
from newcore.domain.modes import EntriesMode
from newcore.domain.orders import INTENT_TRANSITIONS, LIVE, PREDECESSORS, RESULT_STAGE_TRANSITIONS, TERMINAL

S = IntentState
# The pinned table (written out independently of the implementation): state -> allowed next states
EXPECTED = {
    S.PLANNED: {S.DURABLE, S.NOT_SENT},
    S.DURABLE: {S.SUBMITTED, S.CANCELLING, S.NOT_SENT, S.FILLED},     # FILLED: r3 post-hoc booking (exchange_external)
    S.SUBMITTED: {S.WORKING, S.UNKNOWN, S.CANCELLING, S.FILLED, S.CANCELLED, S.REJECTED},
    S.WORKING: {S.UNKNOWN, S.CANCELLING, S.FILLED, S.CANCELLED},
    S.UNKNOWN: {S.WORKING, S.CANCELLING, S.FILLED, S.CANCELLED, S.REJECTED},
    S.CANCELLING: {S.WORKING, S.UNKNOWN, S.FILLED, S.CANCELLED, S.NOT_SENT},
    S.FILLED: set(), S.CANCELLED: set(), S.REJECTED: set(), S.NOT_SENT: set(),
}


@pytest.mark.parametrize('a,b', list(product(IntentState, IntentState)), ids=lambda s: s.value)
def test_every_intent_step_is_allowed_or_forbidden(a, b):
    allowed = b in EXPECTED[a]
    assert (b in INTENT_TRANSITIONS[a]) is allowed
    ev = lambda: F.event(IntentStateChanged, F.Ids(2), F.Ids(1).id('acct'), 1, intent_id=F.Ids(2).id('int'),
                         from_state=a, to_state=b)
    if allowed:
        ev()
    else:
        with pytest.raises(InvalidRecord):
            ev()


def test_terminal_states_never_return():
    assert TERMINAL == {S.FILLED, S.CANCELLED, S.REJECTED, S.NOT_SENT}
    for t in TERMINAL:
        assert INTENT_TRANSITIONS[t] == frozenset()
    assert not any(S.PLANNED in nxt for nxt in INTENT_TRANSITIONS.values())         # nothing becomes "planned" again
    assert not any(S.DURABLE in INTENT_TRANSITIONS[s] for s in IntentState if s is not S.PLANNED)
    for s in IntentState:                                                            # finite predecessor sets
        assert PREDECESSORS[s] == {a for a in IntentState if s in EXPECTED[a]}
    assert LIVE == {S.DURABLE, S.SUBMITTED, S.WORKING, S.UNKNOWN, S.CANCELLING}


def test_durability_gates():
    ids = F.Ids(8)
    it = F.market_entry(ids, ids.id('acct'))
    assert [s for s in IntentState if may_send(replace(it, state=s))] == [S.DURABLE]
    assert [s for s in ResultStage if may_apply(s)] == [ResultStage.DURABLE]
    assert RESULT_STAGE_TRANSITIONS == {ResultStage.RECEIVED: {ResultStage.DURABLE},
                                        ResultStage.DURABLE: {ResultStage.APPLIED}, ResultStage.APPLIED: set()}


def test_terminal_for_results():
    ids = F.Ids(9)
    acct = ids.id('acct')
    r = F.results(ids, acct, F.market_entry(ids, acct))
    assert {k: terminal_for(v) for k, v in r.items()} == {
        'unknown': None, 'not_found': None, 'known': None, 'filled': S.FILLED, 'partial_cancel': S.CANCELLED,
        'refused': S.REJECTED, 'corroborated': S.CANCELLED, 'adopted': S.FILLED}


@pytest.mark.parametrize('a,b', list(product(BindingState, BindingState)), ids=lambda s: s.value)
def test_binding_steps(a, b):
    expected = {(BindingState.UNCONFIRMED, BindingState.CONFIRMED), (BindingState.CONFIRMED, BindingState.MISMATCH),
                (BindingState.CONFIRMED, BindingState.ROTATION_PENDING), (BindingState.MISMATCH, BindingState.CONFIRMED),
                (BindingState.MISMATCH, BindingState.ROTATION_PENDING), (BindingState.ROTATION_PENDING, BindingState.CONFIRMED),
                (BindingState.ROTATION_PENDING, BindingState.RECONCILING), (BindingState.RECONCILING, BindingState.CONFIRMED)}
    assert (b in BINDING_TRANSITIONS[a]) is ((a, b) in expected)


# ----------------------------------------------------------------------------------------------------------- chain
class Log:
    """Builds a consistent event log for one account."""

    def __init__(self, seed=21):
        self.ids = F.Ids(seed)
        self.acct = self.ids.id('acct')
        self.events = []

    def add(self, cls, at=T0, **kw):
        self.events.append(F.event(cls, self.ids, self.acct, len(self.events) + 1, at=at, **kw))

    def record(self, intent, at=T0):
        self.add(IntentRecorded, at=at, intent=replace(intent, state=S.DURABLE), reason=intent.reason)

    def step(self, intent, a, b, at=T0 + 10):
        self.add(IntentStateChanged, at=at, intent_id=intent.intent_id, from_state=a, to_state=b)

    def result(self, res, at=T0 + 40_000):
        self.add(ResultObserved, at=at, result=res)

    def renumber(self, first=1):
        self.events = [replace(e, sequence=first + i) for i, e in enumerate(self.events)]


def _entry(log, **kw):
    return F.market_entry(log.ids, log.acct, **kw)


def test_happy_path_record_submit_result_apply():
    log = Log()
    it = _entry(log)
    res = F.results(log.ids, log.acct, it)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED, at=T0)
    log.step(it, S.SUBMITTED, S.UNKNOWN)
    log.result(res['not_found'])                     # bare not-found: recorded, resolves nothing
    log.result(res['filled'])
    log.step(it, S.UNKNOWN, S.FILLED)
    assert check_event_chain(log.events) == {}


def test_result_for_an_intent_never_made_durable():
    log = Log()
    it = _entry(log)
    log.result(F.results(log.ids, log.acct, it)['filled'])
    with pytest.raises(InvalidRecord, match='never made durable'):
        check_event_chain(log.events)


def test_terminal_step_needs_a_durable_final_result_first():
    log = Log()
    it = _entry(log)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED)
    log.step(it, S.SUBMITTED, S.FILLED)              # applied with no ResultObserved before it
    with pytest.raises(InvalidRecord, match='durable FINAL result first'):
        check_event_chain(log.events)


def test_terminal_step_must_match_the_result():
    log = Log()
    it = _entry(log)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED)
    log.result(F.results(log.ids, log.acct, it)['refused'])
    log.step(it, S.SUBMITTED, S.FILLED)
    with pytest.raises(InvalidRecord, match='final result means rejected'):
        check_event_chain(log.events)


def test_nothing_follows_a_final_result_but_its_terminal_step():
    log = Log()
    it = _entry(log)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED)
    log.result(F.results(log.ids, log.acct, it)['filled'])
    log.step(it, S.SUBMITTED, S.WORKING)
    with pytest.raises(InvalidRecord, match='only its terminal step'):
        check_event_chain(log.events)


def test_no_event_after_a_terminal_state():
    log = Log()
    it = _entry(log)
    res = F.results(log.ids, log.acct, it)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED)
    log.result(res['filled'])
    log.step(it, S.SUBMITTED, S.FILLED)
    log.result(res['refused'])
    with pytest.raises(InvalidRecord, match='event after'):
        check_event_chain(log.events)


def test_armed_trailing_entry_drops_as_not_sent():
    log = Log()
    tr = F.trailing_entry(log.ids, log.acct)
    log.record(tr)
    log.step(tr, S.DURABLE, S.CANCELLING)
    log.result(replace(F.results(log.ids, log.acct, tr)['refused'], evidence=Evidence.NOT_SENT))
    log.step(tr, S.CANCELLING, S.NOT_SENT)
    assert check_event_chain(log.events) == {}


def test_a_submitted_intent_can_never_end_not_sent():
    log = Log()
    it = _entry(log)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED)
    log.result(replace(F.results(log.ids, log.acct, it)['refused'], evidence=Evidence.NOT_SENT))
    with pytest.raises(InvalidRecord, match='was submitted'):
        check_event_chain(log.events)


def test_not_found_corroboration_inside_the_window_proves_nothing():
    log = Log()
    it = _entry(log)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED, at=T0 + 20_000)       # sent later: the factory's reads are now too early
    log.result(F.results(log.ids, log.acct, it)['corroborated'])
    with pytest.raises(InvalidRecord, match='visibility window'):
        check_event_chain(log.events)


def test_sequence_gap_other_account_and_reused_client_id():
    log = Log()
    it = _entry(log)
    log.record(it)
    with pytest.raises(InvalidRecord, match='expected sequence 1'):
        check_event_chain([replace(log.events[0], sequence=2)])
    with pytest.raises(InvalidRecord, match='expected sequence 6'):
        check_event_chain([replace(log.events[0], sequence=7)], after_sequence=5)
    other = Log(99)
    oi = replace(_entry(other), state=S.DURABLE)
    foreign = F.event(IntentRecorded, other.ids, other.acct, 2, intent=oi, reason=oi.reason)
    with pytest.raises(InvalidRecord, match='another account'):
        check_event_chain(log.events + [foreign])
    same_acct_other_aggregate = replace(log.events[0], sequence=2, event_id=log.ids.id('evt'),
                                        aggregate_id=F.Ids(5).id('pf'))
    with pytest.raises(InvalidRecord, match='aggregate'):
        check_event_chain(log.events + [same_acct_other_aggregate])
    log.record(replace(_entry(log), client_order_id=it.client_order_id))
    with pytest.raises(InvalidRecord, match='never reused'):
        check_event_chain(log.events)


def test_one_shot_authorization_must_exist_and_is_single_use():
    log = Log()
    shot = F.decision_with_intents(log.ids, log.acct, Action.PAUSE, ReasonCode.OPERATOR_PAUSE)
    shot = replace(shot, reason=ReasonCode.OPERATOR_ONE_SHOT)
    first = _entry(log, reason=ReasonCode.ENTRY_ONE_SHOT, authorized_by=shot.decision_id)
    log.record(first)
    with pytest.raises(InvalidRecord, match='no recorded operator one-shot'):
        check_event_chain(log.events)
    log.events.insert(0, F.event(DecisionRecorded, log.ids, log.acct, 1, decision=shot, reason=shot.reason))
    log.renumber()
    assert first.intent_id in check_event_chain(log.events)
    log.record(_entry(log, reason=ReasonCode.ENTRY_ONE_SHOT, authorized_by=shot.decision_id))
    with pytest.raises(InvalidRecord, match='authorizes one intent'):
        check_event_chain(log.events)


def test_live_intents_carry_over_from_a_snapshot():
    log = Log()
    it = _entry(log)
    known = {it.intent_id: (it, S.UNKNOWN, T0)}
    log.add(ResultObserved, at=T0 + 40_000, result=F.results(log.ids, log.acct, it)['filled'])
    log.add(IntentStateChanged, at=T0 + 40_001, intent_id=it.intent_id, from_state=S.UNKNOWN, to_state=S.FILLED)
    log.renumber(11)
    assert check_event_chain(log.events, after_sequence=10, known_intents=known) == {}


def test_mode_change_rules_are_explicit():
    acct = F.Ids(3).id('acct')
    ids = F.Ids(4)
    F.event(ModeChanged, ids, acct, 1, from_mode=EntriesMode.HOLD, to_mode=EntriesMode.PAUSED, from_hold=F.HoldKind.NORMAL,
            reasons=(ReasonCode.OPERATOR_PAUSE,), reason=ReasonCode.OPERATOR_PAUSE, reconciliation_id=ids.id('rec'))
    F.event(ModeChanged, ids, acct, 1, from_mode=EntriesMode.HOLD, to_mode=EntriesMode.HOLD,
            from_hold=F.HoldKind.DURABILITY_UNAVAILABLE, to_hold=F.HoldKind.NORMAL,
            reasons=(ReasonCode.RECONCILE_UNRECONCILED,), reason=ReasonCode.RECONCILE_UNRECONCILED)
    F.event(ModeChanged, ids, acct, 1, from_mode=EntriesMode.PAUSED, to_mode=EntriesMode.ACTIVE, reasons=(),
            reason=ReasonCode.OPERATOR_RESUME, decision_id=ids.id('dec'))
    assert PositionRead(at_ms=T0, qty=D('0')).qty == 0


# ----------------------------------------------------------------------------------------------------------- admission
def _cursor(log):
    from newcore.domain import EventCursor
    return EventCursor(account_id=log.acct, aggregate_id=F.pf_id(log.acct), last_sequence=0, applied=())


def test_events_apply_only_in_exact_sequence_and_replay_idempotently():
    """Contract invariant 12: next sequence applies; the same event_id with identical canonical bytes is a no-op."""
    from newcore.domain import Admission, admit
    log = Log()
    it = _entry(log)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED)
    cur = _cursor(log)
    for ev in log.events:
        cur, how = admit(cur, ev)
        assert how is Admission.APPLY
    assert cur.last_sequence == 2
    for ev in log.events:                                          # crash replay: same events again
        again, how = admit(cur, ev)
        assert how is Admission.ALREADY_APPLIED and again == cur


def test_admission_rejects_reuse_gaps_reorders_and_foreign_events():
    from newcore.domain import EventOrderError, admit
    log = Log()
    it = _entry(log)
    log.record(it)
    log.step(it, S.DURABLE, S.SUBMITTED)
    log.step(it, S.SUBMITTED, S.WORKING, at=T0 + 20)
    e1, e2, e3 = log.events
    cur, _ = admit(_cursor(log), e1)
    with pytest.raises(EventOrderError, match='a gap'):
        admit(cur, e3)
    cur, _ = admit(cur, e2)
    with pytest.raises(EventOrderError, match='different bytes'):     # same id, different canonical bytes
        admit(cur, replace(e2, at_ms=e2.at_ms + 1))
    with pytest.raises(EventOrderError, match='different bytes'):     # same id at another sequence
        admit(cur, replace(e2, sequence=3))
    with pytest.raises(EventOrderError, match='already-used sequence'):
        admit(cur, replace(e2, event_id=log.ids.id('evt')))           # a different event claims sequence 2
    with pytest.raises(EventOrderError, match='another account'):
        admit(cur, replace(e3, aggregate_id=F.Ids(6).id('pf')))
    cur, _ = admit(cur, e3)
    assert cur.last_sequence == 3


def test_event_cursor_is_itself_validated():
    from newcore.domain import EventCursor, EventDigest
    log = Log()
    d = EventDigest(event_id=log.ids.id('evt'), sequence=2, sha256='0' * 64)
    with pytest.raises(InvalidRecord):
        EventCursor(account_id=log.acct, aggregate_id=F.pf_id(log.acct), last_sequence=1, applied=(d,))
    with pytest.raises(InvalidRecord):
        EventDigest(event_id=log.ids.id('evt'), sequence=1, sha256='ABC')
