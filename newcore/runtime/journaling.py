"""RT-02 journaling action sink: the one route from a decided action to NC-02-journaled venue work.

Write-ahead order for every order, on the account's own NC-05 runner thread:

    DecisionRecorded -> IntentRecorded (DURABLE) -> IntentStateChanged (SUBMITTED) -> Scheduler.submit

All three events are durable (fsync) before the work is queued, and the runner cannot dispatch the work before the hook
that queued it returns.  Before anything is journaled, every venue payload the action needs is checked against the
NC-04 admission table (``reservation_for``): an action NC-04 would refuse is never journaled and never queued.

Results are folded back on later guard passes (``poll``):

    OrderOutcome FINAL     -> ResultObserved FINAL exchange_final -> IntentStateChanged terminal_for(result)
    OrderOutcome REJECTED  -> ResultObserved FINAL exchange_refused (0 executed) -> REJECTED
    OrderOutcome KNOWN     -> ResultObserved KNOWN -> IntentStateChanged WORKING
    anything else          -> ResultObserved UNKNOWN (NOT_FOUND keeps lookup not_found) -> UNKNOWN, and the sticky
                              ``order_unresolved`` problem: resolving it needs the reconcile query path (deferred)

Protection, after a fill:

* An entry FINAL with an execution opens the lot ``derive_lot_id(account, entry)``.  The sink journals and queues the
  lot's protective stop at once on the algo route (Binance's conditional-order route; journal rule G6), for the
  executed quantity at the NC-06 request's stop price.
* A stop that ends without protecting the whole lot (rejected, cancelled, expired, partly executed), or a protect that
  NC-04 cannot admit, flattens the lot at market (``exit.stop_failed``) and raises the sticky ``protection_failed``.
* A market order (entry or close) answered KNOWN is journaled WORKING and raises ``order_unresolved``: its executed
  quantity is not known, so nothing is built on it until the reconcile query path resolves it.

Entries are accepted only with a strategy ``DecisionKey`` (journal rule G4), only when nothing else is in flight and
no problem is active, and only when NC-04 admits the entry, the algo protect and the fail-safe close.
"""
from __future__ import annotations

import dataclasses
import hashlib
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from newcore.account_context import FillsRequest, Operation, VenueWork, WorkResult, WorkState, reservation_for
from newcore.domain import (Action, Authority, Decision, DecisionKey, DecisionRecorded, Evidence, ExchangeStatus,
                            IntentRecorded, IntentState, IntentStateChanged, Lookup, OrderIntent, OrderResult,
                            OrderType, OwnerKind, Purpose, ReasonCode, ResultObserved, ResultPhase, Side, make_id)
from newcore.domain.orders import FINAL_STATUSES, OPEN_STATUSES, TERMINAL, terminal_for
from newcore.management import CloseAction, FlattenRequired, ManagementPlan, ProtectionRepair
from newcore.ports import keys as K
from newcore.ports.journal import claim_signal
from newcore.ports.venue import (MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, ReadOutcome, StopOrder,
                                 VenueFill)

POLICY_VERSION = 'rt02-v1'
# A WORKING order rests on the exchange and REC-02 checks it; these states have no journaled answer yet.
IN_FLIGHT = frozenset({IntentState.DURABLE, IntentState.SUBMITTED, IntentState.UNKNOWN, IntentState.CANCELLING})
WORK_PREFIX = 'ord-'
MAX_PENDING = 16


TRIGGER_READS = 5     # fill reads of a triggered stop's child before it counts as unresolved


class SinkProblem:
    """Sticky problem codes ``poll`` reports; the runtime turns each into a hold of the same name."""
    ORDER_UNRESOLVED = 'order_unresolved'
    ORDER_JOURNAL_FAILED = 'order_journal_failed'
    PROTECTION_FAILED = 'protection_failed'
    SINK_UNBOUND = 'sink_unbound'
    RECOVERED_LIVE_INTENTS = 'recovered_live_intents'


# While any of these is active the sink's own view of the account is uncertain: it routes nothing new.
UNCERTAIN = frozenset({SinkProblem.ORDER_UNRESOLVED, SinkProblem.ORDER_JOURNAL_FAILED,
                       SinkProblem.RECOVERED_LIVE_INTENTS})
PROBLEMS = frozenset({SinkProblem.ORDER_UNRESOLVED, SinkProblem.ORDER_JOURNAL_FAILED, SinkProblem.PROTECTION_FAILED,
                      SinkProblem.SINK_UNBOUND, SinkProblem.RECOVERED_LIVE_INTENTS})


@dataclass(frozen=True, slots=True, kw_only=True)
class SinkBinding:
    """What the runtime hands a journaling sink after boot.  Nothing here is a venue, credential or budget."""
    account_id: str
    aggregate_id: str
    journal: object                                   # the account's recovered NC-02 FileJournal (writer)
    submit: Callable[[VenueWork], None]               # NC-05 Scheduler.submit for this account
    result: Callable[[str], WorkResult | None]        # NC-04 AccountContext.result
    cancel: Callable[[str, int], bool]                # NC-04 AccountContext.cancel
    wall_clock: Callable[[], int]                     # integer UTC ms
    order_deadline_ms: int


@dataclass(slots=True)
class _Lot:
    lot_id: str
    symbol: str
    side: Side
    quantity: Decimal
    stop_price: Decimal | None
    last_protect: tuple[str, bool, IntentState | None] | None = None   # (route, sent, closed state) of the last attempt


@dataclass(slots=True)
class _Order:
    intent: OrderIntent
    route: str
    work_id: str
    deadline_mono: int
    state: IntentState = IntentState.SUBMITTED
    stop_price: Decimal | None = None                 # an entry's NC-06 stop, used for its lot's first protect


@dataclass(frozen=True, slots=True)
class SinkSnapshot:
    bound: bool
    pending: tuple[str, ...]
    lots: tuple[tuple[str, str], ...]                 # (lot_id, quantity)
    problems: tuple[str, ...]
    log: tuple[str, ...]


def _admissible(action: Action, operation: Operation, payload) -> bool:
    try:
        reservation_for(action, operation, payload)
    except Exception:
        return False
    return True


def _h32(*parts: str) -> int:
    digest = hashlib.sha256(b'zackbot.newcore.rt02.v1' + b''.join(b'\x00' + p.encode('ascii') for p in parts))
    return int.from_bytes(digest.digest()[:16], 'big')


def _ascii_id(text: str) -> str:
    return text if text.isascii() else ''


class JournalingActionSink:
    """An ``ActionSink`` that journals every order through NC-02 before it can reach NC-04.  One instance per account."""

    def __init__(self, *, policy_version: str = POLICY_VERSION, monotonic_clock: Callable[[], int] | None = None):
        self._policy = policy_version
        self._mono = monotonic_clock
        self._lock = threading.Lock()
        self._b: SinkBinding | None = None
        self._pending: dict[str, _Order] = {}
        self._lots: dict[str, _Lot] = {}
        self._problems: set[str] = set()
        self._log: list[str] = []
        self._send_failed = False          # the last _send queued nothing (journal or submit refusal)
        self._watched: dict[str, _Order] = {}       # intent_id -> a WORKING stop, checked with a venue query
        self._queries: dict[str, tuple[str, int]] = {}   # intent_id -> (query or fills work id, deadline)
        self._triggered: dict[str, list] = {}       # intent_id -> [child exchange order id, fill reads without a full fill]
        self._next_query_mono: int | None = None
        self._query_seq = 0

    # ------------------------------------------------------------------------------------------------ wiring
    def bind(self, binding: SinkBinding, monotonic_clock: Callable[[], int]) -> None:
        if type(binding) is not SinkBinding:
            raise ValueError('sink_binding_invalid')
        with self._lock:
            if self._b is not None:
                raise ValueError('sink_already_bound')
            self._b, self._mono = binding, monotonic_clock
            self._rebuild()

    def _rebuild(self) -> None:
        """Replay the journal: lots opened by executed entries, and the last protect attempt of each lot."""
        intents: dict[str, list] = {}       # intent_id -> [OrderIntent, state, sent, final result]
        for ev in self._b.journal.read():
            if isinstance(ev, IntentRecorded):
                intents[ev.intent.intent_id] = [ev.intent, ev.intent.state, False, None]
            elif isinstance(ev, IntentStateChanged) and ev.intent_id in intents:
                row = intents[ev.intent_id]
                row[1] = ev.to_state
                row[2] = row[2] or ev.to_state is IntentState.SUBMITTED
            elif isinstance(ev, ResultObserved) and ev.result.intent_id in intents and \
                    ev.result.phase is ResultPhase.FINAL:
                intents[ev.result.intent_id][3] = ev.result
        account = self._b.account_id
        for intent, state, sent, final in intents.values():
            if state in IN_FLIGHT:
                # Sent (or about to be) with no answer journaled: only the reconcile query path can resolve it.
                self._problems.add(SinkProblem.RECOVERED_LIVE_INTENTS)
            if intent.purpose is Purpose.ENTRY and final is not None and final.evidence is Evidence.EXCHANGE_FINAL \
                    and final.executed_qty and final.executed_qty > 0:
                lot = K.derive_lot_id(account, intent.intent_id)
                self._lots[lot] = _Lot(lot, intent.symbol, intent.side, final.executed_qty, None)
        for intent, state, sent, final in intents.values():
            lot = self._lots.get(intent.owner_id) if intent.owner_kind is OwnerKind.LOT else None
            if lot is None:
                continue
            if intent.purpose is Purpose.PROTECT:
                route = K.route_of(intent.intent_id, intent.client_order_id)
                lot.last_protect = (route, sent, state if state in TERMINAL else None)
                if state is IntentState.WORKING:
                    self._watched[intent.intent_id] = _Order(intent=intent, route=route, work_id='',
                                                             deadline_mono=0, state=IntentState.WORKING)
            if intent.purpose in (Purpose.PROTECT, Purpose.CLOSE, Purpose.REDUCE) and final is not None \
                    and final.executed_qty:
                lot.quantity = max(Decimal('0'), lot.quantity - final.executed_qty)
        for lot in self._lots.values():
            if lot.quantity > 0 and (lot.last_protect is None or lot.last_protect[2] is not None):
                # An open lot with no live stop (for example a crash between the fill and its protect).
                self._problems.add(SinkProblem.PROTECTION_FAILED)
                self._note('recovered_unprotected_lot')
        if SinkProblem.RECOVERED_LIVE_INTENTS in self._problems:
            self._note('recovered_live_intents')

    # ------------------------------------------------------------------------------------------------ helpers
    def _note(self, text: str) -> None:
        self._log.append(text)
        del self._log[:-64]

    def _now_mono(self) -> int:
        return self._mono()

    def _append(self, event) -> None:
        self._b.journal.append(event)        # raises JournalConflict / DurabilityUnavailable: nothing is sent

    def _event(self, cls, *, at_ms: int, reason: ReasonCode, **kw):
        seq = self._b.journal.last_sequence() + 1
        event_id = make_id('evt', _h32('evt', self._b.account_id, self._b.aggregate_id, str(seq)))
        return cls(event_id=event_id, account_id=self._b.account_id, aggregate_id=self._b.aggregate_id,
                   sequence=seq, at_ms=at_ms, reason=reason, **kw)

    def _intent(self, *, intent_id, decision_id, purpose, route, symbol, side, qty, reason, at_ms, owner_id=None,
                owner_kind=None, stop_price=None, slot_id=None) -> OrderIntent:
        return OrderIntent(intent_id=intent_id, account_id=self._b.account_id, decision_id=decision_id,
                           client_order_id=K.client_id_for(intent_id, route), purpose=purpose,
                           order_type=OrderType.STOP_MARKET if purpose is Purpose.PROTECT else OrderType.MARKET,
                           state=IntentState.PLANNED, symbol=symbol, side=side, qty=qty, reason=reason,
                           created_at_ms=at_ms, owner_id=owner_id, owner_kind=owner_kind, slot_id=slot_id, price=None,
                           stop_price=stop_price, arm=None, alt_client_order_id=None, seen_qty=None,
                           authorized_by=None, replaces_intent_id=None)

    @staticmethod
    def _payload(intent: OrderIntent, route: str):
        ref = OrderRef(symbol=intent.symbol, client_id=intent.client_order_id, route=route)
        if intent.purpose is Purpose.PROTECT:
            return Operation.SUBMIT_STOP, StopOrder(ref=ref, position_side=intent.side.value, qty=intent.qty,
                                                    stop_price=intent.stop_price)
        return Operation.SUBMIT_MARKET, MarketOrder(ref=ref, position_side=intent.side.value, qty=intent.qty,
                                                    reduce=intent.purpose is not Purpose.ENTRY)

    def _probe(self, action: Action, purpose: Purpose, route: str, symbol: str, side: Side, qty: Decimal,
               stop_price: Decimal | None = None) -> bool:
        """NC-04 admission of the payload this action would queue, with a placeholder client id of the right route."""
        try:
            probe = 'int_' + '0' * 32
            ref = OrderRef(symbol=symbol, client_id=K.client_id_for(probe, route), route=route)
            if purpose is Purpose.PROTECT:
                payload, op = StopOrder(ref=ref, position_side=side.value, qty=qty, stop_price=stop_price), \
                    Operation.SUBMIT_STOP
            else:
                payload, op = MarketOrder(ref=ref, position_side=side.value, qty=qty,
                                          reduce=purpose is not Purpose.ENTRY), Operation.SUBMIT_MARKET
        except Exception:
            return False
        return _admissible(action, op, payload)

    def _send(self, decision: Decision, intent: OrderIntent, action: Action, route: str, *,
              stop_price: Decimal | None = None) -> bool:
        """Journal decision, intent and SENT (each durable), then queue the work.  False if nothing was queued."""
        at = decision.at_ms
        self._send_failed = True
        try:
            self._append(self._event(DecisionRecorded, at_ms=at, reason=decision.reason, decision=decision))
            durable = dataclasses.replace(intent, state=IntentState.DURABLE)
            self._append(self._event(IntentRecorded, at_ms=at, reason=intent.reason, intent=durable))
            self._append(self._event(IntentStateChanged, at_ms=at, reason=intent.reason, intent_id=intent.intent_id,
                                     from_state=IntentState.DURABLE, to_state=IntentState.SUBMITTED))
        except Exception:
            self._problems.add(SinkProblem.ORDER_JOURNAL_FAILED)
            self._note(f'journal_failed:{intent.purpose.value}')
            return False
        operation, payload = self._payload(intent, route)
        now = self._now_mono()
        work_id = WORK_PREFIX + intent.intent_id
        work = VenueWork(work_id=work_id, action=action, operation=operation, payload=payload, enqueued_at_ms=now,
                         deadline_ms=now + self._b.order_deadline_ms)
        self._pending[work_id] = _Order(intent=durable, route=route, work_id=work_id,
                                        deadline_mono=now + 2 * self._b.order_deadline_ms, stop_price=stop_price)
        try:
            self._b.submit(work)
        except Exception:
            # SENT is durable but nothing was queued: only a venue query can prove that now.
            self._pending.pop(work_id, None)
            self._problems.add(SinkProblem.ORDER_UNRESOLVED)
            self._note(f'submit_failed:{intent.purpose.value}')
            return False
        self._send_failed = False
        self._note(f'sent:{intent.purpose.value}:{route}')
        return True

    def _child_decision(self, *, owner_id: str, purpose: Purpose, route: str, action: Action, reason: ReasonCode,
                        authority: Authority, lot: _Lot, qty: Decimal, stop_price: Decimal | None,
                        at_ms: int) -> tuple[Decision, OrderIntent]:
        grammar = self._b.journal.gate().grammar
        intent_id = grammar.next_child_intent_id(owner_id, purpose)
        # The journal position makes the id unique even if an earlier decision for this ordinal was journaled without
        # its intent (a failed append): G4 never sees the same decision id twice.
        decision_id = make_id('dec', _h32('decision', self._b.account_id, intent_id,
                                          str(self._b.journal.last_sequence())))
        intent = self._intent(intent_id=intent_id, decision_id=decision_id, purpose=purpose, route=route,
                              symbol=lot.symbol, side=lot.side, qty=qty, reason=reason, at_ms=at_ms,
                              owner_id=owner_id, owner_kind=OwnerKind.LOT, stop_price=stop_price)
        decision = Decision(decision_id=decision_id, account_id=self._b.account_id, at_ms=at_ms, action=action,
                            reason=reason, authority=authority, key=None, evidence=(owner_id,), symbol=lot.symbol,
                            side=lot.side, subject_id=owner_id, detail='', intents=(intent,),
                            policy_version=self._policy)
        return decision, intent

    def _protect(self, lot: _Lot, route: str, at_ms: int, reason: ReasonCode = ReasonCode.PROTECT_PLACE) -> bool:
        if lot.stop_price is None or not self._probe(Action.PROTECT, Purpose.PROTECT, route, lot.symbol, lot.side,
                                                     lot.quantity, lot.stop_price):
            return False
        decision, intent = self._child_decision(owner_id=lot.lot_id, purpose=Purpose.PROTECT, route=route,
                                                action=Action.PROTECT, reason=reason, authority=Authority.PROTECTION,
                                                lot=lot, qty=lot.quantity, stop_price=lot.stop_price, at_ms=at_ms)
        if not self._send(decision, intent, Action.PROTECT, route):
            return False
        lot.last_protect = (route, True, None)
        return True

    def _close(self, lot: _Lot, qty: Decimal, action: Action, reason: ReasonCode, authority: Authority,
               at_ms: int) -> bool:
        if not self._probe(action, Purpose.CLOSE, 'classic', lot.symbol, lot.side, qty):
            return False
        decision, intent = self._child_decision(owner_id=lot.lot_id, purpose=Purpose.CLOSE, route='classic',
                                                action=action, reason=reason, authority=authority, lot=lot, qty=qty,
                                                stop_price=None, at_ms=at_ms)
        return self._send(decision, intent, action, 'classic')

    def _fail_protection(self, lot: _Lot, at_ms: int) -> None:
        """Protection could not be placed: flatten the lot at market and hold the account."""
        self._problems.add(SinkProblem.PROTECTION_FAILED)
        self._note('protection_failed')
        if lot.quantity > 0 and not self._send_failed and not self._lot_busy(lot.lot_id):
            self._close(lot, lot.quantity, Action.FLATTEN, ReasonCode.EXIT_STOP_FAILED, Authority.PROTECTION, at_ms)

    def _lot_busy(self, lot_id: str) -> bool:
        return any(o.intent.owner_id == lot_id for o in self._pending.values())

    # ------------------------------------------------------------------------------------------------ ActionSink
    def accept_entry(self, account_id, decision, evaluation, key: DecisionKey | None = None) -> bool:
        from newcore import risk as nc06
        with self._lock:
            b = self._b
            if b is None or account_id != b.account_id or self._problems:
                return False
            if type(decision) is not nc06.ApprovedDecision or type(evaluation) is not nc06.RiskEvaluation:
                return False
            request = evaluation.request
            if type(key) is not DecisionKey or key.purpose is not Purpose.ENTRY:
                self._note('entry_key_missing')
                return False
            side = Side(request.side.value)
            if (key.symbol, key.side) != (request.symbol, side) or decision.account_id != account_id \
                    or request.account_id != account_id:
                self._note('entry_key_mismatch')
                return False
            if self._pending or len(self._pending) >= MAX_PENDING:
                self._note('entry_busy')
                return False
            claim = claim_signal(b.journal.gate().grammar, key)
            if not claim.fresh:
                self._note('signal_consumed')
                return False
            qty, stop = decision.quantity, request.stop_price
            routes_ok = (self._probe(Action.ENTER, Purpose.ENTRY, 'classic', key.symbol, side, qty)
                         and self._probe(Action.FLATTEN, Purpose.CLOSE, 'classic', key.symbol, side, qty))
            if not routes_ok:
                self._note('entry_route_unverified')
                return False
            if not self._probe(Action.PROTECT, Purpose.PROTECT, 'algo', key.symbol, side, qty, stop):
                # An entry that could not be protected is never opened.
                self._note('protect_route_unverified')
                return False
            at = b.wall_clock()
            intent = self._intent(intent_id=claim.intent_ids[0], decision_id=claim.decision_id,
                                  purpose=Purpose.ENTRY, route='classic', symbol=key.symbol, side=side, qty=qty,
                                  reason=ReasonCode.ENTRY_SIGNAL, at_ms=at, slot_id=key.strategy)
            record = Decision(decision_id=claim.decision_id, account_id=account_id, at_ms=at, action=Action.ENTER,
                              reason=ReasonCode.ENTRY_SIGNAL, authority=Authority.STRATEGY, key=key, evidence=(),
                              symbol=key.symbol, side=side, subject_id=None, detail=_ascii_id(request.request_id)[:160],
                              intents=(intent,), policy_version=self._policy)
            return self._send(record, intent, Action.ENTER, 'classic', stop_price=stop)

    def accept_management(self, account_id, plan) -> bool:
        with self._lock:
            b = self._b
            if b is None or account_id != b.account_id or type(plan) is not ManagementPlan or self._problems & UNCERTAIN:
                return False
            lot = self._lots.get(plan.lot_id)
            if lot is None or lot.quantity <= 0 or not plan.actions:
                return False
            if self._lot_busy(lot.lot_id):
                return True                       # already routed: the pending order is the action in progress
            steps = []
            for action in plan.actions:
                if type(action) is ProtectionRepair:
                    if lot.last_protect is not None and lot.last_protect[2] is None:
                        return False              # a live stop exists: replacing it needs the cancel path (deferred)
                    route = self._next_protect_route(lot)
                    if route is None or action.quantity != lot.quantity:
                        return False
                    if not self._probe(Action.PROTECT, Purpose.PROTECT, route, lot.symbol, lot.side, action.quantity,
                                       action.stop_price):
                        return False
                    steps.append(('protect', route, action))
                elif type(action) in (CloseAction, FlattenRequired):
                    flat = type(action) is FlattenRequired
                    if lot.last_protect is not None and lot.last_protect[2] is None:
                        return False              # a live stop would outlive the close: needs the cancel path first
                    if not 0 < action.quantity <= lot.quantity:
                        return False
                    if not self._probe(Action.FLATTEN if flat else Action.CLOSE, Purpose.CLOSE, 'classic',
                                       lot.symbol, lot.side, action.quantity):
                        return False
                    steps.append(('close', flat, action))
                else:
                    return False                  # reduce / stop move / cleanup / risk increase: not routed here
            if len(steps) != 1:
                return False                      # one order per plan in this slice
            at = b.wall_clock()
            kind, arg, action = steps[0]
            if kind == 'protect':
                lot.stop_price = action.stop_price
                return self._protect(lot, arg, at, ReasonCode.PROTECT_RESTORING)
            reason = ReasonCode.EXIT_FLATTEN if arg else ReasonCode.EXIT_SIGNAL
            authority = Authority.PROTECTION if arg else Authority.STRATEGY
            return self._close(lot, action.quantity, Action.FLATTEN if arg else Action.CLOSE, reason, authority, at)

    @staticmethod
    def _next_protect_route(lot: _Lot) -> str | None:
        last = lot.last_protect
        if last is None or last[2] in (IntentState.CANCELLED, IntentState.FILLED, IntentState.NOT_SENT):
            return 'algo'
        if last[0] == 'classic' and last[1] and last[2] is IntentState.REJECTED:
            return 'algo'                         # the G6 fallback of a journal written before algo-first
        return None                               # live, or an algo attempt was rejected: no third route (G6)

    # ------------------------------------------------------------------------------------------------ results
    def poll(self, now_mono: int) -> tuple[str, ...]:
        """Fold every finished order back into the journal.  Returns the sticky problems (empty when healthy)."""
        with self._lock:
            if self._b is None:
                return (SinkProblem.SINK_UNBOUND,)
            for work_id in tuple(self._pending):
                order = self._pending[work_id]
                result = self._b.result(work_id)
                if result is None:
                    if now_mono >= order.deadline_mono:
                        self._b.cancel(work_id, now_mono)
                        self._unresolved(order, Lookup.UNREADABLE)
                    continue
                self._fold(order, result)
            self._watch(now_mono)
            return tuple(sorted(self._problems))

    # ------------------------------------------------------------------------------------------------ resting stops
    def _watch(self, now_mono: int) -> None:
        """Query every WORKING stop once per order deadline; a stop that ended is journaled and its lot follows."""
        for intent_id, (work_id, deadline) in tuple(self._queries.items()):
            order = self._watched.get(intent_id)
            result = self._b.result(work_id)
            if result is None:
                if now_mono >= deadline:
                    self._b.cancel(work_id, now_mono)
                    del self._queries[intent_id]          # no answer: ask again next round
                continue
            del self._queries[intent_id]
            if order is not None:
                if intent_id in self._triggered:
                    self._fold_fills(order, result)
                else:
                    self._fold_query(order, result)
        if self._next_query_mono is not None and now_mono < self._next_query_mono:
            return
        self._next_query_mono = now_mono + self._b.order_deadline_ms
        if self._problems & UNCERTAIN:
            return
        for intent_id, order in self._watched.items():
            if intent_id in self._queries:
                continue
            self._query_seq += 1
            it = order.intent
            if intent_id in self._triggered:              # a triggered algo stop: read its child order's fills
                work = VenueWork(work_id=f'fil{self._query_seq}-{intent_id}'[:64], action=Action.RECONCILE,
                                 operation=Operation.FILLS,
                                 payload=FillsRequest(symbol=it.symbol, exchange_order_id=self._triggered[intent_id][0]),
                                 enqueued_at_ms=now_mono, deadline_ms=now_mono + self._b.order_deadline_ms)
            else:
                work = VenueWork(work_id=f'qry{self._query_seq}-{intent_id}'[:64], action=Action.PROTECT,
                                 operation=Operation.QUERY,
                                 payload=OrderRef(symbol=it.symbol, client_id=it.client_order_id, route=order.route),
                                 enqueued_at_ms=now_mono, deadline_ms=now_mono + self._b.order_deadline_ms)
            try:
                self._b.submit(work)
            except Exception:
                self._note('watch_refused')               # stopping, or NC-04 refused: REC-02 still checks the book
                return
            self._queries[intent_id] = (work.work_id, now_mono + 2 * self._b.order_deadline_ms)

    def _fold_query(self, order: _Order, work: WorkResult) -> None:
        outcome = work.value if work.state is WorkState.COMPLETED else None
        if type(outcome) is not OrderOutcome or outcome.ref.client_id != order.intent.client_order_id:
            return                                        # unreadable this round: REC-02 still checks the book
        if outcome.kind is OutcomeKind.KNOWN:
            if outcome.detail == 'algo_triggered' and outcome.exchange_order_id:
                # The algo service names only the child market order; its fills say what the stop executed.
                if order.intent.intent_id not in self._triggered:
                    self._triggered[order.intent.intent_id] = [outcome.exchange_order_id, 0]
                    self._note('triggered:stop')
            return                                        # still resting (or triggered: fills next round)
        if outcome.kind is OutcomeKind.NOT_FOUND:
            self._watched.pop(order.intent.intent_id, None)
            self._unresolved(order, Lookup.NOT_FOUND)     # a vanished stop: only reconcile can explain it
            return
        if outcome.kind is not OutcomeKind.FINAL:
            return
        try:
            status = ExchangeStatus(outcome.status)
        except ValueError:
            return
        if status not in FINAL_STATUSES:
            return
        self._journal_stop_final(order, outcome.exchange_order_id, status, outcome.executed_qty, outcome.avg_price,
                                 outcome.observed_at_ms)

    def _fold_fills(self, order: _Order, work: WorkResult) -> None:
        """A triggered algo stop: FILLED once its child's fills cover the stop; else unresolved after a few reads."""
        it = order.intent
        child = self._triggered[it.intent_id]
        read = work.value if work.state is WorkState.COMPLETED else None
        if type(read) is not ReadOutcome or read.kind is not ReadKind.OK:
            return                                        # unreadable this round: ask again
        rows = [f for f in read.value if type(f) is VenueFill and f.exchange_order_id == child[0]]
        if any((f.symbol, f.position_side) != (it.symbol, it.side.value) for f in rows):
            self._triggered.pop(it.intent_id, None)
            self._watched.pop(it.intent_id, None)
            self._unresolved(order, Lookup.NOT_FOUND)      # fills that are not this stop's: only reconcile explains
            return
        total = sum((f.qty for f in rows), Decimal('0'))
        if total == it.qty:
            notional = sum((f.qty * f.price for f in rows), Decimal('0'))
            avg = min(max(notional / total, min(f.price for f in rows)), max(f.price for f in rows))
            self._triggered.pop(it.intent_id, None)
            self._journal_stop_final(order, child[0], ExchangeStatus.FILLED, total, avg,
                                     max(f.at_ms for f in rows))
            return
        child[1] += 1
        if total > it.qty or child[1] >= TRIGGER_READS:
            self._triggered.pop(it.intent_id, None)
            self._watched.pop(it.intent_id, None)
            self._unresolved(order, Lookup.NOT_FOUND)      # over-filled, or never fully filled: hold for reconcile

    def _journal_stop_final(self, order: _Order, exchange_order_id, status, executed, avg, observed_at_ms) -> None:
        self._watched.pop(order.intent.intent_id, None)
        # Stamped with the venue's observation time (never before the intent), so it does not run ahead of the
        # evidence REC-02 is about to fold.
        at = max(min(observed_at_ms, self._b.wall_clock()), order.intent.created_at_ms)
        try:
            result = self._record(order, at, phase=ResultPhase.FINAL, exchange_order_id=exchange_order_id,
                                  exchange_status=status, executed_qty=executed, avg_price=avg,
                                  evidence=Evidence.EXCHANGE_FINAL)
            self._step(order, terminal_for(result), at)
        except Exception:
            self._problems.add(SinkProblem.ORDER_JOURNAL_FAILED)
            self._note('journal_failed:stop_result')
            return
        self._note(f'{order.state.value}:stop')
        self._after_final(order, result, at)

    def _unresolved(self, order: _Order, lookup: Lookup | None = None) -> None:
        self._pending.pop(order.work_id, None)
        self._problems.add(SinkProblem.ORDER_UNRESOLVED)
        self._note(f'unresolved:{order.intent.purpose.value}')
        try:
            at = max(self._b.wall_clock(), order.intent.created_at_ms)
            self._record(order, at, phase=ResultPhase.UNKNOWN, lookup=lookup)
            if order.state is not IntentState.UNKNOWN:
                self._step(order, IntentState.UNKNOWN, at)
        except Exception:
            self._problems.add(SinkProblem.ORDER_JOURNAL_FAILED)

    def _record(self, order: _Order, at: int, **kw) -> OrderResult:
        it = order.intent
        fields = dict(exchange_order_id=None, exchange_status=None, lookup=None, executed_qty=None, avg_price=None,
                      evidence=None)
        fields.update(kw)
        seq = self._b.journal.last_sequence() + 1
        result = OrderResult(result_id=make_id('res', _h32('res', self._b.account_id, self._b.aggregate_id, str(seq))),
                             intent_id=it.intent_id, account_id=it.account_id, client_order_id=it.client_order_id,
                             requested_qty=it.qty, observed_at_ms=at, corroboration=(), resolved_by=None,
                             external_trades=(), supersedes_result_id=None, **fields)
        self._append(self._event(ResultObserved, at_ms=at, reason=it.reason, result=result))
        return result

    def _step(self, order: _Order, to: IntentState, at: int) -> None:
        self._append(self._event(IntentStateChanged, at_ms=at, reason=order.intent.reason,
                                 intent_id=order.intent.intent_id, from_state=order.state, to_state=to))
        order.state = to

    def _fold(self, order: _Order, work: WorkResult) -> None:
        outcome = work.value if work.state is WorkState.COMPLETED else None
        if type(outcome) is not OrderOutcome or outcome.ref.client_id != order.intent.client_order_id:
            self._unresolved(order)
            return
        kind = outcome.kind
        if kind is OutcomeKind.NOT_FOUND:
            self._unresolved(order, Lookup.NOT_FOUND)
            return
        try:
            status = None if outcome.status is None else ExchangeStatus(outcome.status)
        except ValueError:
            self._unresolved(order)
            return
        at = max(self._b.wall_clock(), order.intent.created_at_ms)   # a wall clock that stepped back never loses a fill
        try:
            if kind is OutcomeKind.KNOWN and status in OPEN_STATUSES:
                self._record(order, at, phase=ResultPhase.KNOWN, exchange_order_id=outcome.exchange_order_id,
                             exchange_status=status)
                self._step(order, IntentState.WORKING, at)
                self._pending.pop(order.work_id, None)
                self._note(f'working:{order.intent.purpose.value}')
                if order.intent.purpose is Purpose.PROTECT:
                    self._after_working(order)
                else:
                    # A market order resting KNOWN: its executed quantity is unknown, so nothing builds on it.
                    self._problems.add(SinkProblem.ORDER_UNRESOLVED)
                    if order.intent.purpose is Purpose.ENTRY:
                        self._problems.add(SinkProblem.PROTECTION_FAILED)
                return
            if kind is OutcomeKind.FINAL and status in FINAL_STATUSES:
                result = self._record(order, at, phase=ResultPhase.FINAL, exchange_order_id=outcome.exchange_order_id,
                                      exchange_status=status, executed_qty=outcome.executed_qty,
                                      avg_price=outcome.avg_price, evidence=Evidence.EXCHANGE_FINAL)
            elif kind is OutcomeKind.REJECTED:
                result = self._record(order, at, phase=ResultPhase.FINAL, executed_qty=Decimal('0'),
                                      evidence=Evidence.EXCHANGE_REFUSED)
            else:
                self._unresolved(order)
                return
            self._step(order, terminal_for(result), at)
        except Exception:
            self._pending.pop(order.work_id, None)
            self._problems.add(SinkProblem.ORDER_JOURNAL_FAILED)
            if order.intent.purpose is Purpose.ENTRY:
                self._problems.add(SinkProblem.PROTECTION_FAILED)    # an entry answer we could not journal
            self._note('journal_failed:result')
            return
        self._pending.pop(order.work_id, None)
        self._note(f'{order.state.value}:{order.intent.purpose.value}')
        self._after_final(order, result, at)

    def _after_working(self, order: _Order) -> None:
        if order.intent.purpose is Purpose.PROTECT:
            self._watched[order.intent.intent_id] = order
            lot = self._lots.get(order.intent.owner_id)
            if lot is not None:
                lot.last_protect = (order.route, True, None)

    def _after_final(self, order: _Order, result: OrderResult, at: int) -> None:
        it = order.intent
        executed = result.executed_qty or Decimal('0')
        if it.purpose is Purpose.ENTRY:
            if executed > 0:
                lot_id = K.derive_lot_id(self._b.account_id, it.intent_id)
                lot = _Lot(lot_id, it.symbol, it.side, executed, order.stop_price)
                self._lots[lot_id] = lot
                if not self._protect(lot, 'algo', at):
                    self._fail_protection(lot, at)
            return
        lot = self._lots.get(it.owner_id)
        if lot is None:
            return
        if it.purpose is Purpose.PROTECT:
            lot.last_protect = (order.route, True, order.state)
            lot.quantity = max(Decimal('0'), lot.quantity - executed)           # the stop itself closed (part of) it
            if lot.quantity > 0:
                # The stop is gone and the lot is still open.  Only a rejected classic attempt has a G6 fallback;
                # anything else (rejected algo, cancelled, expired, partly executed) flattens the rest.
                route = self._next_protect_route(lot) if order.state is IntentState.REJECTED else None
                if route is None or not self._protect(lot, route, at):
                    self._fail_protection(lot, at)
            return
        if executed > 0:
            lot.quantity = max(Decimal('0'), lot.quantity - executed)

    # ------------------------------------------------------------------------------------------------ view
    def snapshot(self) -> SinkSnapshot:
        with self._lock:
            return SinkSnapshot(bound=self._b is not None, pending=tuple(sorted(self._pending)),
                                lots=tuple(sorted((lot_id, str(lot.quantity)) for lot_id, lot in self._lots.items())),
                                problems=tuple(sorted(self._problems)), log=tuple(self._log))


__all__ = ['JournalingActionSink', 'POLICY_VERSION', 'PROBLEMS', 'SinkBinding', 'SinkProblem', 'SinkSnapshot']
