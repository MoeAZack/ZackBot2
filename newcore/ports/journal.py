"""JournalPort and its one event grammar (STEP0_INTERFACE.md section 3).

The journal stores NC-01 DomainEvents. The grammar is checked over a header projection of each event (EventHeader), so
it is testable before NC-01 merges; `header_of(event)` is written when the port is bound to newcore.domain. NC-01's
`check_event_chain` / `ledger.admit` stay the authority on record content; this grammar is the cross-lane contract on
ORDER and IDEMPOTENCE, and is deliberately coarser (it never duplicates the NC-01 lifecycle table).

Rules (G1-G9), applied by Grammar.admit:
  G1 one journal = one (account_id, aggregate_id).
  G2 sequence is exactly last + 1 (no gap, no reuse); a timestamp never orders events.
  G3 an event_id seen before is ALREADY_APPLIED (no-op) iff same sequence and same digest; otherwise a hard conflict.
  G4 decision_recorded: decision_id is new; a keyed decision's id is derive_decision_id(account, key), so one signal
     is consumed exactly once.
  G5 intent_recorded: intent_id is new (never re-recorded, even after it ended); its decision is already recorded; a
     keyed decision's intents have derived ids (some leg) and the key's purpose; client ids are exactly the derived
     ones (classic first, algo only for protect) and never used before.
  G6 sent: only from recorded (durable, not yet sent).
  G7 state_changed (working / unknown / cancelling): the intent is live and has no final result; working / unknown
     only after sent.
  G8 result_recorded: the intent is recorded (never a result before its intent) and has no final result yet; a
     never-sent intent only takes a final not_sent result; final names its evidence, nothing else does.
  G9 intent_closed (terminal state): only after a final result; not_sent <=> not_sent evidence; nothing follows it.
  mode_changed, binding_changed and incident_recorded carry no intent and only obey G1-G3.
Admission is atomic: a refused event leaves the Grammar unchanged.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from .keys import MAX_LEGS, DecisionKey, client_id_for, derive_decision_id, derive_intent_id
from .values import PURPOSES, PortValueError, check_choice, check_client_id, check_id, check_int, check_ms, plain, req

SHA_RE = re.compile(r'[0-9a-f]{64}')


class EventKind(enum.StrEnum):
    DECISION_RECORDED = 'decision_recorded'    # NC-01 DecisionRecorded
    INTENT_RECORDED = 'intent_recorded'        # NC-01 IntentRecorded (intent DURABLE: write-ahead, before the send)
    SENT = 'sent'                              # NC-01 IntentStateChanged durable -> submitted
    STATE_CHANGED = 'state_changed'            # NC-01 IntentStateChanged -> working / unknown / cancelling
    RESULT_RECORDED = 'result_recorded'        # NC-01 ResultObserved (durable before it is applied)
    INTENT_CLOSED = 'intent_closed'            # NC-01 IntentStateChanged -> filled / cancelled / rejected / not_sent
    MODE_CHANGED = 'mode_changed'              # NC-01 ModeChanged (HOLD enter / leave, pause, halt, resume)
    BINDING_CHANGED = 'binding_changed'        # NC-01 BindingChanged
    INCIDENT_RECORDED = 'incident_recorded'    # NC-02 IncidentRecorded (no NC-01 record yet: see section 6)


class ResultOutcome(enum.StrEnum):
    FINAL = 'final'            # NC-01 ResultPhase.FINAL (+ evidence)
    KNOWN = 'known'            # ResultPhase.KNOWN: open on the venue, executed qty not trusted
    UNKNOWN = 'unknown'        # ResultPhase.UNKNOWN, lookup None / unreadable
    NOT_FOUND = 'not_found'    # ResultPhase.UNKNOWN + Lookup.NOT_FOUND: proves nothing, books nothing


class Admission(enum.StrEnum):     # values equal NC-01 ledger.Admission
    APPLY = 'apply'
    ALREADY_APPLIED = 'already_applied'


EVIDENCE = ('exchange_final', 'exchange_refused', 'not_sent', 'not_found_corroborated', 'position_adopted')
LIVE_STATES = ('working', 'unknown', 'cancelling')
TERMINAL_STATES = ('filled', 'cancelled', 'rejected', 'not_sent')
_INTENT_KINDS = frozenset({EventKind.INTENT_RECORDED, EventKind.SENT, EventKind.STATE_CHANGED,
                           EventKind.RESULT_RECORDED, EventKind.INTENT_CLOSED})


class GrammarError(PortValueError):
    """The event breaks the journal grammar: a hard failure, never skipped."""


@dataclass(frozen=True, slots=True, kw_only=True)
class EventHeader:
    """What the grammar sees of one DomainEvent. `digest` = NC-01 contract_sha256(event)."""
    kind: EventKind
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    digest: str
    decision_id: str | None = None          # decision_recorded; intent_recorded (the creating decision)
    decision_key: DecisionKey | None = None  # decision_recorded of a strategy decision
    intent_id: str | None = None            # every intent kind
    purpose: str | None = None              # intent_recorded
    client_ids: tuple[str, ...] = ()        # intent_recorded: (classic,) or (classic, algo)
    to_state: str | None = None             # state_changed / intent_closed
    outcome: ResultOutcome | None = None    # result_recorded
    evidence: str | None = None             # result_recorded, final only

    def __post_init__(self):
        object.__setattr__(self, 'kind', EventKind(plain(self.kind)))
        k, p = self.kind, f'EventHeader[{self.event_id}]'
        check_id(self.event_id, p + '.event_id', 'evt')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.aggregate_id, p + '.aggregate_id', 'pf')
        check_int(self.sequence, p + '.sequence', 1, 2 ** 63 - 1)
        check_ms(self.at_ms, p + '.at_ms')
        req(isinstance(self.digest, str) and SHA_RE.fullmatch(self.digest), p + '.digest', 'lowercase hex sha256')
        req((self.decision_id is not None) == (k in (EventKind.DECISION_RECORDED, EventKind.INTENT_RECORDED)),
            p + '.decision_id', f'set exactly on decision / intent records ({k})')
        if self.decision_id is not None:
            check_id(self.decision_id, p + '.decision_id', 'dec')
        req(self.decision_key is None or (k is EventKind.DECISION_RECORDED and isinstance(self.decision_key, DecisionKey)),
            p + '.decision_key', 'a DecisionKey, on decision_recorded only')
        req((self.intent_id is not None) == (k in _INTENT_KINDS), p + '.intent_id', f'set exactly on intent kinds ({k})')
        if self.intent_id is not None:
            check_id(self.intent_id, p + '.intent_id', 'int')
        is_rec = k is EventKind.INTENT_RECORDED
        req((self.purpose is not None) == is_rec and (bool(self.client_ids) == is_rec), p + '.purpose',
            'purpose and client ids exactly on intent_recorded')
        if is_rec:
            object.__setattr__(self, 'purpose', plain(self.purpose))
            check_choice(self.purpose, p + '.purpose', PURPOSES)
            req(type(self.client_ids) is tuple and 1 <= len(self.client_ids) <= 2, p + '.client_ids', '1 or 2 ids')
            for c in self.client_ids:
                check_client_id(c, p + '.client_ids')
        states = {EventKind.STATE_CHANGED: LIVE_STATES, EventKind.INTENT_CLOSED: TERMINAL_STATES}.get(k)
        req((self.to_state is not None) == (states is not None), p + '.to_state', f'set exactly on state kinds ({k})')
        if states is not None:
            check_choice(self.to_state, p + '.to_state', states)
        req((self.outcome is not None) == (k is EventKind.RESULT_RECORDED), p + '.outcome', 'on result_recorded only')
        if self.outcome is not None:
            object.__setattr__(self, 'outcome', ResultOutcome(plain(self.outcome)))
            final = self.outcome is ResultOutcome.FINAL
            req((self.evidence is not None) == final, p + '.evidence', 'a final result names its evidence; no other does')
        else:
            req(self.evidence is None, p + '.evidence', 'on result_recorded only')
        if self.evidence is not None:
            check_choice(self.evidence, p + '.evidence', EVIDENCE)


@dataclass(slots=True)
class _Intent:
    decision_id: str
    sent: bool = False
    final_evidence: str | None = None
    closed: bool = False


class Grammar:
    """Pure grammar state of one journal: replay a journal into a fresh Grammar to rebuild it after a restart."""

    def __init__(self, account_id: str, aggregate_id: str):
        check_id(account_id, 'Grammar.account_id', 'acct')
        check_id(aggregate_id, 'Grammar.aggregate_id', 'pf')
        self.account_id, self.aggregate_id = account_id, aggregate_id
        self.last_sequence = 0
        self._events: dict[str, tuple[int, str]] = {}
        self._decisions: dict[str, DecisionKey | None] = {}
        self._intents: dict[str, _Intent] = {}
        self._client_ids: set[str] = set()

    # ------------------------------------------------------------------------------------------------ queries
    def is_consumed(self, key: DecisionKey) -> bool:
        return derive_decision_id(self.account_id, key) in self._decisions

    def intents_of(self, decision_id: str) -> tuple[str, ...]:
        return tuple(i for i, st in self._intents.items() if st.decision_id == decision_id)

    # ------------------------------------------------------------------------------------------------ admission
    def admit(self, h: EventHeader) -> Admission:
        req(isinstance(h, EventHeader), 'event', 'an EventHeader')
        p = f'event[{h.event_id}]'
        if (h.account_id, h.aggregate_id) != (self.account_id, self.aggregate_id):
            raise GrammarError(p + '.aggregate_id', 'G1: event of another account / aggregate')
        seen = self._events.get(h.event_id)
        if seen is not None:
            if seen == (h.sequence, h.digest):
                return Admission.ALREADY_APPLIED
            raise GrammarError(p + '.event_id', 'G3: event id re-used with another sequence or different bytes')
        if h.sequence != self.last_sequence + 1:
            what = 'a gap' if h.sequence > self.last_sequence + 1 else 'an already-used sequence (out of order)'
            raise GrammarError(p + '.sequence', f'G2: {h.sequence} is {what}; expected {self.last_sequence + 1}')
        commit = self._check(h, p)
        commit()
        self._events[h.event_id] = (h.sequence, h.digest)
        self.last_sequence = h.sequence
        return Admission.APPLY

    def _check(self, h, p):
        """Validate without mutating; return the mutation to apply."""
        k = h.kind
        if k is EventKind.DECISION_RECORDED:
            if h.decision_id in self._decisions:
                raise GrammarError(p, 'G4: decision recorded twice (a consumed signal is never decided again)')
            if h.decision_key is not None and h.decision_id != derive_decision_id(self.account_id, h.decision_key):
                raise GrammarError(p + '.decision_id', 'G4: a keyed decision id must be derive_decision_id(account, key)')
            return lambda: self._decisions.__setitem__(h.decision_id, h.decision_key)
        if k is EventKind.INTENT_RECORDED:
            return self._check_intent_recorded(h, p)
        if k not in _INTENT_KINDS:
            return lambda: None
        st = self._intents.get(h.intent_id)
        if st is None:
            raise GrammarError(p, f'G6-G9: {k} before the intent was recorded')
        if st.closed:
            raise GrammarError(p, f'G9: {k} after the intent closed')
        if k is EventKind.SENT:
            req_g(not st.sent and st.final_evidence is None, p, 'G6: sent twice or after its final result')
            return lambda: setattr(st, 'sent', True)
        if k is EventKind.STATE_CHANGED:
            req_g(st.final_evidence is None, p, 'G7: after a final result only intent_closed may follow')
            req_g(st.sent or h.to_state == 'cancelling', p, f'G7: {h.to_state} before sent')
            return lambda: None
        if k is EventKind.RESULT_RECORDED:
            req_g(st.final_evidence is None, p, 'G8: a result after the final result')
            if not st.sent:
                req_g(h.outcome is ResultOutcome.FINAL and h.evidence == 'not_sent', p,
                      'G8: a never-sent intent only takes a final not_sent result')
            else:
                req_g(h.evidence != 'not_sent', p, 'G8: the intent was sent; not_sent is impossible')
            if h.outcome is ResultOutcome.FINAL:
                return lambda: setattr(st, 'final_evidence', h.evidence)
            return lambda: None
        # INTENT_CLOSED
        req_g(st.final_evidence is not None, p, 'G9: closed before its final result was recorded')
        req_g((h.to_state == 'not_sent') == (st.final_evidence == 'not_sent'), p,
              'G9: not_sent terminal <=> not_sent evidence')
        return lambda: setattr(st, 'closed', True)

    def _check_intent_recorded(self, h, p):
        if h.intent_id in self._intents:
            raise GrammarError(p, 'G5: intent recorded twice (one signal never creates a second intent)')
        if h.decision_id not in self._decisions:
            raise GrammarError(p + '.decision_id', 'G5: intent recorded before its decision')
        key = self._decisions[h.decision_id]
        if key is not None:
            req_g(h.purpose == key.purpose, p + '.purpose', 'G5: purpose differs from the decision key')
            req_g(h.intent_id in {derive_intent_id(self.account_id, key, leg) for leg in range(MAX_LEGS)},
                  p + '.intent_id', 'G5: a keyed intent id must be derive_intent_id(account, key, leg)')
        want = (client_id_for(h.intent_id, 'classic'),) + (
            (client_id_for(h.intent_id, 'algo'),) if len(h.client_ids) == 2 else ())
        req_g(h.client_ids == want, p + '.client_ids', 'G5: client ids must be client_id_for(intent, classic[, algo])')
        req_g(len(h.client_ids) == 1 or h.purpose == 'protect', p + '.client_ids', 'G5: only protect has an algo id')
        req_g(not (set(h.client_ids) & self._client_ids), p + '.client_ids', 'G5: a client id is never reused')

        def commit():
            self._intents[h.intent_id] = _Intent(decision_id=h.decision_id)
            self._client_ids.update(h.client_ids)
        return commit


def req_g(cond, path, msg):
    if not cond:
        raise GrammarError(path, msg)


def replay(account_id: str, aggregate_id: str, headers: Iterable[EventHeader]) -> Grammar:
    """Fold a journal into a fresh Grammar (what a restart does). Raises GrammarError on the first bad event."""
    g = Grammar(account_id, aggregate_id)
    for h in headers:
        g.admit(h)
    return g


# ---------------------------------------------------------------------------------------------- consumed-signal rule
@dataclass(frozen=True, slots=True)
class SignalClaim:
    fresh: bool                       # True: decide now; False: already consumed, never decide again
    decision_id: str
    intent_ids: tuple[str, ...]       # fresh: the derived ids to use; consumed: the intents actually recorded


def claim_signal(grammar: Grammar, key: DecisionKey, legs: int = 1) -> SignalClaim:
    """Map a (possibly re-delivered) signal to its one decision. Pure: it reads the grammar, it records nothing."""
    check_int(legs, 'legs', 1, MAX_LEGS)
    d = derive_decision_id(grammar.account_id, key)
    if grammar.is_consumed(key):
        return SignalClaim(False, d, grammar.intents_of(d))
    return SignalClaim(True, d, tuple(derive_intent_id(grammar.account_id, key, leg) for leg in range(legs)))


# ---------------------------------------------------------------------------------------------- the port
class JournalConflict(Exception):
    """append refused by the grammar / NC-01 admission: a hard failure (HOLD), never retried with the same event."""


class JournalUnavailable(Exception):
    """The store cannot make the event durable (ENOSPC, EROFS, ...): nothing may be sent (NC-02 A21 / hard HOLD)."""


@runtime_checkable
class JournalPort(Protocol):
    """Append-only, single-writer journal of one account aggregate. Implemented by NC-02a (file) and by the slice's
    MemoryJournal. Event types are NC-01 DomainEvents (forward reference until NC-01 merges)."""

    def append(self, event: 'DomainEvent') -> Admission:
        """Durable (fsync'd) before it returns APPLY; ALREADY_APPLIED for an identical re-append (G3).
        Raises JournalConflict (grammar / admission) or JournalUnavailable (store failure)."""
        ...

    def last_sequence(self) -> int:
        """Sequence of the last durable event (0 for an empty journal)."""
        ...

    def read(self, after_sequence: int = 0) -> Iterable['DomainEvent']:
        """Durable events with sequence > after_sequence, in sequence order."""
        ...

    def find_decision(self, decision_id: str) -> 'DecisionRecorded | None':
        """The recorded decision with this id, or None. The consumed-signal lookup."""
        ...
