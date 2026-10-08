"""Decisions: what the pure core decided, with one machine reason and the intents the shell will make durable and send.

Every Decision carries exactly one ReasonCode whose stage fits its action (ACTION_STAGES), plus a display-only `detail`
(<= 160 printable characters, never parsed). Its intents are NEW intents (ARMED / SENDING) created by this decision.
`priority` orders a scheduler queue: PROTECT before FLATTEN / CLOSE / REDUCE before anything that opens risk, so under
budget pressure an entry can never pre-empt protection.
"""
from __future__ import annotations

import enum

from .base import Record, check_id, record, req
from .orders import IntentState, OrderIntent, Purpose, Side
from .reasons import GATE_STAGES, STAGES, ReasonCode


class Action(enum.StrEnum):
    PROTECT = 'protect'
    FLATTEN = 'flatten'
    CLOSE = 'close'
    REDUCE = 'reduce'
    HALT = 'halt'
    PAUSE = 'pause'
    CANCEL_ENTRY = 'cancel_entry'
    RECONCILE = 'reconcile'
    RESUME = 'resume'
    ADD = 'add'
    ENTER = 'enter'
    WAIT = 'wait'            # decided to do nothing now
    SKIP = 'skip'            # a signal not taken (gate reason)


PRIORITY_ORDER = tuple(Action)               # declaration order IS the priority (lower index runs first)
PRIORITY = {a: i for i, a in enumerate(PRIORITY_ORDER)}

ACTION_STAGES = {
    Action.PROTECT: frozenset({'protect'}),
    Action.FLATTEN: frozenset({'exit'}),
    Action.CLOSE: frozenset({'exit'}),
    Action.REDUCE: frozenset({'exit'}),
    Action.HALT: frozenset({'filter', 'operator', 'recovery', 'risk_gateway'}),
    Action.PAUSE: frozenset({'operator', 'recovery', 'account', 'connectivity', 'filter'}),
    Action.CANCEL_ENTRY: frozenset({'drain', 'trailing', 'execution', 'filter', 'operator', 'capacity', 'risk_gateway'}),
    Action.RECONCILE: frozenset({'recovery', 'order', 'operator', 'account'}),
    Action.RESUME: frozenset({'operator'}),
    Action.ADD: frozenset({'entry'}),
    Action.ENTER: frozenset({'entry'}),
    Action.WAIT: frozenset(STAGES),
    Action.SKIP: GATE_STAGES,
}
ACTION_PURPOSES = {
    Action.PROTECT: frozenset({Purpose.PROTECT}),
    Action.FLATTEN: frozenset({Purpose.CLOSE}),
    Action.CLOSE: frozenset({Purpose.CLOSE}),
    Action.REDUCE: frozenset({Purpose.REDUCE}),
    Action.RECONCILE: frozenset({Purpose.PROTECT, Purpose.CLOSE}),
    Action.ADD: frozenset({Purpose.ADD}),
    Action.ENTER: frozenset({Purpose.ENTRY}),
}
NEEDS_INTENTS = frozenset({Action.PROTECT, Action.CLOSE, Action.REDUCE, Action.ADD, Action.ENTER})


@record
class Decision(Record):
    decision_id: str
    account_id: str
    at_ms: int
    action: Action
    reason: ReasonCode
    symbol: str | None = None
    side: Side | None = None
    subject_id: str | None = None            # lot_ / int_ the decision is about
    detail: str = ''
    intents: tuple[OrderIntent, ...] = ()
    policy_version: str = ''

    def _validate(self, p):
        p = f'{p}[{self.decision_id}]'
        check_id(self.decision_id, p + '.decision_id', 'dec')
        check_id(self.account_id, p + '.account_id', 'acct')
        if self.subject_id is not None:
            check_id(self.subject_id, p + '.subject_id', 'lot', 'int')
        req(len(self.detail) <= 160 and self.detail.isprintable(), p + '.detail', 'at most 160 printable characters')
        req(len(self.policy_version) <= 32 and self.policy_version.isprintable(), p + '.policy_version', '<= 32 chars')
        a = self.action
        req(self.reason.stage in ACTION_STAGES[a], p + '.reason', f'{self.reason} is not a reason for {a}')
        allowed = ACTION_PURPOSES.get(a, frozenset())
        req(all(i.purpose in allowed for i in self.intents), p + '.intents', f'{a} sends only {sorted(allowed)} intents')
        req(a not in NEEDS_INTENTS or self.intents, p + '.intents', f'{a} needs its intents')
        req(a is not Action.ENTER or len(self.intents) == 1, p + '.intents', 'one entry intent per ENTER')
        for i in self.intents:
            ip = f'{p}.intent[{i.intent_id}]'
            req(i.account_id == self.account_id, ip + '.account_id', 'intent of another account')
            req(i.decision_id == self.decision_id and i.created_at_ms == self.at_ms, ip + '.decision_id',
                'created by another decision')
            req(i.state in (IntentState.ARMED, IntentState.SENDING), ip + '.state', 'a decision creates new intents only')
        ids = [i.intent_id for i in self.intents]
        req(len(ids) == len(set(ids)), p + '.intents', 'duplicate intent id')

    @property
    def priority(self):
        return PRIORITY[self.action]
