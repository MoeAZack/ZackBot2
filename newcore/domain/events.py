"""DomainEvent records (contract 2, invariants 6 and 12; ruling 1). NC-02 stores them; NC-01 defines them, the pure
chain check here and the sequence / idempotency admission in `ledger`.

Every event carries `event_id` (evt_, caller-supplied), `account_id`, `aggregate_id` (the portfolio's pf_ id), an
integer `sequence` (1, 2, 3, ... per aggregate: THE replay ordering key; a timestamp never breaks ties), `at_ms` (integer
UTC-ms evidence time) and one `reason`. Durability order: IntentRecorded (the intent in state DURABLE) is appended
BEFORE the send, and ResultObserved is appended BEFORE the result is applied (the intent's terminal step), for every
purpose including stops and closes.
"""
from __future__ import annotations

import re

from .account import BINDING_TRANSITIONS, AccountBinding, BindingConfirmation, BindingState
from .base import Record, check_id, record, req
from .decision import Decision
from .modes import EntriesMode, HoldKind
from .orders import (INTENT_TRANSITIONS, TERMINAL, IntentState, OrderIntent, OrderResult, ResultPhase,
                     check_result_for_intent, terminal_for)
from .reasons import ReasonCode


class DomainEvent(Record):
    """Common fields (declared on every concrete event): event_id, account_id, aggregate_id, sequence, at_ms, reason."""
    __slots__ = ()

    def _validate(self, p):
        check_id(self.event_id, p + '.event_id', 'evt')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.aggregate_id, p + '.aggregate_id', 'pf')
        req(self.sequence >= 1, p + '.sequence', '>= 1')
        self._check(p)

    def _check(self, p):
        pass


@record
class IntentRecorded(DomainEvent):
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    reason: ReasonCode
    intent: OrderIntent

    def _check(self, p):
        req(self.intent.account_id == self.account_id, p + '.intent', 'intent of another account')
        req(self.intent.state is IntentState.DURABLE, p + '.intent.state', 'the write-ahead record of an unsent intent')
        req(self.at_ms >= self.intent.created_at_ms, p + '.at_ms', 'recorded before it was created')
        req(self.reason is self.intent.reason, p + '.reason', 'the event carries its intent\'s reason')


@record
class IntentStateChanged(DomainEvent):
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    reason: ReasonCode
    intent_id: str
    from_state: IntentState
    to_state: IntentState

    def _check(self, p):
        check_id(self.intent_id, p + '.intent_id', 'int')
        req(self.to_state in INTENT_TRANSITIONS[self.from_state], p + '.to_state',
            f'{self.from_state} -> {self.to_state} is not a lifecycle step')


@record
class ResultObserved(DomainEvent):
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    reason: ReasonCode
    result: OrderResult

    def _check(self, p):
        req(self.result.account_id == self.account_id, p + '.result', 'result of another account')


@record
class DecisionRecorded(DomainEvent):
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    reason: ReasonCode
    decision: Decision

    def _check(self, p):
        req(self.decision.account_id == self.account_id, p + '.decision', 'decision of another account')
        req(self.reason is self.decision.reason, p + '.reason', 'the event carries its decision\'s reason')


@record
class ModeChanged(DomainEvent):
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    reason: ReasonCode
    from_mode: EntriesMode
    to_mode: EntriesMode
    reasons: tuple[ReasonCode, ...]
    from_hold: HoldKind | None
    to_hold: HoldKind | None
    decision_id: str | None           # the operator decision (required to resume)
    reconciliation_id: str | None     # rec_: required to leave HOLD

    def _check(self, p):
        req((self.from_hold is not None) == (self.from_mode is EntriesMode.HOLD), p + '.from_hold', 'set exactly in HOLD')
        req((self.to_hold is not None) == (self.to_mode is EntriesMode.HOLD), p + '.to_hold', 'set exactly in HOLD')
        req((self.from_mode, self.from_hold) != (self.to_mode, self.to_hold), p + '.to_mode', 'not a change')
        req((len(self.reasons) == 0) == (self.to_mode is EntriesMode.ACTIVE), p + '.reasons', 'non-empty unless ACTIVE')
        req(self.reason in self.reasons if self.reasons else self.reason is ReasonCode.OPERATOR_RESUME, p + '.reason',
            'the change names one of its reasons (operator.resume to resume)')
        if self.to_hold is HoldKind.DURABILITY_UNAVAILABLE:
            req(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE in self.reasons, p + '.reasons', 'hard HOLD names its cause')
        if self.decision_id is not None:
            check_id(self.decision_id, p + '.decision_id', 'dec')
        if self.reconciliation_id is not None:
            check_id(self.reconciliation_id, p + '.reconciliation_id', 'rec')
        if self.from_mode is EntriesMode.HOLD and self.to_mode is not EntriesMode.HOLD:
            req(self.reconciliation_id is not None, p + '.reconciliation_id', 'only a reconciliation record leaves HOLD')
        if self.to_mode is EntriesMode.ACTIVE:
            req(self.decision_id is not None, p + '.decision_id', 'opening risk again needs an explicit resume decision')


@record
class BindingChanged(DomainEvent):
    event_id: str
    account_id: str
    aggregate_id: str
    sequence: int
    at_ms: int
    reason: ReasonCode
    from_state: BindingState
    to_state: BindingState
    binding: AccountBinding                  # the account's binding AFTER the change
    confirmation: BindingConfirmation | None
    reconciliation_id: str | None

    def _check(self, p):
        req(self.to_state in BINDING_TRANSITIONS[self.from_state], p + '.to_state',
            f'{self.from_state} -> {self.to_state} is not a binding step')
        typed = (self.to_state is BindingState.RECONCILING or
                 (self.from_state is BindingState.UNCONFIRMED and self.to_state is BindingState.CONFIRMED))
        req((self.confirmation is not None) == typed, p + '.confirmation', 'a typed confirmation exactly where one is required')
        if self.confirmation is not None:
            c = self.confirmation
            req(c.account_id == self.account_id and c.new_key_digest == self.binding.key_digest, p + '.confirmation',
                'confirms another account or key')
            req((c.old_key_digest is None) == (self.from_state is BindingState.UNCONFIRMED), p + '.confirmation',
                'a rotation names the old key; a first bind does not')
        leaving_rec = self.from_state is BindingState.RECONCILING
        req((self.reconciliation_id is not None) == leaving_rec, p + '.reconciliation_id',
            'a rotation is confirmed only by a reconciliation record')
        if self.reconciliation_id is not None:
            check_id(self.reconciliation_id, p + '.reconciliation_id', 'rec')


EVENT_TYPES = (IntentRecorded, IntentStateChanged, ResultObserved, DecisionRecorded, ModeChanged, BindingChanged)


def check_event_chain(events, *, after_sequence=0, known_intents=None):
    """Validate an ordered event list of one aggregate. `after_sequence` is the snapshot's last_sequence.
    `known_intents` maps intent_id ->
    (OrderIntent, state, submitted_at_ms or None) for intents live in that snapshot. Returns the live intents after the
    chain in the same shape. Raises InvalidRecord naming the event.

    Detects: a sequence gap / reorder, another account's or aggregate's event, a result for an intent that was never made durable, a
    terminal step without a durable FINAL result (applied before recorded) or not matching it, any event after a terminal
    state, a reused id or client id, and a one-shot authorization that is missing or used twice."""
    live = dict(known_intents or {})
    cids = {c for it, _, _ in live.values() for c in it.client_ids}
    finals, ended, decisions, one_shots, used_auth = {}, set(), set(), set(), set()
    owner = None
    for n, ev in enumerate(events):
        p = f'events[{n}]'
        req(isinstance(ev, EVENT_TYPES), p, 'not an event')
        req(ev.sequence == after_sequence + n + 1, p + '.sequence',
            f'expected sequence {after_sequence + n + 1} (gap, reorder or rollback)')
        owner = owner or (ev.account_id, ev.aggregate_id)
        req((ev.account_id, ev.aggregate_id) == owner, p + '.account_id', 'event of another account / aggregate in this log')
        if isinstance(ev, DecisionRecorded):
            d = ev.decision
            req(d.decision_id not in decisions, p, 'decision recorded twice')
            decisions.add(d.decision_id)
            if d.reason is ReasonCode.OPERATOR_ONE_SHOT:
                one_shots.add(d.decision_id)
        elif isinstance(ev, IntentRecorded):
            it = ev.intent
            req(it.intent_id not in live and it.intent_id not in ended, p, 'intent recorded twice')
            req(not (set(it.client_ids) & cids), p + '.intent.client_order_id', 'a client order id is never reused')
            if it.authorized_by is not None:
                req(it.authorized_by in one_shots, p + '.intent.authorized_by', 'names no recorded operator one-shot')
                req(it.authorized_by not in used_auth, p + '.intent.authorized_by', 'a one-shot authorizes one intent')
                used_auth.add(it.authorized_by)
            cids.update(it.client_ids)
            live[it.intent_id] = (it, it.state, None)
        elif isinstance(ev, IntentStateChanged):
            req(ev.intent_id not in ended, p, 'event after a terminal state (terminal never returns to active)')
            req(ev.intent_id in live, p, 'state change of an intent that was never made durable')
            it, st, sent = live[ev.intent_id]
            req(ev.from_state is st, p + '.from_state', f'the intent is {st}')
            fin = finals.get(ev.intent_id)
            if ev.to_state in TERMINAL:
                req(fin is not None, p + '.to_state', 'a terminal step needs a durable FINAL result first')
                req(terminal_for(fin) is ev.to_state, p + '.to_state', f'the final result means {terminal_for(fin)}')
                ended.add(ev.intent_id)
                del live[ev.intent_id]
                continue
            req(fin is None, p + '.to_state', 'after a FINAL result only its terminal step may follow')
            if ev.to_state is IntentState.SUBMITTED and sent is None:
                sent = ev.at_ms
            live[ev.intent_id] = (it, ev.to_state, sent)
        elif isinstance(ev, ResultObserved):
            r = ev.result
            req(r.intent_id not in ended and r.intent_id not in finals, p, 'event after the final result')
            req(r.intent_id in live, p, 'a result for an intent that was never made durable')
            it, st, sent = live[r.intent_id]
            check_result_for_intent(it, r, sent)
            if r.phase is ResultPhase.FINAL:
                finals[r.intent_id] = r
    return live


# ------------------------------------------------------------------------------------------------ admission cursor
SHA_RE = re.compile(r'[0-9a-f]{64}')


@record
class EventDigest(Record):
    event_id: str
    sequence: int
    sha256: str

    def _validate(self, p):
        check_id(self.event_id, p + '.event_id', 'evt')
        req(self.sequence >= 1, p + '.sequence', '>= 1')
        req(SHA_RE.fullmatch(self.sha256) is not None, p + '.sha256', 'lowercase hex SHA-256')


@record
class EventCursor(Record):
    account_id: str
    aggregate_id: str
    last_sequence: int
    applied: tuple[EventDigest, ...]          # applied[i] has sequence i + 1

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.aggregate_id, p + '.aggregate_id', 'pf')
        req(self.last_sequence == len(self.applied), p + '.last_sequence', 'one digest per applied sequence')
        req(all(d.sequence == i + 1 for i, d in enumerate(self.applied)), p + '.applied', 'digests out of sequence')
        ids = [d.event_id for d in self.applied]
        req(len(ids) == len(set(ids)), p + '.applied', 'an event id applied twice')
