"""Step-0 journal: header_of over real NC-01 events, the Grammar / JournalGate, the consumed-signal rule, and the
JournalPort contract suite run against the reference in-memory journal (STEP0_INTERFACE.md sections 2-3)."""
import pytest

from journal_contract import BAD, GOOD, JournalContract, _flow, flow_fallback, flow_fill_and_protect
from nc_events import ACCT, ENTRY, KEY, LOT, PF, Scenario
from newcore.domain import IntentState, Purpose, Side
from newcore.domain.events import EVENT_TYPES
from newcore.domain.errors import InvalidRecord
from newcore.ports import keys as K
from newcore.ports.journal import (Admission, EventKind, JournalConflict, JournalGate, JournalUnavailable,
                                   ResultOutcome, claim_signal, header_of)


class ReferenceJournal:
    """The reference JournalPort: in-memory list + JournalGate (what S1's MemoryJournal must behave like)."""

    def __init__(self, account_id=ACCT, aggregate_id=PF, events=()):
        self._gate = JournalGate.rebuild(account_id, aggregate_id, events)
        self._events = list(events)
        self._fail_next = False

    def append(self, event):
        staged = self._gate.stage(event)            # 1. validate only
        if staged is None:
            return Admission.ALREADY_APPLIED
        self._write(event)                          # 2. durable (raises JournalUnavailable: the gate never saw it)
        return staged.commit()                      # 3. only now consumed

    def _write(self, event):
        if self._fail_next:
            self._fail_next = False
            raise JournalUnavailable('injected write failure')
        self._events.append(event)                  # the durable write (write + fsync in a real store)

    def inject_write_failure(self):
        self._fail_next = True

    def last_sequence(self):
        return len(self._events)

    def read(self, after_sequence=0):
        return iter(self._events[after_sequence:])

    def find_decision(self, decision_id):
        return next((e for e in self._events if getattr(getattr(e, 'decision', None), 'decision_id', None)
                     == decision_id), None)

    def gate(self):
        return self._gate


class TestReferenceJournal(JournalContract):
    @pytest.fixture
    def make_journal(self):
        return ReferenceJournal

    @pytest.fixture
    def reopen(self):
        return lambda j: ReferenceJournal(events=list(j.read()))        # a restart = rebuild from the durable events

    @pytest.fixture
    def fail_next_write(self):
        return lambda j: j.inject_write_failure()


# ------------------------------------------------------------------------------------------------ header_of
def test_header_of_maps_every_nc01_event_type():
    s = _flow(flow_fallback)
    s.hold()
    kinds = [header_of(ev).kind for ev in s.events]
    assert kinds[:5] == [EventKind.DECISION_RECORDED, EventKind.INTENT_RECORDED, EventKind.SENT,
                         EventKind.RESULT_RECORDED, EventKind.INTENT_CLOSED]
    assert kinds[-1] is EventKind.MODE_CHANGED and EventKind.STATE_CHANGED in kinds
    assert len(EVENT_TYPES) == 7 and len(EventKind) == 9      # a new NC-01 event type must be mapped here first


def test_header_of_projects_identity_and_lineage():
    s = _flow(flow_fill_and_protect)
    dec, rec, sent, res = (header_of(e) for e in s.events[:4])
    assert dec.decision_key == KEY and dec.authorized == (ENTRY,)
    assert rec.owner_id is None and rec.client_ids == (K.client_id_for(ENTRY),) and rec.purpose is Purpose.ENTRY
    assert res.outcome is ResultOutcome.FINAL and res.client_ids == rec.client_ids
    stop = header_of(s.events[6])
    assert stop.owner_id == LOT and stop.intent_id == K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 0)
    with pytest.raises(InvalidRecord):
        header_of(object())


def test_not_found_projects_to_not_found():
    s = Scenario()
    s.entry_filled()
    sid = s.protect_attempt(0)
    assert header_of(s.result(sid, 'not_found')).outcome is ResultOutcome.NOT_FOUND
    assert header_of(s.result(sid, 'unknown')).outcome is ResultOutcome.UNKNOWN


# ------------------------------------------------------------------------------------------------ gate behaviour
def test_stage_changes_nothing_until_commit():
    s = _flow(flow_fill_and_protect)
    g = JournalGate(ACCT, PF)
    for ev in s.events:
        staged = g.stage(ev)
        assert g.grammar.last_sequence == ev.sequence - 1                 # staged, not consumed
        assert not g.grammar.is_consumed(KEY) or ev.sequence > 1
        assert g.stage(ev) is not None                                    # still new: no ALREADY_APPLIED yet
        assert staged.commit() is Admission.APPLY
    assert g.stage(s.events[0]) is None                                  # committed -> identical re-append


def test_staged_event_commits_once_and_never_after_the_gate_moved():
    s = _flow(flow_fill_and_protect)
    g = JournalGate(ACCT, PF)
    first, twin = g.stage(s.events[0]), g.stage(s.events[0])
    first.commit()
    with pytest.raises(InvalidRecord, match='already committed'):
        first.commit()
    with pytest.raises(InvalidRecord, match='the gate moved'):
        twin.commit()                                                     # a stale stage can never double-consume
    assert g.grammar.last_sequence == 1


def test_refused_event_leaves_the_gate_unchanged_and_the_right_event_still_applies():
    s = _flow(flow_fill_and_protect)
    g = JournalGate(ACCT, PF)
    for ev in s.events[:3]:
        g.admit(ev)
    with pytest.raises(JournalConflict):
        g.admit(s.events[4])                       # gap
    assert g.grammar.last_sequence == 3 and g.admit(s.events[3]) is Admission.APPLY


def test_good_and_bad_catalogues_are_complete():
    assert set(GOOD) == {'fill_and_protect', 'fallback', 'unknown_then_found', 'never_sent'}
    must = {'reused_sequence', 'wrong_parent', 'wrong_ordinal', 'wrong_id', 'fallback_after_unknown',
            'duplicate_route_send',
            'second_algo_after_algo_rejected', 'decision_authorizes_leg1', 'leg1_after_leg0_completed',
            'unauthorized_intent', 'signal_decided_twice', 'result_before_intent', 'reorder', 'gap'}
    assert must <= set(BAD)


# ------------------------------------------------------------------------------------------------ consumed signal
def test_claim_is_fresh_then_spent():
    g = JournalGate(ACCT, PF)
    first = claim_signal(g.grammar, KEY)
    assert first.fresh and first.intent_ids == (ENTRY,) and first.decision_id == K.derive_decision_id(ACCT, KEY)
    for ev in _flow(lambda s: s.entry_filled()).events:
        g.admit(ev)
    again = claim_signal(g.grammar, K.decision_key('trend_ema_mom', 'v1', '4h', 'SOLUSDT', Side.LONG,
                                                   KEY.candle_close_ms,
                                                   Purpose.ENTRY))
    assert not again.fresh and again.intent_ids == (ENTRY,)


def test_four_hour_and_fifteen_minute_keys_on_the_same_close_never_collide():
    k4 = K.decision_key('trend_ema_mom', 'v1', '4h', 'SOLUSDT', Side.LONG, KEY.candle_close_ms, Purpose.ENTRY)
    k15 = K.decision_key('trend_ema_mom', 'v1', '15m', 'SOLUSDT', Side.LONG, KEY.candle_close_ms, Purpose.ENTRY)
    assert k4 != k15 and K.derive_decision_id(ACCT, k4) != K.derive_decision_id(ACCT, k15)
    assert K.derive_intent_id(ACCT, k4) != K.derive_intent_id(ACCT, k15)
    g = JournalGate(ACCT, PF)
    for ev in _flow(lambda s: s.entry_filled(k4)).events:
        g.admit(ev)
    assert not claim_signal(g.grammar, k4).fresh and claim_signal(g.grammar, k15).fresh
    s = Scenario()
    s.n = g.grammar.last_sequence
    s.entry_filled(k15)
    assert [g.admit(ev) for ev in s.events] == [Admission.APPLY] * 5


def test_a_key_not_built_by_decision_key_is_refused():
    from newcore.domain import DecisionKey
    bare = DecisionKey(strategy='trend_ema_mom', strategy_version='v1', symbol='SOLUSDT', side=Side.LONG,
                       candle_close_ms=KEY.candle_close_ms, purpose=Purpose.ENTRY)
    with pytest.raises(InvalidRecord):
        claim_signal(JournalGate(ACCT, PF).grammar, bare)
    s = Scenario()
    with pytest.raises(JournalConflict):
        JournalGate(ACCT, PF).admit(s.decision(key=bare, intent_ids=(K.derive_intent_id(ACCT, bare),)))


def test_lineage_ordinal_follows_the_journal():
    s = _flow(flow_fallback)
    g = JournalGate.rebuild(ACCT, PF, s.events).grammar
    assert g.next_child_intent_id(LOT, Purpose.PROTECT) == K.derive_child_intent_id(ACCT, LOT, Purpose.PROTECT, 2)
    assert g.next_child_intent_id(LOT, Purpose.CLOSE) == K.derive_child_intent_id(ACCT, LOT, Purpose.CLOSE, 0)
    assert IntentState.REJECTED in {header_of(e).to_state for e in s.events}


def test_lineage_owner_is_resolved_by_owner_kind():
    """NC-01 #38 adaptation: the journal resolves a lineage owner by OrderIntent.owner_kind, never by the id prefix.
    A provisional stop owned by the recorded ENTRY intent (kind entry_intent) is accepted; the header carries the kind."""
    from newcore.domain import OwnerKind
    from newcore.ports.journal import header_of
    s = Scenario()
    s.decision(key=KEY, intent_ids=(ENTRY,))
    s.record(ENTRY)
    sid = K.derive_child_intent_id(ACCT, ENTRY, Purpose.PROTECT, 0)
    s.decision(purpose=Purpose.PROTECT, intent_ids=(sid,), owner=ENTRY, owner_kind=OwnerKind.ENTRY_INTENT)
    rec = s.record(sid)
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    h = header_of(rec)
    assert (h.owner_id, h.owner_kind) == (ENTRY, OwnerKind.ENTRY_INTENT)
    assert j.last_sequence() == len(s.events)
    with pytest.raises(InvalidRecord):                 # the kind must fit the id family: an entry id is no lot
        s._intent(sid, Purpose.PROTECT, rec.intent.decision_id, rec.at_ms, owner=ENTRY, owner_kind=OwnerKind.LOT)


def test_an_incident_is_journaled_in_sequence_without_effect():
    """NC-01 r3 draft item 1: IncidentRecorded maps to incident_recorded and only advances the sequence."""
    from newcore.domain import Incident, IncidentRecorded, ReasonCode, make_id
    s = _flow(flow_fill_and_protect)
    inc = Incident(incident_id=make_id('inc', 1), account_id=ACCT, kind=ReasonCode.RECONCILE_MANUAL_CLOSE,
                   at_ms=s.events[-1].at_ms, symbol='SOLUSDT', side=Side.LONG, intent_refs=(ENTRY,), lot_refs=(LOT,),
                   position_refs=(), evidence=(), detail='')
    ev = IncidentRecorded(event_id=make_id('evt', 10 ** 6), account_id=ACCT, aggregate_id=PF,
                          sequence=len(s.events) + 1, at_ms=inc.at_ms, reason=inc.kind, incident=inc)
    j = ReferenceJournal()
    for e in s.events + [ev]:
        j.append(e)
    assert header_of(ev).kind is EventKind.INCIDENT_RECORDED and j.last_sequence() == len(s.events) + 1
