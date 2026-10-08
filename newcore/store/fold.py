"""The journal fold: admission of one event at a time, and the recovered state of the journal (pure, no IO).

Every event passes, in this order, before it is written (append) or accepted (replay):
  1. the step-0 projection `header_of` and the event-id / sequence identity (G2 / G3);
  2. NC-01 `check_event_chain` over the whole chain (record content, the lifecycle table, client-id reuse, one-shots);
  3. NC-01 `ledger.admit` (sequence admission + idempotent re-apply, invariant 12);
  4. the step-0 Grammar G1-G9 (order and idempotence across the lanes).
Nothing changes before all four pass; a refused event leaves the Folder unchanged (the Grammar only advances when it
admits, and a caller whose write then fails poisons itself, so it never reads the Grammar again).

The recovered state is what NC-01 can say from the chain alone:
- ownership is always UNKNOWN: the journal never proves an exchange position (NC-01 invariant 1; reconciliation does);
- each live intent is classified for the Runner (IntentRecovery): DURABLE_NOT_SENT (write-ahead record, no `sent`:
  provably never sent), UNKNOWN_NEEDS_QUERY (sent, no final result: never assumed filled or never-filled; the Runner
  queries the venue by its deterministic client id) or FINAL_NOT_CLOSED (a durable FINAL result whose terminal step was
  not journaled: the terminal step is `terminal_for(result)`);
- `booked` is the net executed quantity per symbol / side from FINAL results only (opening +, reduce-only -). It is a
  journal fact, not a position: NC-01 has no event -> Portfolio apply yet (interface item), so no Portfolio is built.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

from newcore.domain import (DecisionRecorded, EntriesMode, EventCursor, HoldKind, IntentRecorded, IntentState,
                            IntentStateChanged, ModeChanged, Ownership, ResultObserved, admit, check_event_chain,
                            contract_sha256)
from newcore.domain.errors import DomainError, EventOrderError
from newcore.domain.ledger import Admission as LedgerAdmission
from newcore.domain.orders import TERMINAL, ResultPhase
from newcore.ports.journal import Admission, Grammar, JournalConflict
from newcore.ports.values import PortValueError

from .errors import SequenceConflict
from .projection import header_of


class IntentRecovery(enum.StrEnum):
    DURABLE_NOT_SENT = 'durable_not_sent'
    UNKNOWN_NEEDS_QUERY = 'unknown_needs_query'
    FINAL_NOT_CLOSED = 'final_not_closed'


@dataclass(frozen=True, slots=True)
class IntentView:
    intent: object                 # the durable write-ahead OrderIntent (state DURABLE inside the record)
    state: IntentState             # the last journaled lifecycle state
    sent_at_ms: int | None         # when `sent` was journaled; None = provably never sent
    last_result: object | None     # the last OrderResult journaled for it
    final_result: object | None    # its FINAL OrderResult, if any
    recovery: IntentRecovery

    @property
    def intent_id(self):
        return self.intent.intent_id


@dataclass(frozen=True, slots=True)
class JournalState:
    account_id: str
    aggregate_id: str
    last_sequence: int
    ownership: Ownership           # always UNKNOWN from the journal alone
    mode: EntriesMode | None       # the last journaled ModeChanged (None = no mode change journaled)
    hold_kind: HoldKind | None
    intents: tuple                 # live IntentViews, in recorded order
    closed: tuple                  # (intent_id, terminal IntentState), in close order
    decisions: frozenset
    booked: tuple                  # ((symbol, side), net executed Decimal), sorted

    def intent(self, intent_id):
        return next((v for v in self.intents if v.intent_id == intent_id), None)

    def by_recovery(self, kind):
        return tuple(v.intent_id for v in self.intents if v.recovery is kind)

    @property
    def needs_venue_query(self):
        return self.by_recovery(IntentRecovery.UNKNOWN_NEEDS_QUERY)

    @property
    def durable_not_sent(self):
        return self.by_recovery(IntentRecovery.DURABLE_NOT_SENT)


@dataclass(frozen=True, slots=True)
class Prepared:
    event: object
    digest: str
    cursor: object
    live: dict


class Folder:
    """Incremental fold of one journal (one account aggregate)."""

    def __init__(self, account_id, aggregate_id):
        self.account_id, self.aggregate_id = account_id, aggregate_id
        self.cursor = EventCursor(account_id=account_id, aggregate_id=aggregate_id, last_sequence=0, applied=())
        self.grammar = Grammar(account_id, aggregate_id)
        self.events = []
        self.ids = {}                 # event_id -> (sequence, digest)
        self.live = {}
        self.recorded = {}            # intent_id -> OrderIntent
        self.results = {}             # intent_id -> (last OrderResult, final OrderResult | None)
        self.closed = {}
        self.decisions = {}           # decision_id -> DecisionRecorded
        self.mode = self.hold_kind = None
        self.booked = {}

    @property
    def last_sequence(self):
        return len(self.events)

    def prepare(self, ev):
        """Validate `ev` against the chain. Returns Admission.ALREADY_APPLIED (identical re-append) or a Prepared to
        commit once the event is durable. Raises JournalConflict / SequenceConflict and changes nothing."""
        try:
            digest = contract_sha256(ev)
            h = header_of(ev, digest)
        except (PortValueError, DomainError, AttributeError, TypeError) as ex:
            raise JournalConflict(f'not a journal event: {type(ex).__name__}') from None
        if (h.account_id, h.aggregate_id) != (self.account_id, self.aggregate_id):
            raise JournalConflict('G1: event of another account / aggregate')
        seen = self.ids.get(h.event_id)
        if seen is not None:
            if seen == (h.sequence, digest):
                return Admission.ALREADY_APPLIED
            raise SequenceConflict(f'G3: event id re-used with another sequence or other bytes (sequence {h.sequence})')
        if h.sequence != self.last_sequence + 1:
            what = 'already used by another event' if h.sequence <= self.last_sequence else 'a gap'
            raise SequenceConflict(f'G2: sequence {h.sequence} is {what}; expected {self.last_sequence + 1}')
        try:
            live = check_event_chain(self.events + [ev])
        except DomainError as ex:
            raise JournalConflict(f'NC-01 chain: {ex.path}: {ex.msg}') from None
        try:
            cursor, adm = admit(self.cursor, ev)
        except EventOrderError as ex:
            raise SequenceConflict(f'NC-01 admission: {ex.msg}') from None
        if adm is not LedgerAdmission.APPLY:
            raise SequenceConflict('NC-01 admission disagrees with the journal index')
        try:
            self.grammar.admit(h)
        except PortValueError as ex:
            raise JournalConflict(str(ex)) from None
        return Prepared(ev, digest, cursor, live)

    def commit(self, p):
        ev = p.event
        self.events.append(ev)
        self.ids[ev.event_id] = (ev.sequence, p.digest)
        self.cursor, self.live = p.cursor, p.live
        if isinstance(ev, DecisionRecorded):
            self.decisions[ev.decision.decision_id] = ev
        elif isinstance(ev, IntentRecorded):
            self.recorded[ev.intent.intent_id] = ev.intent
        elif isinstance(ev, IntentStateChanged) and ev.to_state in TERMINAL:
            self.closed[ev.intent_id] = ev.to_state
        elif isinstance(ev, ResultObserved):
            r = ev.result
            final = r if r.phase is ResultPhase.FINAL else self.results.get(r.intent_id, (None, None))[1]
            self.results[r.intent_id] = (r, final)
            if r.phase is ResultPhase.FINAL and r.executed_qty:
                it = self.recorded[r.intent_id]
                k = (it.symbol, str(it.side))
                self.booked[k] = self.booked.get(k, Decimal(0)) + (r.executed_qty if it.opening else -r.executed_qty)
        elif isinstance(ev, ModeChanged):
            self.mode, self.hold_kind = ev.to_mode, ev.to_hold

    def admit(self, ev):
        p = self.prepare(ev)
        if p is Admission.ALREADY_APPLIED:
            return p
        self.commit(p)
        return Admission.APPLY

    def state(self):
        views = []
        for iid, (it, st, sent) in self.live.items():
            last, final = self.results.get(iid, (None, None))
            if final is not None:
                rec = IntentRecovery.FINAL_NOT_CLOSED
            elif sent is None:
                rec = IntentRecovery.DURABLE_NOT_SENT
            else:
                rec = IntentRecovery.UNKNOWN_NEEDS_QUERY
            views.append(IntentView(it, st, sent, last, final, rec))
        return JournalState(self.account_id, self.aggregate_id, self.last_sequence, Ownership.UNKNOWN, self.mode,
                            self.hold_kind, tuple(views), tuple(self.closed.items()), frozenset(self.decisions),
                            tuple(sorted(self.booked.items())))


def fold(account_id, aggregate_id, events):
    """Replay events into a fresh Folder (what recovery does). Raises on the first refused event."""
    f = Folder(account_id, aggregate_id)
    for ev in events:
        if f.admit(ev) is not Admission.APPLY:
            raise SequenceConflict(f'event {ev.sequence} appears twice in the journal')
    return f
