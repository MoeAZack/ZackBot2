"""Append-only events (ruling 1). NC-02 stores them; NC-01 defines them and the pure chain check.

Durability order: IntentRecorded is durable BEFORE the send and ResultObserved is durable BEFORE it is applied, for every
purpose including stops and closes. `check_event_chain` makes violations detectable in a log: a result for an intent
that was never recorded, a lifecycle step the table forbids, an event after a final result, a reused client id, a gap in
the sequence or a one-shot authorization used twice.
"""
from __future__ import annotations

from .account import BINDING_TRANSITIONS, AccountBinding, BindingConfirmation, BindingState
from .base import Record, check_id, record, req
from .decision import Decision
from .modes import EntriesMode, HoldKind
from .orders import INTENT_TRANSITIONS, IntentState, OrderIntent, OrderResult, ResultPhase, check_result_for_intent
from .reasons import ReasonCode


class Event(Record):
    """Common fields: `seq` (1, 2, 3, ... per account, no gaps), `account_id`, `at_ms`."""
    __slots__ = ()

    def _validate(self, p):
        req(self.seq >= 1, p + '.seq', '>= 1')
        check_id(self.account_id, p + '.account_id', 'acct')
        self._check(p)

    def _check(self, p):
        pass


@record
class IntentRecorded(Event):
    seq: int
    account_id: str
    at_ms: int
    intent: OrderIntent

    def _check(self, p):
        req(self.intent.account_id == self.account_id, p + '.intent', 'intent of another account')
        req(self.intent.state in (IntentState.ARMED, IntentState.SENDING), p + '.intent.state',
            'recorded before it is sent (ARMED or SENDING)')
        req(self.at_ms >= self.intent.created_at_ms, p + '.at_ms', 'recorded before it was created')


@record
class IntentStateChanged(Event):
    seq: int
    account_id: str
    at_ms: int
    intent_id: str
    from_state: IntentState
    to_state: IntentState

    def _check(self, p):
        check_id(self.intent_id, p + '.intent_id', 'int')
        req(self.to_state in INTENT_TRANSITIONS[self.from_state], p + '.to_state',
            f'{self.from_state} -> {self.to_state} is not a lifecycle step')


@record
class ResultObserved(Event):
    seq: int
    account_id: str
    at_ms: int
    result: OrderResult

    def _check(self, p):
        req(self.result.account_id == self.account_id, p + '.result', 'result of another account')


@record
class DecisionRecorded(Event):
    seq: int
    account_id: str
    at_ms: int
    decision: Decision

    def _check(self, p):
        req(self.decision.account_id == self.account_id, p + '.decision', 'decision of another account')


@record
class ModeChanged(Event):
    seq: int
    account_id: str
    at_ms: int
    from_mode: EntriesMode
    to_mode: EntriesMode
    reasons: tuple[ReasonCode, ...]
    from_hold: HoldKind | None = None
    to_hold: HoldKind | None = None
    decision_id: str | None = None           # the operator decision (required to resume)
    reconciliation_id: str | None = None     # rec_: required to leave HOLD

    def _check(self, p):
        req((self.from_hold is not None) == (self.from_mode is EntriesMode.HOLD), p + '.from_hold', 'set exactly in HOLD')
        req((self.to_hold is not None) == (self.to_mode is EntriesMode.HOLD), p + '.to_hold', 'set exactly in HOLD')
        req((self.from_mode, self.from_hold) != (self.to_mode, self.to_hold), p + '.to_mode', 'not a change')
        req((len(self.reasons) == 0) == (self.to_mode is EntriesMode.ACTIVE), p + '.reasons', 'non-empty unless ACTIVE')
        if self.decision_id is not None:
            check_id(self.decision_id, p + '.decision_id', 'dec')
        if self.reconciliation_id is not None:
            check_id(self.reconciliation_id, p + '.reconciliation_id', 'rec')
        if self.from_mode is EntriesMode.HOLD and self.to_mode is not EntriesMode.HOLD:
            req(self.reconciliation_id is not None, p + '.reconciliation_id', 'only a reconciliation record leaves HOLD')
        if self.to_mode is EntriesMode.ACTIVE:
            req(self.decision_id is not None, p + '.decision_id', 'opening risk again needs an explicit resume decision')


@record
class BindingChanged(Event):
    seq: int
    account_id: str
    at_ms: int
    from_state: BindingState
    to_state: BindingState
    binding: AccountBinding                  # the account's binding AFTER the change
    confirmation: BindingConfirmation | None = None
    reconciliation_id: str | None = None

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


def check_event_chain(events, *, after_seq=0, known_intents=None):
    """Validate an ordered event list. `after_seq` is the snapshot's last_seq. `known_intents` maps intent_id ->
    (OrderIntent, state, sent_at_ms or None) for intents open in that snapshot. Returns the open intents after the
    chain, in the same shape. Raises InvalidRecord naming the event."""
    open_ = dict(known_intents or {})
    cids = {c for it, _, _ in open_.values() for c in it.client_ids}
    finals, decisions, one_shots, used_auth = set(), set(), set(), set()
    account = None
    for n, ev in enumerate(events):
        p = f'events[{n}]'
        req(isinstance(ev, EVENT_TYPES), p, 'not an event')
        req(ev.seq == after_seq + n + 1, p + '.seq', f'expected seq {after_seq + n + 1} (gap, reorder or rollback)')
        account = account or ev.account_id
        req(ev.account_id == account, p + '.account_id', 'event of another account in this log')
        if isinstance(ev, DecisionRecorded):
            d = ev.decision
            req(d.decision_id not in decisions, p, 'decision recorded twice')
            decisions.add(d.decision_id)
            if d.reason is ReasonCode.OPERATOR_ONE_SHOT:
                one_shots.add(d.decision_id)
        elif isinstance(ev, IntentRecorded):
            it = ev.intent
            req(it.intent_id not in open_ and it.intent_id not in finals, p, 'intent recorded twice')
            req(not (set(it.client_ids) & cids), p + '.intent.client_order_id', 'a client order id is never reused')
            if it.authorized_by is not None:
                req(it.authorized_by in one_shots, p + '.intent.authorized_by', 'names no recorded operator one-shot')
                req(it.authorized_by not in used_auth, p + '.intent.authorized_by', 'a one-shot authorizes one intent')
                used_auth.add(it.authorized_by)
            cids.update(it.client_ids)
            open_[it.intent_id] = (it, it.state, ev.at_ms if it.state is IntentState.SENDING else None)
        elif isinstance(ev, IntentStateChanged):
            req(ev.intent_id not in finals, p, 'event after the final result')
            req(ev.intent_id in open_, p, 'state change of an intent that was never recorded')
            it, st, sent = open_[ev.intent_id]
            req(ev.from_state is st, p + '.from_state', f'the intent is {st}')
            open_[ev.intent_id] = (it, ev.to_state, ev.at_ms if ev.to_state is IntentState.SENDING else sent)
        elif isinstance(ev, ResultObserved):
            r = ev.result
            req(r.intent_id not in finals, p, 'event after the final result')
            req(r.intent_id in open_, p, 'a result for an intent that was never made durable (applied before recorded)')
            it, st, sent = open_[r.intent_id]
            check_result_for_intent(it, r, sent)
            if r.phase is ResultPhase.FINAL:
                finals.add(r.intent_id)
                del open_[r.intent_id]
    return open_
