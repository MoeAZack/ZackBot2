"""MemoryJournal: grammar G1-G9 + NC-01 chain on append, append-only, identical re-append idempotence."""
import dataclasses
from decimal import Decimal as D

import pytest

from newcore.adapters.memory_journal import MemoryJournal, header_of
from newcore.domain import (Action, Authority, Decision, DecisionKey, DecisionRecorded, IntentRecorded, IntentState,
                            IntentStateChanged, Purpose, ReasonCode, ResultObserved, Side)
from newcore.ports import Admission, EventKind, JournalConflict, JournalPort, JournalUnavailable
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind
from newcore.runner import ids
from newcore.runner.records import planned_intent, result_from
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, T0

AT = T0 + 280 * H4
KEY = DecisionKey(strategy='trend_ema_mom@4h', strategy_version='v1', symbol='SOLUSDT', side=Side.LONG,
                  candle_close_ms=AT, purpose=Purpose.ENTRY)
DEC_ID = ids.derive_decision_id(ACCOUNT_ID, KEY)
INT_ID = ids.derive_intent_id(ACCOUNT_ID, KEY, 0)


def planned():
    return planned_intent(intent_id=INT_ID, account_id=ACCOUNT_ID, decision_id=DEC_ID, purpose='entry',
                          symbol='SOLUSDT', side='LONG', qty=D('5'), reason=ReasonCode.ENTRY_SIGNAL, at_ms=AT,
                          slot_id='S1')


def ev(cls, seq, **kw):
    return cls(event_id=ids.event_id(PORTFOLIO_ID, seq), account_id=ACCOUNT_ID, aggregate_id=PORTFOLIO_ID,
               sequence=seq, at_ms=AT, **kw)


def chain():
    p = planned()
    dec = Decision(decision_id=DEC_ID, account_id=ACCOUNT_ID, at_ms=AT, action=Action.ENTER,
                   reason=ReasonCode.ENTRY_SIGNAL, authority=Authority.STRATEGY, key=KEY, evidence=(), symbol='SOLUSDT',
                   side=Side.LONG, subject_id=None, detail='', intents=(p,), policy_version='s1')
    durable = dataclasses.replace(p, state=IntentState.DURABLE)
    out = OrderOutcome(kind=OutcomeKind.FINAL, ref=OrderRef(symbol='SOLUSDT', client_id=p.client_order_id),
                       observed_at_ms=AT, status='FILLED', exchange_order_id='1', executed_qty=D('5'),
                       avg_price=D('100.02'))
    res = result_from(out, durable, ids.result_id(INT_ID, 0), submit=True)
    r = ReasonCode.ENTRY_SIGNAL
    return [ev(DecisionRecorded, 1, reason=r, decision=dec),
            ev(IntentRecorded, 2, reason=r, intent=durable),
            ev(IntentStateChanged, 3, reason=r, intent_id=INT_ID, from_state=IntentState.DURABLE,
               to_state=IntentState.SUBMITTED),
            ev(ResultObserved, 4, reason=r, result=res),
            ev(IntentStateChanged, 5, reason=r, intent_id=INT_ID, from_state=IntentState.SUBMITTED,
               to_state=IntentState.FILLED)]


def test_satisfies_the_port_and_projects_headers():
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    assert isinstance(j, JournalPort)
    kinds = [header_of(e).kind for e in chain()]
    assert kinds == [EventKind.DECISION_RECORDED, EventKind.INTENT_RECORDED, EventKind.SENT, EventKind.RESULT_RECORDED,
                     EventKind.INTENT_CLOSED]
    assert header_of(chain()[0]).decision_key.canonical_bytes() == KEY_BYTES()


def KEY_BYTES():
    from newcore.domain import canonical_bytes
    return canonical_bytes(KEY)


def test_full_lifecycle_applies_and_is_idempotent():
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    evs = chain()
    assert [j.append(e) for e in evs] == [Admission.APPLY] * 5
    assert j.last_sequence() == 5
    assert j.append(evs[3]) is Admission.ALREADY_APPLIED           # identical re-append: no-op
    assert j.last_sequence() == 5 and len(j.read()) == 5
    assert j.read(3) == tuple(evs[3:])
    assert j.find_decision(DEC_ID) is evs[0]
    assert j.is_consumed(KEY)


def test_reused_event_id_with_other_bytes_is_a_conflict():
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    evs = chain()
    for e in evs[:3]:
        j.append(e)
    other = dataclasses.replace(evs[2], at_ms=AT + 1)
    with pytest.raises(JournalConflict):
        j.append(other)
    assert j.last_sequence() == 3


@pytest.mark.parametrize('bad', ['gap', 'result_before_intent', 'decision_twice', 'close_before_result'])
def test_grammar_breaks_are_refused_and_change_nothing(bad):
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    evs = chain()
    j.append(evs[0])
    if bad == 'gap':
        e = dataclasses.replace(evs[2], sequence=3, event_id=ids.event_id(PORTFOLIO_ID, 3))
    elif bad == 'result_before_intent':
        e = dataclasses.replace(evs[3], sequence=2, event_id=ids.event_id(PORTFOLIO_ID, 2))
    elif bad == 'decision_twice':
        e = dataclasses.replace(evs[0], sequence=2, event_id=ids.event_id(PORTFOLIO_ID, 2))
    else:
        j.append(evs[1])
        j.append(evs[2])
        e = dataclasses.replace(evs[4], sequence=4, event_id=ids.event_id(PORTFOLIO_ID, 4))
    before = j.read()
    with pytest.raises(JournalConflict):
        j.append(e)
    assert j.read() == before


def test_grammar_alone_also_refuses_when_the_chain_check_is_off():
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID, check_chain=False)
    evs = chain()
    j.append(evs[0])
    with pytest.raises(JournalConflict):
        j.append(dataclasses.replace(evs[3], sequence=2, event_id=ids.event_id(PORTFOLIO_ID, 2)))


def test_store_failure_raises_unavailable_and_lands_nothing():
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    j.fail_writes(1)
    with pytest.raises(JournalUnavailable):
        j.append(chain()[0])
    assert j.last_sequence() == 0
    assert j.append(chain()[0]) is Admission.APPLY


def test_other_aggregate_is_refused():
    j = MemoryJournal(ACCOUNT_ID, 'pf_' + '9' * 32)
    with pytest.raises(JournalConflict):
        j.append(chain()[0])
