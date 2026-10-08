"""Builders of REAL NC-01 DomainEvents for the step-0 journal tests and the JournalPort contract suite.

Every id is the step-0 derived id (decision / intent / lot / child / client), so a scenario is exactly what a runner
following STEP0_INTERFACE.md would journal. Nothing reads a clock: times step from T0."""
import dataclasses
from decimal import Decimal as D

from newcore.domain import (Action, Authority, Decision, DecisionRecorded, EntriesMode, Evidence, ExchangeStatus,
                            HoldKind, IntentRecorded, IntentState, IntentStateChanged, Lookup, ModeChanged, OrderIntent,
                            OrderResult, OrderType, OwnerKind, Purpose, ReasonCode, ResultObserved, ResultPhase, Side,
                            make_id)
from newcore.ports import keys as K

ACCT = 'acct_' + '0123456789abcdef' * 2
PF = make_id('pf', int(ACCT[5:], 16))
T0 = 1759924800000                       # a 4h candle close (2025-10-08 12:00 UTC)
QTY = D('1.5')
KEY = K.decision_key('trend_ema_mom', 'v1', '4h', 'SOLUSDT', Side.LONG, T0, Purpose.ENTRY)
ENTRY = K.derive_intent_id(ACCT, KEY)
LOT = K.derive_lot_id(ACCT, ENTRY)
REASON = {Purpose.ENTRY: ReasonCode.ENTRY_SIGNAL, Purpose.PROTECT: ReasonCode.PROTECT_PLACE,
          Purpose.CLOSE: ReasonCode.EXIT_SIGNAL, Purpose.REDUCE: ReasonCode.EXIT_TP1,
          Purpose.ADD: ReasonCode.ENTRY_PYRAMID}
ACTION = {Purpose.ENTRY: Action.ENTER, Purpose.PROTECT: Action.PROTECT, Purpose.CLOSE: Action.CLOSE,
          Purpose.REDUCE: Action.REDUCE, Purpose.ADD: Action.ADD}
AUTHORITY = {Purpose.ENTRY: Authority.STRATEGY, Purpose.PROTECT: Authority.PROTECTION,
             Purpose.CLOSE: Authority.STRATEGY,
             Purpose.REDUCE: Authority.STRATEGY, Purpose.ADD: Authority.STRATEGY}


def replace(rec, **kw):
    return dataclasses.replace(rec, **kw)


class Scenario:
    """Appends consecutive events (sequence 1, 2, ...). Each method returns the event it built."""

    def __init__(self, acct=ACCT, pf=PF):
        self.acct, self.pf, self.events, self.n = acct, pf, [], 0
        self.intents = {}

    def _event(self, cls, **kw):
        self.n += 1
        ev = cls(event_id=make_id('evt', self.n), account_id=self.acct, aggregate_id=self.pf, sequence=self.n,
                 at_ms=T0 + 1000 * self.n, **kw)
        self.events.append(ev)
        return ev

    def _intent(self, intent_id, purpose, dec_id, at, *, owner=None, owner_kind=None, route='classic', stop_price=None,
                qty=QTY, symbol='SOLUSDT', side=Side.LONG):
        protect = purpose is Purpose.PROTECT
        if owner is None:
            owner_kind = None
        elif owner_kind is None:
            owner_kind = OwnerKind.LOT          # every step-0 lineage owner here is a lot unless a case says otherwise
        return OrderIntent(intent_id=intent_id, account_id=self.acct, decision_id=dec_id,
                           client_order_id=K.client_id_for(intent_id, route), purpose=purpose,
                           order_type=OrderType.STOP_MARKET if protect else OrderType.MARKET, state=IntentState.PLANNED,
                           symbol=symbol, side=side, qty=qty, reason=REASON[purpose], created_at_ms=at, owner_id=owner,
                           owner_kind=owner_kind,
                           slot_id='S1' if purpose is Purpose.ENTRY else None, price=None,
                           stop_price=(stop_price or D('140')) if protect else None, arm=None,
                           alt_client_order_id=None, seen_qty=None, authorized_by=None, replaces_intent_id=None,
                           stop_distance=D('5') if purpose is Purpose.ENTRY else None)

    def decision(self, *, key=None, purpose=None, intent_ids=(), owner=None, owner_kind=None, route='classic',
                 dec_id=None):
        """A DecisionRecorded authorizing `intent_ids` (keyed: the strategy key; else a lineage decision)."""
        purpose = key.purpose if key is not None else purpose
        at = T0 + 1000 * (self.n + 1)
        if dec_id is None:
            dec_id = K.derive_decision_id(self.acct, key) if key is not None else make_id('dec', 10 ** 6 + self.n)
        its = tuple(self._intent(i, purpose, dec_id, at, owner=owner, owner_kind=owner_kind, route=route)
                    for i in intent_ids)
        for it in its:
            self.intents[it.intent_id] = it
        action, reason, authority = ACTION[purpose], REASON[purpose], AUTHORITY[purpose]
        if not its:                                    # a keyed signal not taken: SKIP with a gate reason
            action, reason, authority = Action.SKIP, ReasonCode.CAPACITY_IN_TRADE, Authority.RISK
        d = Decision(decision_id=dec_id, account_id=self.acct, at_ms=at, action=action, reason=reason,
                     authority=authority, key=key, evidence=(), symbol='SOLUSDT', side=Side.LONG,
                     subject_id=owner, detail='', intents=its, policy_version='step0-test')
        return self._event(DecisionRecorded, reason=d.reason, decision=d)

    def record(self, intent_id):
        it = replace(self.intents[intent_id], state=IntentState.DURABLE)
        return self._event(IntentRecorded, reason=it.reason, intent=it)

    def step(self, intent_id, frm, to):
        return self._event(IntentStateChanged, reason=ReasonCode.LIFECYCLE_DRAIN, intent_id=intent_id,
                           from_state=frm, to_state=to)

    def result(self, intent_id, kind):
        it = self.intents[intent_id]
        common = dict(result_id=make_id('res', self.n + 1), intent_id=intent_id, account_id=self.acct,
                      client_order_id=it.client_order_id, requested_qty=it.qty, observed_at_ms=T0 + 1000 * (self.n + 1),
                      corroboration=(), resolved_by=None, external_trades=())
        none = dict(exchange_order_id=None, exchange_status=None, lookup=None, executed_qty=None, avg_price=None,
                    evidence=None)
        r = {'filled': dict(phase=ResultPhase.FINAL, exchange_order_id='1001', exchange_status=ExchangeStatus.FILLED,
                            lookup=None, executed_qty=it.qty, avg_price=D('150'), evidence=Evidence.EXCHANGE_FINAL),
             'refused': dict(none, phase=ResultPhase.FINAL, executed_qty=D('0'), evidence=Evidence.EXCHANGE_REFUSED),
             'not_sent': dict(none, phase=ResultPhase.FINAL, executed_qty=D('0'), evidence=Evidence.NOT_SENT),
             'unknown': dict(none, phase=ResultPhase.UNKNOWN),
             'not_found': dict(none, phase=ResultPhase.UNKNOWN, lookup=Lookup.NOT_FOUND),
             'known': dict(none, phase=ResultPhase.KNOWN, exchange_order_id='1001',
                           exchange_status=ExchangeStatus.NEW)}[kind]
        res = OrderResult(**common, **r)
        return self._event(ResultObserved, reason=ReasonCode.LIFECYCLE_DRAIN, result=res)

    def hold(self):
        return self._event(ModeChanged, reason=ReasonCode.OPERATOR_PAUSE, from_mode=EntriesMode.ACTIVE,
                           to_mode=EntriesMode.HOLD, reasons=(ReasonCode.OPERATOR_PAUSE,), from_hold=None,
                           to_hold=HoldKind.NORMAL, decision_id=None, reconciliation_id=None)

    # ------------------------------------------------------------------------------------------- composite flows
    def entry_filled(self, key=KEY):
        i = K.derive_intent_id(self.acct, key)
        self.decision(key=key, intent_ids=(i,))
        self.record(i)
        self.step(i, IntentState.DURABLE, IntentState.SUBMITTED)
        self.result(i, 'filled')
        self.step(i, IntentState.SUBMITTED, IntentState.FILLED)
        return i

    def protect_attempt(self, ordinal, route='classic', owner=LOT):
        sid = K.derive_child_intent_id(self.acct, owner, Purpose.PROTECT, ordinal)
        self.decision(purpose=Purpose.PROTECT, intent_ids=(sid,), owner=owner, route=route)
        self.record(sid)
        self.step(sid, IntentState.DURABLE, IntentState.SUBMITTED)
        return sid

    def refused(self, intent_id):
        self.result(intent_id, 'refused')
        return self.step(intent_id, IntentState.SUBMITTED, IntentState.REJECTED)
