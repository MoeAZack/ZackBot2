"""The journal fold: admission of one event at a time, and the recovered state of the journal (pure, no IO).

Admission (STEP0_INTERFACE.md r3 section 3, store atomicity): `stage` = the ONE step-0 `JournalGate.stage` (header_of
-> Grammar G1-G10 -> NC-01 per-record checks), validation only; `commit` = `staged.commit()`, called by the journal
only after the event is durable, so a failed write consumes nothing. The gate's refusal (JournalConflict, exact reason)
changes nothing; G2 / G3 refusals (gap, used sequence, reused id with other bytes) are re-raised as the typed
`SequenceConflict` (a JournalConflict, same message). As a tripwire NC-01 `check_event_chain` also runs over the whole
chain before the write (pure): if it ever refused what the gate admits, the event is refused (a gate gap to report).
At boot `replay` rebuilds the gate with `JournalGate.rebuild(durable events)` (the only use of admit), then one chain
check over the whole journal.

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

from newcore.domain import (DecisionRecorded, EntriesMode, HoldKind, IntentRecorded, IntentState, IntentStateChanged,
                            ModeChanged, Ownership, ResultObserved, check_event_chain)
from newcore.domain.errors import DomainError
from newcore.domain.orders import TERMINAL, ResultPhase
from newcore.ports.journal import Admission, JournalConflict, JournalGate

from .errors import SequenceConflict

_SEQUENCE_REASONS = ('G2:', 'G3:')


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
    staged: object                 # the gate's StagedEvent: committed only after the event is durable
    live: dict

    @property
    def event(self):
        return self.staged.event


def _seq_typed(fn):
    """Re-raise the gate's G2 / G3 refusals as the typed SequenceConflict (same message)."""
    try:
        return fn()
    except SequenceConflict:
        raise
    except JournalConflict as ex:
        msg = str(ex)
        if any(r in msg for r in _SEQUENCE_REASONS):
            raise SequenceConflict(msg) from ex
        raise


class Folder:
    """Incremental fold of one journal (one account aggregate)."""

    def __init__(self, account_id, aggregate_id, *, facts=None, after_sequence=0, known_intents=None):
        """facts / after_sequence / known_intents: start from a snapshot (a compacted journal) - the NC-01 r3a seed
        (Snapshot.facts, Snapshot.last_sequence, the snapshot's live intents). Defaults: the whole journal."""
        self.account_id, self.aggregate_id = account_id, aggregate_id
        self.base, self.base_facts = after_sequence, facts
        self.base_known = dict(known_intents or {})
        self.gate = JournalGate(account_id, aggregate_id, facts=facts, after_sequence=after_sequence)
        self.events = []              # the events AFTER self.base
        self.live = dict(self.base_known)
        self.recorded = {iid: v[0] for iid, v in self.base_known.items()}     # intent_id -> OrderIntent
        self.results = {}             # intent_id -> (last OrderResult, final OrderResult | None)
        self.closed = {}
        self.decisions = {}           # decision_id -> DecisionRecorded
        self.mode = self.hold_kind = None
        self.booked = {}

    @property
    def last_sequence(self):
        return self.base + len(self.events)

    def events_after(self, after_sequence):
        """The held events with sequence > after_sequence. Before the seed point they are compacted away: refused."""
        if after_sequence < self.base:
            raise ValueError('events before the snapshot this journal was seeded from are compacted')
        return tuple(self.events[after_sequence - self.base:])

    def _chain(self, events):
        return check_event_chain(events, after_sequence=self.base, known_intents=self.base_known or None,
                                 facts=self.base_facts)

    def stage(self, ev):
        """gate().stage(ev): validate only. Returns Admission.ALREADY_APPLIED (identical re-append) or a Prepared to
        commit once the event is durable. Raises JournalConflict / SequenceConflict. Nothing changes here."""
        staged = _seq_typed(lambda: self.gate.stage(ev))
        if staged is None:
            return Admission.ALREADY_APPLIED
        try:
            live = self._chain(self.events + [ev])                  # tripwire: never refuses what the gate admits
        except DomainError as ex:
            raise JournalConflict(f'NC-01 chain refuses what the gate admitted ({ex.path}: {ex.msg})') from None
        return Prepared(staged, live)

    def commit(self, p):
        """After the event is durable: staged.commit(), then the journal's own indexes."""
        p.staged.commit()
        ev = p.event
        self.events.append(ev)
        self.live = p.live
        self._index(ev)

    def _index(self, ev):
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

    @classmethod
    def replay(cls, account_id, aggregate_id, events, *, facts=None, after_sequence=0, known_intents=None):
        """Boot: JournalGate.rebuild from the durable events (the only use of admit), then one NC-01 chain check over
        all of them. Raises JournalConflict (incl. an event stored twice) on the first refusal.
        After compaction pass the snapshot's FactIndex, last sequence and live intents with only the tail events:
        BOTH layers are seeded (JournalGate.rebuild(..., facts=, after_sequence=) and check_event_chain(...,
        after_sequence=, known_intents=, facts=)), so a fact the compacted prefix journaled stays exactly-once."""
        events = list(events)
        f = cls(account_id, aggregate_id, facts=facts, after_sequence=after_sequence, known_intents=known_intents)
        f.gate = _seq_typed(lambda: JournalGate.rebuild(account_id, aggregate_id, events, facts=facts,
                                                        after_sequence=after_sequence))
        if f.gate.grammar.last_sequence != after_sequence + len(events):
            raise SequenceConflict('an event is stored twice')
        try:
            f.live = f._chain(events)
        except DomainError as ex:
            raise JournalConflict(f'NC-01 chain: {ex.path}: {ex.msg}') from None
        for ev in events:
            f.events.append(ev)
            f._index(ev)
        return f

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
    """Replay events into a fresh Folder (what recovery does)."""
    return Folder.replay(account_id, aggregate_id, events)
