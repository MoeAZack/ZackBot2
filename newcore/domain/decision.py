"""Decisions: what the pure core decided, with one machine reason and the intents the shell will make durable and send.

Every Decision carries exactly one ReasonCode whose namespace fits its action (ACTION_NAMESPACES), its AUTHORITY (who
decided: strategy, risk, protection, recovery, reconciliation or operator), references to the inputs / evidence it used
(opaque ids), its integer UTC-ms time, and a display-only `detail` (<= 160 printable characters, never parsed). Its
intents are NEW intents in state PLANNED: they become owned only once durable. Operator authority is never a bypass: a
manual entry is an OPERATOR decision and still meets the same pause / HOLD table.
`priority` orders a scheduler queue: PROTECT before FLATTEN / CLOSE / REDUCE before anything that opens risk, so under
budget pressure an entry can never pre-empt protection.
"""
from __future__ import annotations

import enum

from .base import ID_PREFIXES, Record, check_id, record, req
from .orders import IntentState, OrderIntent, Purpose, Side
from .reasons import GATE_NAMESPACES, NAMESPACES, ReasonCode


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

ACTION_NAMESPACES = {
    Action.PROTECT: frozenset({'protect'}),
    Action.FLATTEN: frozenset({'exit'}),
    Action.CLOSE: frozenset({'exit'}),
    Action.REDUCE: frozenset({'exit'}),
    Action.HALT: frozenset({'filter', 'operator', 'recovery', 'risk_gateway'}),
    Action.PAUSE: frozenset({'operator', 'recovery', 'binding', 'connectivity', 'filter', 'reconcile'}),
    Action.CANCEL_ENTRY: frozenset({'lifecycle', 'trailing', 'execution', 'filter', 'operator', 'capacity', 'risk_gateway'}),
    Action.RECONCILE: frozenset({'reconcile', 'recovery', 'evidence', 'ownership', 'operator', 'binding'}),
    Action.RESUME: frozenset({'operator'}),
    Action.ADD: frozenset({'entry'}),
    Action.ENTER: frozenset({'entry'}),
    Action.WAIT: frozenset(NAMESPACES),
    Action.SKIP: GATE_NAMESPACES,
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


class Authority(enum.StrEnum):
    STRATEGY = 'strategy'
    RISK = 'risk'
    PROTECTION = 'protection'
    RECOVERY = 'recovery'
    RECONCILIATION = 'reconciliation'
    OPERATOR = 'operator'


OPERATOR_REASONS = frozenset({ReasonCode.ENTRY_MANUAL, ReasonCode.ENTRY_ONE_SHOT})


@record
class Decision(Record):
    decision_id: str
    account_id: str
    at_ms: int
    action: Action
    reason: ReasonCode
    authority: Authority
    evidence: tuple[str, ...]           # opaque ids of the inputs / evidence it used (rec_, res_, int_, lot_, ...)
    symbol: str | None
    side: Side | None
    subject_id: str | None            # lot_ / int_ the decision is about
    detail: str
    intents: tuple[OrderIntent, ...]
    policy_version: str

    def _validate(self, p):
        p = f'{p}[{self.decision_id}]'
        check_id(self.decision_id, p + '.decision_id', 'dec')
        check_id(self.account_id, p + '.account_id', 'acct')
        if self.subject_id is not None:
            check_id(self.subject_id, p + '.subject_id', 'lot', 'int')
        req(len(self.detail) <= 160 and self.detail.isprintable(), p + '.detail', 'at most 160 printable characters')
        req(len(self.policy_version) <= 32 and self.policy_version.isprintable(), p + '.policy_version', '<= 32 chars')
        a = self.action
        req(self.reason.namespace in ACTION_NAMESPACES[a], p + '.reason', f'{self.reason} is not a reason for {a}')
        operator = self.reason.namespace == 'operator' or self.reason in OPERATOR_REASONS or a is Action.RESUME
        req(operator == (self.authority is Authority.OPERATOR), p + '.authority',
            'operator reasons (manual / one-shot / operator.*) and resume are exactly the OPERATOR decisions')
        for i, ref in enumerate(self.evidence):
            check_id(ref, f'{p}.evidence[{i}]', *ID_PREFIXES)
        req(len(set(self.evidence)) == len(self.evidence), p + '.evidence', 'duplicate reference')
        allowed = ACTION_PURPOSES.get(a, frozenset())
        req(all(i.purpose in allowed for i in self.intents), p + '.intents', f'{a} sends only {sorted(allowed)} intents')
        req(a not in NEEDS_INTENTS or self.intents, p + '.intents', f'{a} needs its intents')
        req(a is not Action.ENTER or len(self.intents) == 1, p + '.intents', 'one entry intent per ENTER')
        for i in self.intents:
            ip = f'{p}.intent[{i.intent_id}]'
            req(i.account_id == self.account_id, ip + '.account_id', 'intent of another account')
            req(i.decision_id == self.decision_id and i.created_at_ms == self.at_ms, ip + '.decision_id',
                'created by another decision')
            req(i.state is IntentState.PLANNED, ip + '.state', 'a decision creates PLANNED intents only')
        ids = [i.intent_id for i in self.intents]
        req(len(ids) == len(set(ids)), p + '.intents', 'duplicate intent id')

    @property
    def priority(self):
        return PRIORITY[self.action]
