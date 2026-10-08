"""Step-0 journal: header_of over real NC-01 events, the Grammar / JournalGate, the consumed-signal rule, and the
JournalPort contract suite run against the reference in-memory journal (STEP0_INTERFACE.md sections 2-3)."""
from decimal import Decimal as D

import pytest

from journal_contract import BAD, GOOD, JournalContract, _flow, flow_fallback, flow_fill_and_protect
from nc_events import ACCT, ENTRY, KEY, LOT, PF, Scenario
from newcore.domain import IntentState, Purpose, Side
from newcore.domain.account import Venue
from newcore.domain import DetailCode, IncidentDetail

_NO = dict(venue_qty=None, journal_qty=None, price=None, count=None, at_ms=None, exchange_status=None)
DETAIL_FLAT = IncidentDetail(**{**_NO, 'code': DetailCode.VENUE_FLAT_JOURNAL_OPEN, 'journal_qty': D('1.5')})
DETAIL_OTHER = IncidentDetail(**{**_NO, 'code': DetailCode.FOREIGN_ORDERS, 'count': 2})
from newcore.domain.events import EVENT_TYPES
from newcore.domain.errors import InvalidRecord
from newcore.ports import keys as K
from newcore.ports.journal import (Admission, EventKind, JournalConflict, JournalGate, JournalUnavailable,
                                   ResultOutcome, claim_signal, header_of)


class ReferenceJournal:
    """The reference JournalPort: in-memory list + JournalGate (what S1's MemoryJournal must behave like)."""

    def __init__(self, account_id=ACCT, aggregate_id=PF, events=(), *, facts=None, after_sequence=0):
        self._gate = JournalGate.rebuild(account_id, aggregate_id, events, facts=facts, after_sequence=after_sequence)
        self._base = after_sequence             # a compacted journal holds only the tail after the snapshot
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
        return self._base + len(self._events)

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
                   position_refs=(), evidence=(), detail=None)
    ev = IncidentRecorded(event_id=make_id('evt', 10 ** 6), account_id=ACCT, aggregate_id=PF,
                          sequence=len(s.events) + 1, at_ms=inc.at_ms, reason=inc.kind, incident=inc)
    j = ReferenceJournal()
    for e in s.events + [ev]:
        j.append(e)
    assert header_of(ev).kind is EventKind.INCIDENT_RECORDED and j.last_sequence() == len(s.events) + 1


def _post_hoc_close_flow(reason_override=None):
    from decimal import Decimal as D
    from newcore.domain import (Action, Authority, Decision, DecisionRecorded, Evidence, ExternalTrade, OrderResult,
                                ReasonCode, ResultObserved, ResultPhase, make_id)
    s = Scenario()
    s.entry_filled()
    cid = K.derive_child_intent_id(ACCT, LOT, Purpose.CLOSE, 0)
    at = s.events[-1].at_ms + 1000
    dec_id = make_id('dec', 4242)
    it = s._intent(cid, Purpose.CLOSE, dec_id, at, owner=LOT)
    it = it.__class__(**{**{f: getattr(it, f) for f in it.__dataclass_fields__},
                         'reason': ReasonCode.RECONCILE_EXTERNAL_CLOSE})
    d = Decision(decision_id=dec_id, account_id=ACCT, at_ms=at, action=Action.RECONCILE,
                 reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, authority=Authority.RECONCILIATION, key=None, evidence=(),
                 symbol='SOLUSDT', side=Side.LONG, subject_id=LOT, detail='', intents=(it,), policy_version='step0-test')
    s._event(DecisionRecorded, reason=d.reason, decision=d)
    s.intents[cid] = it
    s.record(cid)
    trade = ExternalTrade(trade_id='777', venue=Venue.BINANCE_USDM, symbol='SOLUSDT', at_ms=at, qty=it.qty,
                          price=D('149'))
    res = OrderResult(result_id=make_id('res', 4243), intent_id=cid, account_id=ACCT, client_order_id=it.client_order_id,
                      phase=ResultPhase.FINAL, requested_qty=it.qty, observed_at_ms=at + 2000, exchange_order_id=None,
                      exchange_status=None, lookup=None, executed_qty=it.qty, avg_price=D('149'),
                      evidence=Evidence.EXCHANGE_EXTERNAL, corroboration=(), resolved_by=dec_id,
                      external_trades=(trade,), supersedes_result_id=None)
    s._event(ResultObserved, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, result=res)
    s.step(cid, IntentState.DURABLE, IntentState.FILLED)
    return s


def test_an_external_close_is_journaled_as_a_post_hoc_booking():
    """NC-01 r3 draft item 3a: a RECONCILE decision books an external close; the never-sent intent ends with a final
    exchange_external result (G9) and closes FILLED."""
    s = _post_hoc_close_flow()
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    assert j.last_sequence() == len(s.events)


def _late_fill_flow():
    """NC-01 r3 draft item 3b: a protective stop sent, lost (UNKNOWN), decided not_found_corroborated and closed
    CANCELLED - then the venue's FINAL record of its client id shows it filled. Returns (scenario, stop id, late)."""
    from newcore.domain import Evidence, OrderResult, PositionRead, ReasonCode, ResultObserved, ResultPhase, make_id
    from decimal import Decimal as D
    from nc_events import T0, replace
    s = Scenario()
    s.entry_filled()
    sid = s.protect_attempt(0)
    s.step(sid, IntentState.SUBMITTED, IntentState.UNKNOWN)
    it = s.intents[sid]
    reads = (PositionRead(at_ms=T0 + 60_000, qty=it.qty), PositionRead(at_ms=T0 + 61_000, qty=it.qty))
    cor = OrderResult(result_id=make_id('res', 9001), intent_id=sid, account_id=ACCT, client_order_id=it.client_order_id,
                      phase=ResultPhase.FINAL, requested_qty=it.qty, observed_at_ms=T0 + 62_000, exchange_order_id=None,
                      exchange_status=None, lookup=None, executed_qty=D('0'), avg_price=None,
                      evidence=Evidence.NOT_FOUND_CORROBORATED, corroboration=reads, resolved_by=make_id('dec', 9002),
                      external_trades=(), supersedes_result_id=None)
    s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=cor)
    s.step(sid, IntentState.UNKNOWN, IntentState.CANCELLED)
    filled = s.result(sid, 'filled').result
    s.events.pop()
    s.n -= 1
    late = replace(filled, result_id=make_id('res', 9003), observed_at_ms=T0 + 90_000,
                   supersedes_result_id=cor.result_id)                    # PR #44 P1-b: a new fact naming the prior
    return s, sid, late


def test_a_late_final_record_supersedes_not_found_corroborated_once():
    from nc_events import replace
    from newcore.domain import (Action, Authority, Decision, DecisionRecorded, ReasonCode, ResultObserved, Side,
                                make_id)
    s, sid, late = _late_fill_flow()
    s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=late)
    d = Decision(decision_id=make_id('dec', 9004), account_id=ACCT, at_ms=s.events[-1].at_ms + 1000,
                 action=Action.RECONCILE, reason=ReasonCode.RECONCILE_LATE_FILL, authority=Authority.RECONCILIATION,
                 key=None, evidence=(late.result_id,), symbol='SOLUSDT', side=Side.LONG, subject_id=sid, detail='',
                 intents=(), policy_version='step0-test')
    s._event(DecisionRecorded, reason=d.reason, decision=d)
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    assert j.last_sequence() == len(s.events)
    again = s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL,
                     result=replace(late, result_id=make_id('res', 9005)))
    with pytest.raises(JournalConflict, match='G10'):
        j.append(again)                                          # superseded once; the intent stays closed


def test_only_an_executed_exchange_record_supersedes_in_the_journal():
    from decimal import Decimal as D
    from nc_events import replace
    from newcore.domain import ExchangeStatus, ReasonCode, ResultObserved
    s, sid, late = _late_fill_flow()
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    zero = replace(late, executed_qty=D('0'), avg_price=None, exchange_status=ExchangeStatus.CANCELED,
                   supersedes_result_id=None)
    with pytest.raises(JournalConflict, match='superseding not_found_corroborated'):
        j.append(s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=zero))


def test_the_journal_refuses_to_send_a_post_hoc_booking():
    """r3a ruling 2 / 3 (cross-layer): the step-0 journal gate applies the same rule as the domain event chain - a
    reconcile.external_close booking is never sent, so its SENT step is refused before it is durable."""
    s = _post_hoc_close_flow()
    j = ReferenceJournal()
    events = s.events[:-2]                       # decision + record (before the external result / FILLED step)
    for ev in events:
        j.append(ev)
    cid = s.events[-1].intent_id
    send = s.step(cid, IntentState.DURABLE, IntentState.SUBMITTED)
    with pytest.raises(JournalConflict, match='a post-hoc booking is never sent'):
        j.append(replace_seq(send, len(events) + 1))
    assert j.last_sequence() == len(events)


def replace_seq(ev, n):
    from nc_events import replace
    return replace(ev, sequence=n)


def _late_fill_decision_event(s, sid, late, dec_n=9004):
    from newcore.domain import Action, Authority, Decision, DecisionRecorded, ReasonCode, Side, make_id
    d = Decision(decision_id=make_id('dec', dec_n), account_id=ACCT, at_ms=late.observed_at_ms + 1000,
                 action=Action.RECONCILE, reason=ReasonCode.RECONCILE_LATE_FILL, authority=Authority.RECONCILIATION,
                 key=None, evidence=(late.result_id,), symbol='SOLUSDT', side=Side.LONG, subject_id=sid, detail='',
                 intents=(), policy_version='step0-test')
    return s._event(DecisionRecorded, reason=d.reason, decision=d)


def test_late_fill_crash_before_the_journal_write_leaves_nothing_applied():
    """r3 item 3b ruling 4: the write fails (crash before durable) -> the gate never saw the late record; the retry
    is admitted exactly once, and the reconcile then applies it once."""
    from newcore.domain import ReasonCode, ResultObserved
    s, sid, late = _late_fill_flow()
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    rec = s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=late)
    j.inject_write_failure()
    with pytest.raises(JournalUnavailable):
        j.append(rec)
    dec = _late_fill_decision_event(s, sid, late)
    with pytest.raises(JournalConflict, match='journaled superseding FINAL record'):
        j.append(replace_seq(dec, rec.sequence))                 # nothing to apply: the late record is not durable
    assert j.append(rec) is Admission.APPLY                       # the retry
    assert j.append(dec) is Admission.APPLY


def test_late_fill_crash_after_the_journal_write_restart_and_repeated_reconcile_apply_once():
    """r3 item 3b ruling 4: the late record and its reconcile are durable; after a restart (gate rebuilt from the
    decoded journal bytes) a repeated reconcile pass re-delivers the same events -> ALREADY_APPLIED, and any second
    application (another decision id, another late record) is refused. Two rebuilds fold identically."""
    from newcore.domain import ReasonCode, ResultObserved, canonical_bytes, loads, make_id
    from nc_events import replace
    s, sid, late = _late_fill_flow()
    rec = s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=late)
    dec = _late_fill_decision_event(s, sid, late)
    blob = [canonical_bytes(ev) for ev in s.events]
    journals = [ReferenceJournal(events=[loads(b) for b in blob]) for _ in range(2)]      # two restarts
    for j in journals:
        assert j.last_sequence() == len(blob)
        assert j.append(rec) is Admission.ALREADY_APPLIED and j.append(dec) is Admission.ALREADY_APPLIED
        again = replace(dec, event_id=make_id('evt', 777), sequence=len(blob) + 1,
                        decision=replace(dec.decision, decision_id=make_id('dec', 9005)))
        with pytest.raises(JournalConflict, match='reconciled once'):
            j.append(again)
        late2 = replace(rec, event_id=make_id('evt', 778), sequence=len(blob) + 1,
                        result=replace(late, result_id=make_id('res', 9006)))
        with pytest.raises(JournalConflict, match='G10'):
            j.append(late2)
        assert j.last_sequence() == len(blob)
    assert [canonical_bytes(e) for e in journals[0]._events] == [canonical_bytes(e) for e in journals[1]._events]


def test_late_fill_before_the_terminal_step_is_refused_by_the_journal():
    """r3 item 3b ruling 4 (cross-layer twin of test_item3b_the_original_intent_stays_terminal): the intent closes on
    its corroborated final first; a late record before that step is 'a result after the final result' (G9)."""
    from newcore.domain import ReasonCode, ResultObserved
    s, sid, late = _late_fill_flow()
    events = s.events[:-1]                                   # without the UNKNOWN -> CANCELLED step
    j = ReferenceJournal()
    for ev in events:
        j.append(ev)
    rec = replace_seq(s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=late), len(events) + 1)
    with pytest.raises(JournalConflict, match='G9: a result after the final result'):
        j.append(rec)


def test_the_journal_refuses_a_reused_incident_id():
    """PR #44 (Cowork 6), the step-0 twin of test_6_an_incident_id_is_used_once_in_the_log: the gate refuses a second
    IncidentRecorded with an incident id already journaled; re-delivering the SAME event stays idempotent."""
    from nc_events import replace
    from newcore.domain import Incident, IncidentRecorded, ReasonCode, make_id
    s = Scenario()
    s.entry_filled()
    inc = Incident(incident_id=make_id('inc', 1), account_id=ACCT, kind=ReasonCode.RECONCILE_MANUAL_CLOSE,
                   at_ms=s.events[-1].at_ms, symbol='SOLUSDT', side=Side.LONG, intent_refs=(), lot_refs=(LOT,),
                   position_refs=(), evidence=(), detail=DETAIL_FLAT)
    first = s._event(IncidentRecorded, reason=inc.kind, incident=inc)
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    assert j.append(first) is Admission.ALREADY_APPLIED
    again = s._event(IncidentRecorded, reason=inc.kind, incident=replace(inc, detail=DETAIL_OTHER))
    with pytest.raises(JournalConflict, match='incident id .* used for a different fact'):
        j.append(again)
    other = replace(again, incident=replace(inc, incident_id=make_id('inc', 2)))
    assert j.append(other) is Admission.APPLY


# ================================================== Codex review of #44: the step-0 journal twins (ruling 3)
def _second_booking(s, trade_ids=('777',), n=4300):
    """Append a second external-close booking (a REDUCE of 0.5) citing `trade_ids` to scenario `s`."""
    from decimal import Decimal as D
    from nc_events import replace
    from newcore.domain import (Action, Authority, Decision, DecisionRecorded, Evidence, ExternalTrade, OrderResult,
                                ReasonCode, ResultObserved, ResultPhase, make_id)
    rid = K.derive_child_intent_id(ACCT, LOT, Purpose.REDUCE, 0)
    at = s.events[-1].at_ms + 1000
    dec_id = make_id('dec', n)
    it = replace(s._intent(rid, Purpose.REDUCE, dec_id, at, owner=LOT, qty=D('0.5')),
                 reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE)
    d = Decision(decision_id=dec_id, account_id=ACCT, at_ms=at, action=Action.RECONCILE,
                 reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, authority=Authority.RECONCILIATION, key=None, evidence=(),
                 symbol='SOLUSDT', side=Side.LONG, subject_id=LOT, detail='', intents=(it,), policy_version='step0-test')
    s._event(DecisionRecorded, reason=d.reason, decision=d)
    s.intents[rid] = it
    s.record(rid)
    q = D('0.5') / len(trade_ids)
    trades = tuple(ExternalTrade(trade_id=t, venue=Venue.BINANCE_USDM, symbol='SOLUSDT', at_ms=at, qty=q,
                                 price=D('149')) for t in trade_ids)
    res = OrderResult(result_id=make_id('res', n + 1), intent_id=rid, account_id=ACCT, client_order_id=it.client_order_id,
                      phase=ResultPhase.FINAL, requested_qty=it.qty, observed_at_ms=at + 2000, exchange_order_id=None,
                      exchange_status=None, lookup=None, executed_qty=it.qty, avg_price=D('149'),
                      evidence=Evidence.EXCHANGE_EXTERNAL, corroboration=(), resolved_by=dec_id, external_trades=trades,
                      supersedes_result_id=None)
    return s._event(ResultObserved, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, result=res)


def test_journal_books_one_venue_trade_once_across_restarts():
    """P1-a twin: trade 777 booked by the first external close cannot be booked again - not in the live journal and
    not after a restart rebuilt from the decoded bytes; a repeated reconcile pass of the same events is idempotent."""
    from newcore.domain import canonical_bytes, loads
    s = _post_hoc_close_flow()
    booked = list(s.events)
    again = _second_booking(s, ('777',))
    j = ReferenceJournal()
    for ev in booked:
        j.append(ev)
    for ev in s.events[len(booked):-1]:
        j.append(ev)
    with pytest.raises(JournalConflict, match='venue trade .*/777 is already booked'):
        j.append(again)
    restarted = ReferenceJournal(events=[loads(canonical_bytes(ev)) for ev in s.events[:-1]])
    assert all(restarted.append(ev) is Admission.ALREADY_APPLIED for ev in s.events[:-1])     # repeated reconcile
    with pytest.raises(JournalConflict, match='venue trade .*/777 is already booked'):
        restarted.append(again)


def test_journal_keeps_result_ids_and_incident_ids_unique_and_late_finals_explicit():
    """P1-b / P2-1 twins: a late final reusing the prior result id, or not naming it, is refused; an incident id
    reused for different content is refused even after a restart; the identical incident again is a no-op."""
    from nc_events import replace
    from newcore.domain import Incident, IncidentRecorded, ReasonCode, ResultObserved, canonical_bytes, loads, make_id
    s, sid, late = _late_fill_flow()
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    prior = [e for e in s.events if isinstance(e, ResultObserved)][-1].result
    reused = replace(late, result_id=prior.result_id, supersedes_result_id=None)    # the prior fact's own id
    for bad, why in ((reused, 'different fact'), (replace(late, supersedes_result_id=None), 'superseding')):
        ev = replace_seq(s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=bad),
                         len(s.events))
        s.events.pop()
        s.n -= 1
        with pytest.raises(JournalConflict, match=why):
            j.append(ev)
    with pytest.raises(InvalidRecord, match='supersedes itself'):                    # cannot even be built
        replace(late, result_id=prior.result_id)
    inc = Incident(incident_id=make_id('inc', 44), account_id=ACCT, kind=ReasonCode.RECONCILE_MANUAL_CLOSE,
                   at_ms=s.events[-1].at_ms, symbol='SOLUSDT', side=Side.LONG, intent_refs=(), lot_refs=(LOT,),
                   position_refs=(), evidence=(), detail=DETAIL_FLAT)
    j.append(s._event(IncidentRecorded, reason=inc.kind, incident=inc))
    assert j.append(s._event(IncidentRecorded, reason=inc.kind, incident=inc)) is Admission.APPLY   # identical fact
    restarted = ReferenceJournal(events=[loads(canonical_bytes(ev)) for ev in s.events])
    conflict = s._event(IncidentRecorded, reason=inc.kind, incident=replace(inc, detail=DETAIL_OTHER))
    with pytest.raises(JournalConflict, match='incident id .* used for a different fact'):
        restarted.append(conflict)


def test_journal_refuses_an_external_booking_resolved_by_another_decision():
    """P2-2 twin: the gate's content check requires resolved_by == the booking intent's own RECONCILE decision."""
    from nc_events import replace
    from newcore.domain import ResultObserved, make_id
    s = _post_hoc_close_flow()
    j = ReferenceJournal()
    for ev in s.events[:-2]:
        j.append(ev)
    res_ev = s.events[-2]
    assert isinstance(res_ev, ResultObserved)
    bad = replace(res_ev, result=replace(res_ev.result, resolved_by=make_id('dec', 999_999)))
    with pytest.raises(JournalConflict, match='resolved by the RECONCILE decision that booked it'):
        j.append(bad)


def test_journal_refuses_a_dangling_supersedes_result_id():
    """P1-b step-0 twin: a first FINAL that names a prior result it cannot supersede is refused by the gate."""
    from nc_events import replace
    from newcore.domain import ResultObserved, make_id
    s = Scenario()
    s.entry_filled()
    sid = s.protect_attempt(0)
    j = ReferenceJournal()
    for ev in s.events:
        j.append(ev)
    ev = s.result(sid, 'filled')
    with pytest.raises(JournalConflict, match='superseding'):
        j.append(replace(ev, result=replace(ev.result, supersedes_result_id=make_id('res', 31337))))
    assert isinstance(ev, ResultObserved) and j.append(ev) is Admission.APPLY


def test_journal_rebuilt_from_a_compacted_snapshot_keeps_its_facts():
    """Codex re-review P1 (step-0 side): after compaction the gate is rebuilt from the snapshot's FactIndex and the
    tail only. A venue trade, a result id or an incident id from before the snapshot is still a fact: reuse with other
    content is refused; the identical incident again is a no-op fact."""
    from nc_events import replace
    from newcore.domain import (Incident, IncidentRecorded, ReasonCode, ResultObserved, canonical_bytes, fold_facts,
                                loads, make_id)
    from newcore.domain.codec import from_json, to_json
    from newcore.domain import FactIndex
    s = _post_hoc_close_flow()
    inc = Incident(incident_id=make_id('inc', 77), account_id=ACCT, kind=ReasonCode.RECONCILE_MANUAL_CLOSE,
                   at_ms=s.events[-1].at_ms, symbol='SOLUSDT', side=Side.LONG, intent_refs=(), lot_refs=(LOT,),
                   position_refs=(), evidence=(), detail=DETAIL_FLAT)
    s._event(IncidentRecorded, reason=inc.kind, incident=inc)
    booked = next(e for e in s.events if isinstance(e, ResultObserved) and e.result.external_trades).result
    facts = from_json(FactIndex, to_json(fold_facts([loads(canonical_bytes(e)) for e in s.events])), 'facts')
    n = len(s.events)

    def gate():
        return ReferenceJournal(events=(), facts=facts, after_sequence=n)      # the compacted restart

    def nxt(ev):
        return replace(ev, sequence=n + 1, event_id=make_id('evt', 10 ** 7 + 1))

    reuse_trade = replace(booked, result_id=make_id('res', 8801))
    with pytest.raises(JournalConflict, match='venue trade .*777 is already booked'):
        gate().append(nxt(s._event(ResultObserved, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, result=reuse_trade)))
    reuse_id = replace(booked, avg_price=booked.external_trades[0].price, observed_at_ms=booked.observed_at_ms + 1)
    with pytest.raises(JournalConflict, match='result id .* used for a different fact'):
        gate().append(nxt(s._event(ResultObserved, reason=ReasonCode.RECONCILE_EXTERNAL_CLOSE, result=reuse_id)))
    assert gate().append(nxt(s._event(IncidentRecorded, reason=inc.kind, incident=inc))) is Admission.APPLY
    with pytest.raises(JournalConflict, match='incident id .* used for a different fact'):
        gate().append(nxt(s._event(IncidentRecorded, reason=inc.kind, incident=replace(inc, detail=DETAIL_OTHER))))
