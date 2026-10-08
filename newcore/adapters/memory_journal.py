"""MemoryJournal: the in-memory JournalPort of slice S1 (STEP0_INTERFACE.md section 3).

Append-only list of NC-01 DomainEvents for ONE (account, aggregate). Every append is checked twice before it lands:
  1. the step-0 grammar G1-G9 over `header_of(event)` (newcore.ports.journal.Grammar): order and idempotence;
  2. NC-01 `check_event_chain` over the whole chain (record content, lifecycle table, client-id reuse), unless
     `check_chain=False`.
An identical re-append (same event_id, sequence and canonical bytes) is ALREADY_APPLIED and changes nothing; any other
reuse, a gap, a reorder or a grammar break raises JournalConflict and changes nothing. `fail_writes(n)` makes the next
n appends raise JournalUnavailable (the store-failure path: nothing may be sent). "Durable" = in this object: the
journal survives a Runner restart (a new Runner over the same journal), not a process exit; NC-02a is the file store.

`header_of` is the NC-01 -> step-0 projection the interface doc leaves for "when NC-01 merges". It lives here until the
ports package is bound to newcore.domain (reported as an interface item).
"""
from __future__ import annotations

from newcore.domain import (BindingChanged, DecisionRecorded, DomainEvent, IntentRecorded, IntentStateChanged,
                            ModeChanged, ResultObserved, check_event_chain, contract_sha256)
from newcore.domain.errors import DomainError
from newcore.domain.orders import IntentState, Lookup, ResultPhase, TERMINAL
from newcore.ports.journal import (Admission, EventHeader, EventKind, Grammar, GrammarError, JournalConflict,
                                   JournalUnavailable, ResultOutcome)
from newcore.ports.keys import DecisionKey as PortKey
from newcore.ports.values import PortValueError

_LIVE_TO = {IntentState.WORKING: 'working', IntentState.UNKNOWN: 'unknown', IntentState.CANCELLING: 'cancelling'}


def port_key(key):
    """NC-01 DecisionKey -> the step-0 DecisionKey (byte-identical canonical form)."""
    return PortKey(strategy=key.strategy, strategy_version=key.strategy_version, symbol=key.symbol, side=str(key.side),
                   candle_close_ms=key.candle_close_ms, purpose=str(key.purpose))


def header_of(ev: DomainEvent) -> EventHeader:
    """What the step-0 grammar sees of one NC-01 event. digest = contract_sha256(event)."""
    base = dict(event_id=ev.event_id, account_id=ev.account_id, aggregate_id=ev.aggregate_id, sequence=ev.sequence,
                at_ms=ev.at_ms, digest=contract_sha256(ev))
    if isinstance(ev, DecisionRecorded):
        d = ev.decision
        return EventHeader(kind=EventKind.DECISION_RECORDED, decision_id=d.decision_id,
                           decision_key=None if d.key is None else port_key(d.key), **base)
    if isinstance(ev, IntentRecorded):
        it = ev.intent
        return EventHeader(kind=EventKind.INTENT_RECORDED, decision_id=it.decision_id, intent_id=it.intent_id,
                           purpose=str(it.purpose), client_ids=it.client_ids, **base)
    if isinstance(ev, IntentStateChanged):
        to = ev.to_state
        if to is IntentState.SUBMITTED:
            return EventHeader(kind=EventKind.SENT, intent_id=ev.intent_id, **base)
        if to in TERMINAL:
            return EventHeader(kind=EventKind.INTENT_CLOSED, intent_id=ev.intent_id, to_state=str(to), **base)
        if to in _LIVE_TO:
            return EventHeader(kind=EventKind.STATE_CHANGED, intent_id=ev.intent_id, to_state=_LIVE_TO[to], **base)
        raise GrammarError('event.to_state', f'{to} has no journal kind (PLANNED / DURABLE are never a state change)')
    if isinstance(ev, ResultObserved):
        r = ev.result
        if r.phase is ResultPhase.FINAL:
            out, evidence = ResultOutcome.FINAL, str(r.evidence)
        elif r.phase is ResultPhase.KNOWN:
            out, evidence = ResultOutcome.KNOWN, None
        else:
            out = ResultOutcome.NOT_FOUND if r.lookup is Lookup.NOT_FOUND else ResultOutcome.UNKNOWN
            evidence = None
        return EventHeader(kind=EventKind.RESULT_RECORDED, intent_id=r.intent_id, outcome=out, evidence=evidence, **base)
    if isinstance(ev, ModeChanged):
        return EventHeader(kind=EventKind.MODE_CHANGED, **base)
    if isinstance(ev, BindingChanged):
        return EventHeader(kind=EventKind.BINDING_CHANGED, **base)
    raise GrammarError('event', f'{type(ev).__name__} is not a journal event')


class MemoryJournal:
    def __init__(self, account_id: str, aggregate_id: str, *, check_chain: bool = True):
        self.account_id, self.aggregate_id = account_id, aggregate_id
        self.check_chain = check_chain
        self._grammar = Grammar(account_id, aggregate_id)
        self._events = []
        self._decisions = {}
        self._ids = set()
        self._fail = 0
        self._fail_after = 0

    def fail_writes(self, n=1, *, after=0):
        """Fault hook: after `after` more successful appends, the next n appends raise JournalUnavailable (nothing
        lands). With after > 0 it is a crash point between two journal boundaries."""
        self._fail += n
        self._fail_after = after

    # ------------------------------------------------------------------------------------------------ JournalPort
    def append(self, event) -> Admission:
        if self._fail > 0 and self._fail_after == 0:
            self._fail -= 1
            raise JournalUnavailable('memory journal: injected store failure')
        try:
            h = header_of(event)
        except (PortValueError, DomainError) as ex:
            raise JournalConflict(f'not a journal event: {ex}') from None
        seen = h.event_id in self._ids
        if not seen and self.check_chain:
            try:
                check_event_chain(self._events + [event])
            except DomainError as ex:
                raise JournalConflict(f'NC-01 chain: {ex}') from None
        try:
            adm = self._grammar.admit(h)
        except PortValueError as ex:
            raise JournalConflict(str(ex)) from None
        if adm is Admission.APPLY:
            self._events.append(event)
            self._ids.add(h.event_id)
            if self._fail > 0 and self._fail_after > 0:
                self._fail_after -= 1
            if isinstance(event, DecisionRecorded):
                self._decisions[event.decision.decision_id] = event
        return adm

    def last_sequence(self) -> int:
        return self._grammar.last_sequence

    def read(self, after_sequence: int = 0):
        return tuple(self._events[after_sequence:])

    def find_decision(self, decision_id: str):
        return self._decisions.get(decision_id)

    # ------------------------------------------------------------------------------------------------ inspection
    def is_consumed(self, key) -> bool:
        return self._grammar.is_consumed(port_key(key))
