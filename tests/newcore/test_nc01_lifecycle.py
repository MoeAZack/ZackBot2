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
    S.DURABLE: {S.SUBMITTED, S.CANCELLING, S.NOT_SENT},
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
    ev = lambda: IntentStateChanged(seq=1, account_id=F.Ids(1).id('acct'), at_ms=T0, intent_id=F.Ids(2).id('int'),
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

    def add(self, cls, **kw):
        self.events.append(cls(seq=len(self.events) + 1, account_id=self.acct, **kw))

    def record(self, intent, at=T0):
        self.add(IntentRecorded, at_ms=at, intent=replace(intent, state=S.DURABLE))

    def step(self, intent, a, b, at=T0 + 10):
        self.add(IntentStateChanged, at_ms=at, intent_id=intent.intent_id, from_state=a, to_state=b)

    def result(self, res, at=T0 + 40_000):
        self.add(ResultObserved, at_ms=at, result=res)


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
    with pytest.raises(InvalidRecord, match='expected seq 1'):
        check_event_chain([replace(log.events[0], seq=2)])
    with pytest.raises(InvalidRecord, match='expected seq 6'):
        check_event_chain([replace(log.events[0], seq=7)], after_seq=5)
    other = Log(99)
    foreign = IntentRecorded(seq=2, account_id=other.acct, at_ms=T0, intent=replace(_entry(other), state=S.DURABLE))
    with pytest.raises(InvalidRecord, match='another account'):
        check_event_chain(log.events + [foreign])
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
    log.events.insert(0, DecisionRecorded(seq=1, account_id=log.acct, at_ms=T0, decision=shot))
    log.events[1] = replace(log.events[1], seq=2)
    assert first.intent_id in check_event_chain(log.events)
    log.record(_entry(log, reason=ReasonCode.ENTRY_ONE_SHOT, authorized_by=shot.decision_id))
    with pytest.raises(InvalidRecord, match='authorizes one intent'):
        check_event_chain(log.events)


def test_live_intents_carry_over_from_a_snapshot():
    log = Log()
    it = _entry(log)
    known = {it.intent_id: (it, S.UNKNOWN, T0)}
    log.add(ResultObserved, at_ms=T0 + 40_000, result=F.results(log.ids, log.acct, it)['filled'])
    log.events[0] = replace(log.events[0], seq=11)
    log.add(IntentStateChanged, at_ms=T0 + 40_001, intent_id=it.intent_id, from_state=S.UNKNOWN, to_state=S.FILLED)
    log.events[1] = replace(log.events[1], seq=12)
    assert check_event_chain(log.events, after_seq=10, known_intents=known) == {}


def test_mode_change_rules_are_explicit():
    acct = F.Ids(3).id('acct')
    ModeChanged(seq=1, account_id=acct, at_ms=T0, from_mode=EntriesMode.HOLD, to_mode=EntriesMode.PAUSED,
                from_hold=F.HoldKind.NORMAL, reasons=(ReasonCode.OPERATOR_PAUSE,), reconciliation_id=F.Ids(4).id('rec'))
    ModeChanged(seq=1, account_id=acct, at_ms=T0, from_mode=EntriesMode.HOLD, to_mode=EntriesMode.HOLD,
                from_hold=F.HoldKind.DURABILITY_UNAVAILABLE, to_hold=F.HoldKind.NORMAL,
                reasons=(ReasonCode.RECONCILE_UNRECONCILED,))
    assert PositionRead(at_ms=T0, qty=D('0')).qty == 0
