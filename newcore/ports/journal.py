"""JournalPort, its one event grammar and the one admission gate (STEP0_INTERFACE.md section 3).

Events are NC-01 DomainEvents. `header_of(event)` is the ONE canonical projection the grammar reads; `JournalGate` is
the ONE admission every JournalPort implementation runs before making an event durable:

    header_of(event) -> Grammar.admit (order, idempotence, identity, lineage, authorization, routes)
                     -> NC-01 per-record checks (lifecycle step from the current state, result vs its intent,
                        terminal step = terminal_for(final result))

Grammar rules (G1-G10). Admission is atomic: a refused event leaves everything unchanged.
  G1  one journal = one (account_id, aggregate_id).
  G2  sequence is exactly last + 1; a timestamp never orders events.
  G3  a known event_id is ALREADY_APPLIED iff same sequence and same digest (NC-01 contract_sha256); else a conflict.
  G4  decision_recorded: new decision_id; it commits its exact authorized intent-id set (Decision.intents). A keyed
      decision has decision_id = derive_decision_id(account, key), a key from keys.decision_key() (strategy instance
      '<name>@<tf>', on-grid close), and authorizes () or exactly (derive_intent_id(account, key),).
  G5  intent_recorded: new intent_id, authorized by its (already recorded) decision. A keyed intent has the key's
      purpose. Every other intent needs an owner (int_ entry or lot_) and its id must be
      derive_child_intent_id(account, owner, purpose, ordinal) with ordinal = the journal's count of earlier
      (owner, purpose) intents. An int_ owner is a recorded entry intent; a lot_ owner is derive_lot_id of an entry with
      an executing final result. One client id per intent: client_id_for(intent, route), never reused.
  G6  routes: 'algo' only for protect, and only as the fallback of the owner's previous protect attempt, which was sent
      on 'classic' and closed 'rejected'. An attempt intent is one route and one send; a second route is a new
      lineage intent, so the second send is journaled and a third attempt on the same failure is refused.
  G7  sent: only from recorded, once.
  G8  state_changed (working / unknown / cancelling): live, no final result yet; working / unknown only after sent.
  G9  result_recorded: the intent is recorded (never a result before it), no final result yet, the result names the
      intent's client id; a never-sent intent only takes final + not_sent; final names its evidence, nothing else.
  G10 intent_closed (terminal): only after a final result; nothing follows it.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from newcore.domain import (DecisionKey, DecisionRecorded, Evidence, IntentRecorded, IntentState, IntentStateChanged,
                            Lookup, ModeChanged, OwnerKind, Purpose, ResultObserved, ResultPhase)
from newcore.domain.codec import contract_sha256
from newcore.domain.errors import DomainError
from newcore.domain.events import EVENT_TYPES
from newcore.domain.ledger import Admission
from newcore.domain.orders import TERMINAL, can_transition, check_result_for_intent, terminal_for

from .keys import (check_decision_key, derive_child_intent_id, derive_decision_id, derive_intent_id, derive_lot_id,
                   route_of)
from .values import PortValueError, check_client_id, check_id, check_int, check_ms, req

SHA_RE = re.compile(r'[0-9a-f]{64}')
LIVE_STATES = frozenset({IntentState.WORKING, IntentState.UNKNOWN, IntentState.CANCELLING})
EXECUTING = frozenset({Evidence.EXCHANGE_FINAL, Evidence.POSITION_ADOPTED})


class EventKind(enum.StrEnum):
    DECISION_RECORDED = 'decision_recorded'    # DecisionRecorded
    INTENT_RECORDED = 'intent_recorded'        # IntentRecorded (the intent DURABLE: write-ahead, before the send)
    SENT = 'sent'                              # IntentStateChanged -> submitted
    STATE_CHANGED = 'state_changed'            # IntentStateChanged -> working / unknown / cancelling
    RESULT_RECORDED = 'result_recorded'        # ResultObserved (durable before it is applied)
    INTENT_CLOSED = 'intent_closed'            # IntentStateChanged -> filled / cancelled / rejected / not_sent
    MODE_CHANGED = 'mode_changed'              # ModeChanged (HOLD in / out, halt, pause, resume)
    BINDING_CHANGED = 'binding_changed'        # BindingChanged (the binding confirmation the risk gate requires)


class ResultOutcome(enum.StrEnum):
    """Projection of OrderResult (phase, lookup); not an NC-01 type."""
    FINAL = 'final'
    KNOWN = 'known'
    UNKNOWN = 'unknown'
    NOT_FOUND = 'not_found'      # phase UNKNOWN + lookup NOT_FOUND: proves nothing, books nothing


_INTENT_KINDS = frozenset({EventKind.INTENT_RECORDED, EventKind.SENT, EventKind.STATE_CHANGED,
                           EventKind.RESULT_RECORDED, EventKind.INTENT_CLOSED})


class GrammarError(PortValueError):
    """The event breaks the journal grammar: a hard failure, never skipped."""


class JournalConflict(Exception):
    """append refused by the gate: a hard failure (HOLD), never retried with the same event."""


class JournalUnavailable(Exception):
    """The store cannot make the event durable (ENOSPC, EROFS, ...): nothing may be sent (NC-02 A21 / hard HOLD)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class EventHeader:
    """What the grammar reads of one DomainEvent. Built only by header_of()."""
    kind: EventKind
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    digest: str
    decision_id: str | None = None          # decision_recorded; intent_recorded (the authorizing decision)
    decision_key: DecisionKey | None = None  # decision_recorded of a strategy decision
    authorized: tuple[str, ...] = ()        # decision_recorded: the exact intent ids it authorizes
    intent_id: str | None = None            # every intent kind
    purpose: Purpose | None = None          # intent_recorded
    owner_id: str | None = None             # intent_recorded: the entry intent / lot it works for (lineage parent)
    owner_kind: OwnerKind | None = None     # intent_recorded: what owner_id is (NC-01 OrderIntent.owner_kind)
    client_ids: tuple[str, ...] = ()        # intent_recorded (exactly one in step 0); result_recorded (the result's)
    to_state: IntentState | None = None     # state_changed / intent_closed
    outcome: ResultOutcome | None = None    # result_recorded
    evidence: Evidence | None = None        # result_recorded, final only

    def __post_init__(self):
        k, p = self.kind, f'EventHeader[{self.event_id}]'
        req(isinstance(k, EventKind), p + '.kind', 'an EventKind')
        check_id(self.event_id, p + '.event_id', 'evt')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.aggregate_id, p + '.aggregate_id', 'pf')
        check_int(self.sequence, p + '.sequence', 1, 2 ** 63 - 1)
        check_ms(self.at_ms, p + '.at_ms')
        req(isinstance(self.digest, str) and SHA_RE.fullmatch(self.digest) is not None, p + '.digest', 'sha256 hex')
        on = {EventKind.DECISION_RECORDED: ('decision_id',),
              EventKind.INTENT_RECORDED: ('decision_id', 'intent_id', 'purpose', 'client_ids'),
              EventKind.SENT: ('intent_id',), EventKind.STATE_CHANGED: ('intent_id', 'to_state'),
              EventKind.INTENT_CLOSED: ('intent_id', 'to_state'),
              EventKind.RESULT_RECORDED: ('intent_id', 'outcome', 'client_ids')}.get(k, ())
        for name in ('decision_id', 'intent_id', 'purpose', 'client_ids', 'to_state', 'outcome'):
            v = getattr(self, name)
            req((v not in (None, ())) == (name in on), f'{p}.{name}', f'set exactly when {k} needs it')
        req(self.decision_key is None or k is EventKind.DECISION_RECORDED, p + '.decision_key', 'decisions only')
        req(not self.authorized or k is EventKind.DECISION_RECORDED, p + '.authorized', 'decisions only')
        req(self.owner_id is None or k is EventKind.INTENT_RECORDED, p + '.owner_id', 'intent_recorded only')
        req((self.owner_kind is None) == (self.owner_id is None), p + '.owner_kind', 'set exactly with owner_id')
        req(len(self.client_ids) <= 1, p + '.client_ids', 'one client id per intent (no alt id in step 0)')
        for c in self.client_ids:
            check_client_id(c, p + '.client_ids')
        states = {EventKind.STATE_CHANGED: LIVE_STATES, EventKind.INTENT_CLOSED: TERMINAL}.get(k)
        req(states is None or self.to_state in states, p + '.to_state', f'not a {k} state')
        req((self.evidence is not None) == (self.outcome is ResultOutcome.FINAL), p + '.evidence',
            'a final result names its evidence; nothing else does')


def header_of(event) -> EventHeader:
    """The canonical projection of an NC-01 DomainEvent. Raises InvalidRecord for anything else."""
    req(isinstance(event, EVENT_TYPES), 'event', f'{type(event).__name__} is not an NC-01 DomainEvent')
    base = dict(event_id=event.event_id, account_id=event.account_id, aggregate_id=event.aggregate_id,
                sequence=event.sequence, at_ms=event.at_ms, digest=contract_sha256(event))
    if isinstance(event, DecisionRecorded):
        d = event.decision
        return EventHeader(kind=EventKind.DECISION_RECORDED, decision_id=d.decision_id, decision_key=d.key,
                           authorized=tuple(i.intent_id for i in d.intents), **base)
    if isinstance(event, IntentRecorded):
        i = event.intent
        return EventHeader(kind=EventKind.INTENT_RECORDED, decision_id=i.decision_id, intent_id=i.intent_id,
                           purpose=i.purpose, owner_id=i.owner_id, owner_kind=i.owner_kind, client_ids=i.client_ids,
                           **base)
    if isinstance(event, IntentStateChanged):
        to = event.to_state
        if to is IntentState.SUBMITTED:
            return EventHeader(kind=EventKind.SENT, intent_id=event.intent_id, **base)
        kind = EventKind.INTENT_CLOSED if to in TERMINAL else EventKind.STATE_CHANGED
        return EventHeader(kind=kind, intent_id=event.intent_id, to_state=to, **base)
    if isinstance(event, ResultObserved):
        r = event.result
        outcome = {ResultPhase.FINAL: ResultOutcome.FINAL, ResultPhase.KNOWN: ResultOutcome.KNOWN}.get(
            r.phase, ResultOutcome.NOT_FOUND if r.lookup is Lookup.NOT_FOUND else ResultOutcome.UNKNOWN)
        return EventHeader(kind=EventKind.RESULT_RECORDED, intent_id=r.intent_id, outcome=outcome, evidence=r.evidence,
                           client_ids=(r.client_order_id,), **base)
    if isinstance(event, ModeChanged):
        return EventHeader(kind=EventKind.MODE_CHANGED, **base)
    return EventHeader(kind=EventKind.BINDING_CHANGED, **base)         # BindingChanged: the rest of EVENT_TYPES


@dataclass(slots=True)
class _Intent:
    decision_id: str
    purpose: Purpose
    owner_id: str | None
    client_id: str
    route: str
    sent: bool = False
    final: Evidence | None = None
    closed: IntentState | None = None


def req_g(cond, path, msg):
    if not cond:
        raise GrammarError(path, msg)


class Grammar:
    """Pure grammar state of one journal. Rebuilt after a restart by admitting the durable journal again."""

    def __init__(self, account_id: str, aggregate_id: str):
        check_id(account_id, 'Grammar.account_id', 'acct')
        check_id(aggregate_id, 'Grammar.aggregate_id', 'pf')
        self.account_id, self.aggregate_id = account_id, aggregate_id
        self.last_sequence = 0
        self._events: dict[str, tuple[int, str]] = {}
        self._decisions: dict[str, tuple[DecisionKey | None, frozenset]] = {}
        self._intents: dict[str, _Intent] = {}
        self._client_ids: set[str] = set()
        self._lineage: dict[tuple[str, Purpose], int] = {}
        self._last_protect: dict[str, str] = {}
        self._lots: set[str] = set()

    # ------------------------------------------------------------------------------------------------ queries
    def is_consumed(self, key: DecisionKey) -> bool:
        return derive_decision_id(self.account_id, key) in self._decisions

    def intents_of(self, decision_id: str) -> tuple[str, ...]:
        return tuple(i for i, st in self._intents.items() if st.decision_id == decision_id)

    def next_child_intent_id(self, owner_id: str, purpose) -> str:
        """The only id the next (owner, purpose) intent may have (restart-stable: counted from the journal)."""
        purpose = Purpose(purpose)
        return derive_child_intent_id(self.account_id, owner_id, purpose, self._lineage.get((owner_id, purpose), 0))

    # ------------------------------------------------------------------------------------------------ admission
    def admit(self, h: EventHeader) -> Admission:
        commit = self.prepare(h)
        if commit is None:
            return Admission.ALREADY_APPLIED
        commit()
        return Admission.APPLY

    def prepare(self, h: EventHeader):
        """Check h without mutating. None = ALREADY_APPLIED (G3); else the commit to run once it is admitted."""
        req(isinstance(h, EventHeader), 'event', 'an EventHeader')
        p = f'event[{h.event_id}]'
        req_g((h.account_id, h.aggregate_id) == (self.account_id, self.aggregate_id), p + '.aggregate_id',
              'G1: event of another account / aggregate')
        seen = self._events.get(h.event_id)
        if seen is not None:
            req_g(seen == (h.sequence, h.digest), p + '.event_id', 'G3: event id re-used with other sequence / bytes')
            return None
        if h.sequence != self.last_sequence + 1:
            what = 'a gap' if h.sequence > self.last_sequence + 1 else 'an already-used sequence (out of order)'
            raise GrammarError(p + '.sequence', f'G2: {h.sequence} is {what}; expected {self.last_sequence + 1}')
        apply = self._check(h, p)

        def commit():
            apply()
            self._events[h.event_id] = (h.sequence, h.digest)
            self.last_sequence = h.sequence
        return commit

    def _check(self, h, p):
        """Validate without mutating; return the mutation."""
        k = h.kind
        if k is EventKind.DECISION_RECORDED:
            return self._check_decision(h, p)
        if k is EventKind.INTENT_RECORDED:
            return self._check_intent(h, p)
        if k not in _INTENT_KINDS:
            return lambda: None
        st = self._intents.get(h.intent_id)
        req_g(st is not None, p, f'{k} before the intent was recorded')
        req_g(st.closed is None, p, f'G10: {k} after the intent closed')
        if k is EventKind.SENT:
            req_g(not st.sent and st.final is None, p, 'G7: sent twice or after its final result')

            def commit_sent():
                st.sent = True
            return commit_sent
        if k is EventKind.STATE_CHANGED:
            req_g(st.final is None, p, 'G8: after a final result only intent_closed may follow')
            req_g(st.sent or h.to_state is IntentState.CANCELLING, p, f'G8: {h.to_state} before sent')
            return lambda: None
        if k is EventKind.RESULT_RECORDED:
            req_g(st.final is None, p, 'G9: a result after the final result')
            req_g(h.client_ids == (st.client_id,), p + '.client_ids', "G9: the result names another intent's order")
            if not st.sent:
                req_g(h.evidence is Evidence.NOT_SENT, p, 'G9: a never-sent intent only takes a final not_sent result')
            else:
                req_g(h.evidence is not Evidence.NOT_SENT, p, 'G9: the intent was sent; not_sent is impossible')
            if h.outcome is not ResultOutcome.FINAL:
                return lambda: None
            lot = derive_lot_id(self.account_id, h.intent_id) if (
                st.purpose is Purpose.ENTRY and h.evidence in EXECUTING) else None

            def commit_final():
                st.final = h.evidence
                if lot is not None:
                    self._lots.add(lot)
            return commit_final
        req_g(st.final is not None, p, 'G10: closed before its final result was recorded')

        def commit_closed():
            st.closed = h.to_state
        return commit_closed

    def _check_decision(self, h, p):
        req_g(h.decision_id not in self._decisions, p, 'G4: decision recorded twice (a consumed signal is spent)')
        req_g(len(set(h.authorized)) == len(h.authorized), p + '.authorized', 'G4: duplicate authorized id')
        key = h.decision_key
        if key is not None:
            check_decision_key(key)
            req_g(h.decision_id == derive_decision_id(self.account_id, key), p + '.decision_id',
                  'G4: a keyed decision id must be derive_decision_id(account, key)')
            req_g(h.authorized in ((), (derive_intent_id(self.account_id, key),)), p + '.authorized',
                  'G4: a keyed decision authorizes exactly derive_intent_id(account, key) or nothing')
        return lambda: self._decisions.__setitem__(h.decision_id, (key, frozenset(h.authorized)))

    def _check_intent(self, h, p):
        req_g(h.intent_id not in self._intents, p, 'G5: intent recorded twice (one signal never creates a second)')
        dec = self._decisions.get(h.decision_id)
        req_g(dec is not None, p + '.decision_id', 'G5: intent recorded before its decision')
        key, authorized = dec
        req_g(h.intent_id in authorized, p + '.intent_id', 'G4/G5: not in its decision\'s authorized intent set')
        owner = h.owner_id
        if owner is not None:                   # resolved by the intent's typed owner_kind, never by the id's prefix
            # (a PORTFOLIO owner cannot reach here: NC-01 makes a portfolio-owned intent cancel-only, never DURABLE)
            if h.owner_kind is OwnerKind.ENTRY_INTENT:
                o = self._intents.get(owner)
                req_g(o is not None and o.purpose is Purpose.ENTRY, p + '.owner_id', 'G5: owner is no recorded entry')
            else:
                req_g(owner in self._lots, p + '.owner_id', 'G5: owner is no lot opened by a recorded entry fill')
        n = self._lineage.get((owner, h.purpose), 0)
        if key is not None:
            req_g(h.purpose is key.purpose, p + '.purpose', 'G5: purpose differs from the decision key')
        else:
            req_g(owner is not None, p + '.owner_id', 'G5: an unkeyed intent needs its lineage owner')
            req_g(h.intent_id == derive_child_intent_id(self.account_id, owner, h.purpose, n), p + '.intent_id',
                  'G5: lineage id must be derive_child_intent_id(account, owner, purpose, journal ordinal)')
        cid = h.client_ids[0]
        route = route_of(h.intent_id, cid)
        req_g(route is not None, p + '.client_ids', 'G5: the client id must be client_id_for(intent, route)')
        req_g(cid not in self._client_ids, p + '.client_ids', 'G5: a client id is never reused')
        if route == 'algo':
            prev = self._intents.get(self._last_protect.get(owner, ''))
            req_g(h.purpose is Purpose.PROTECT and prev is not None and prev.route == 'classic' and prev.sent
                  and prev.closed is IntentState.REJECTED, p + '.client_ids',
                  'G6: algo only as the fallback of a sent classic protect attempt that closed rejected')

        def commit():
            self._intents[h.intent_id] = _Intent(h.decision_id, h.purpose, owner, cid, route)
            self._client_ids.add(cid)
            if owner is not None:
                self._lineage[(owner, h.purpose)] = n + 1
                if h.purpose is Purpose.PROTECT:
                    self._last_protect[owner] = h.intent_id
        return commit


# ---------------------------------------------------------------------------------------------- the one gate
class StagedEvent:
    """A validated, NOT yet committed admission (JournalGate.stage). The journal makes the event durable FIRST and
    only then calls commit(); if persistence fails it simply drops this object, and the gate never saw the event."""
    __slots__ = ('_gate', '_event', '_grammar_commit', '_at_sequence', '_done')

    def __init__(self, gate, event, grammar_commit):
        self._gate, self._event, self._grammar_commit = gate, event, grammar_commit
        self._at_sequence, self._done = gate.grammar.last_sequence, False

    @property
    def event(self):
        return self._event

    def commit(self) -> Admission:
        """Apply the admission to the gate. Call exactly once, after the event is durable."""
        req(not self._done, 'staged', 'already committed')
        req(self._gate.grammar.last_sequence == self._at_sequence, 'staged',
            'the gate moved since this event was staged (single writer: stage -> persist -> commit, one at a time)')
        self._done = True
        self._grammar_commit()
        self._gate._apply(self._event)
        return Admission.APPLY


class JournalGate:
    """The ONE admission every JournalPort runs. Pure; rebuild() from the durable events after a restart.

    append() protocol (store atomicity; part of the JournalPort contract):
        staged = gate.stage(event)          validate only; raises JournalConflict; None = ALREADY_APPLIED
        <write + fsync the event>           on failure: raise JournalUnavailable, drop `staged` (gate unchanged)
        staged.commit()                     only now do sequence, decision, intent and lineage become consumed
    """

    def __init__(self, account_id: str, aggregate_id: str):
        self.grammar = Grammar(account_id, aggregate_id)
        self._live = {}     # intent_id -> [OrderIntent, IntentState, sent_at_ms | None, final OrderResult | None]

    @classmethod
    def rebuild(cls, account_id, aggregate_id, events: Iterable) -> 'JournalGate':
        gate = cls(account_id, aggregate_id)
        for ev in events:
            gate.admit(ev)
        return gate

    def stage(self, event) -> StagedEvent | None:
        """Validate `event` against the gate without changing it. None = ALREADY_APPLIED (identical re-append)."""
        try:
            commit = self.grammar.prepare(header_of(event))      # order / identity / lineage first
            if commit is None:
                return None
            self._content(event)                                 # then the NC-01 per-record checks
        except DomainError as ex:
            raise JournalConflict(str(ex)) from ex
        return StagedEvent(self, event, commit)

    def admit(self, event) -> Admission:
        """stage + commit at once: ONLY for events that are already durable (rebuild / replay)."""
        staged = self.stage(event)
        return Admission.ALREADY_APPLIED if staged is None else staged.commit()

    def _content(self, ev):
        """NC-01 per-record checks against the current state (pure)."""
        if isinstance(ev, IntentStateChanged) and ev.intent_id in self._live:
            it, state, _, final = self._live[ev.intent_id]
            req(ev.from_state is state, 'event.from_state', f'the intent is {state}')
            req(can_transition(state, ev.to_state), 'event.to_state', f'{state} -> {ev.to_state} is no lifecycle step')
            if ev.to_state in TERMINAL:
                req(final is not None and terminal_for(final) is ev.to_state, 'event.to_state',
                    'the terminal step must be terminal_for(final result)')
        elif isinstance(ev, ResultObserved) and ev.result.intent_id in self._live:
            it, _, sent_at, _ = self._live[ev.result.intent_id]
            check_result_for_intent(it, ev.result, sent_at)

    def _apply(self, ev):
        if isinstance(ev, IntentRecorded):
            self._live[ev.intent.intent_id] = [ev.intent, ev.intent.state, None, None]
        elif isinstance(ev, IntentStateChanged):
            st = self._live[ev.intent_id]
            st[1] = ev.to_state
            if ev.to_state is IntentState.SUBMITTED and st[2] is None:
                st[2] = ev.at_ms
        elif isinstance(ev, ResultObserved) and ev.result.phase is ResultPhase.FINAL:
            self._live[ev.result.intent_id][3] = ev.result


# ---------------------------------------------------------------------------------------------- consumed-signal rule
@dataclass(frozen=True, slots=True)
class SignalClaim:
    fresh: bool                       # True: decide now; False: already consumed, never decide again
    decision_id: str
    intent_ids: tuple[str, ...]       # fresh: the one derived id; consumed: the intents actually recorded


def claim_signal(grammar: Grammar, key: DecisionKey) -> SignalClaim:
    """Map a (possibly re-delivered) signal to its one decision. Pure: it records nothing."""
    check_decision_key(key)
    d = derive_decision_id(grammar.account_id, key)
    if grammar.is_consumed(key):
        return SignalClaim(False, d, grammar.intents_of(d))
    return SignalClaim(True, d, (derive_intent_id(grammar.account_id, key),))


# ---------------------------------------------------------------------------------------------- the port
@runtime_checkable
class JournalPort(Protocol):
    """Append-only, single-writer journal of one account aggregate (NC-02a file journal, S1 MemoryJournal). Every
    implementation admits through JournalGate and must pass tests/newcore_ports/journal_contract.py."""

    def append(self, event: 'DomainEvent') -> Admission:
        """staged = gate().stage(event) -> write + fsync -> staged.commit() -> APPLY. ALREADY_APPLIED writes nothing.
        Raises JournalConflict (refused, nothing written) or JournalUnavailable (store failure: nothing durable AND
        the gate unchanged, so the same event can be appended once the store is writable)."""
        ...

    def last_sequence(self) -> int:
        """Sequence of the last durable event (0 for an empty journal)."""
        ...

    def read(self, after_sequence: int = 0) -> Iterable['DomainEvent']:
        """Durable events with sequence > after_sequence, in sequence order."""
        ...

    def find_decision(self, decision_id: str) -> 'DecisionRecorded | None':
        """The recorded DecisionRecorded event with this decision id, or None (the consumed-signal lookup)."""
        ...

    def gate(self) -> JournalGate:
        """The admission state after the last durable event (claim_signal / next_child_intent_id read it)."""
        ...
