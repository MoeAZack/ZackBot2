"""Order intents and order results: the ONE order-ownership family (contract 2, invariants 5-7; rulings 1, 3, 7).

OrderIntent covers every purpose (ENTRY, ADD, REDUCE, CLOSE, PROTECT). There is no EntryIntent and no side queue: an
unconfirmed market entry, a resting maker, an armed trailing entry and an orphan cancel are all OrderIntents in some
lifecycle state. An orphan cancel (an owned order whose lot / entry is gone) is re-owned by the portfolio aggregate
(owner_kind PORTFOLIO, owner_id = the portfolio id) and may only be CANCELLING: no reference ever dangles. What owns an
intent is the explicit `owner_kind`; ids are opaque and no logic ever reads an id's prefix (contract section 2).
Lifecycle:

    PLANNED -> DURABLE -> SUBMITTED -> WORKING / UNKNOWN -> CANCELLING -> terminal (FILLED, CANCELLED, REJECTED, NOT_SENT)

PLANNED exists only inside a Decision. DURABLE = the write-ahead record exists, so it may be sent (`may_send`). Each step
has a finite predecessor set (PREDECESSORS) and a terminal state never returns to an active one. A terminal step is
applied only from a durable FINAL OrderResult (events.check_event_chain).

OrderResult is what the venue told us. Only a FINAL result books an executed quantity, and it names its evidence. A bare
not-found is not evidence: it is an UNKNOWN result with `lookup=NOT_FOUND`, which carries no executed value. Resolving it
needs a final exchange record, or an explicit decision (`resolved_by`) corroborated by >= 2 agreeing position reads past
the visibility window (NOT_FOUND_CORROBORATED for nothing executed, POSITION_ADOPTED for an adopted quantity).
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .base import (CTX, ZERO, Record, check_ascii_text, check_client_id, check_id, check_symbol, check_text, non_negative, positive, record,
                   req)
from .account import Venue
from .reasons import ReasonCode

NOT_FOUND_WINDOW_MS = 20_000          # a lookup earlier than this after the send proves even less (legacy window)


class Side(enum.StrEnum):
    """Position side. LONG and SHORT are distinct; a side is never inferred from a quantity sign."""
    LONG = 'LONG'
    SHORT = 'SHORT'


class Purpose(enum.StrEnum):
    ENTRY = 'entry'          # open a new position (market, maker or trailing)
    ADD = 'add'              # add to a lot (DCA level / pyramid)
    REDUCE = 'reduce'        # partial close of a lot
    CLOSE = 'close'          # full close of a lot
    PROTECT = 'protect'      # reduce-only protective stop


OPENING = frozenset({Purpose.ENTRY, Purpose.ADD})
REDUCE_ONLY = frozenset({Purpose.REDUCE, Purpose.CLOSE, Purpose.PROTECT})


class OwnerKind(enum.StrEnum):
    """What `OrderIntent.owner_id` refers to (explicit, never inferred from the id)."""
    LOT = 'lot'                    # a lot of this portfolio (ADD / REDUCE / CLOSE / its PROTECT)
    ENTRY_INTENT = 'entry_intent'  # an unresolved market ENTRY intent (its provisional PROTECT)
    PORTFOLIO = 'portfolio'        # the portfolio aggregate itself: orphan cancel work


OWNER_KINDS = {Purpose.ADD: frozenset({OwnerKind.LOT, OwnerKind.PORTFOLIO}),
               Purpose.REDUCE: frozenset({OwnerKind.LOT, OwnerKind.PORTFOLIO}),
               Purpose.CLOSE: frozenset({OwnerKind.LOT, OwnerKind.PORTFOLIO}),
               Purpose.PROTECT: frozenset(OwnerKind)}
_ID_FAMILY = {OwnerKind.LOT: 'lot', OwnerKind.ENTRY_INTENT: 'int', OwnerKind.PORTFOLIO: 'pf'}


class OwnerFamily(enum.StrEnum):
    ENTRY = 'entry'            # opening risk that is not a lot yet
    LOT = 'lot'                # add / reduce / close of a lot
    PROTECTION = 'protection'  # the stop of a lot or of an unconfirmed entry
    ORPHAN = 'orphan'          # owner gone; only cancel work remains (derived, see Portfolio)


FAMILY = {Purpose.ENTRY: OwnerFamily.ENTRY, Purpose.ADD: OwnerFamily.LOT, Purpose.REDUCE: OwnerFamily.LOT,
          Purpose.CLOSE: OwnerFamily.LOT, Purpose.PROTECT: OwnerFamily.PROTECTION}
REASON_NAMESPACE = {Purpose.ENTRY: 'entry', Purpose.ADD: 'entry', Purpose.REDUCE: 'exit', Purpose.CLOSE: 'exit',
                    Purpose.PROTECT: 'protect'}


class OrderType(enum.StrEnum):
    MARKET = 'market'
    LIMIT_POST_ONLY = 'limit_post_only'   # maker
    STOP_MARKET = 'stop_market'           # protective stop


class IntentState(enum.StrEnum):
    PLANNED = 'planned'          # decided; not durable, must not be sent
    DURABLE = 'durable'          # write-ahead record exists; may be sent (an armed trailing entry waits here)
    SUBMITTED = 'submitted'      # the send was attempted
    WORKING = 'working'          # the exchange acknowledged it and it is open
    UNKNOWN = 'unknown'          # the answer was lost / the order cannot be read
    CANCELLING = 'cancelling'    # cancel requested (or a durable, never-sent intent being dropped)
    FILLED = 'filled'            # terminal: executed the whole request
    CANCELLED = 'cancelled'      # terminal: ended with part or nothing executed (cancel / expiry / corroborated not-found)
    REJECTED = 'rejected'        # terminal: refused, nothing executed
    NOT_SENT = 'not_sent'        # terminal: never reached the exchange


TERMINAL = frozenset({IntentState.FILLED, IntentState.CANCELLED, IntentState.REJECTED, IntentState.NOT_SENT})
LIVE = frozenset({IntentState.DURABLE, IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN,
                  IntentState.CANCELLING})     # the states an intent may have inside a Portfolio
CANCEL_ONLY = TERMINAL | {IntentState.CANCELLING}
POST_HOC_STATES = frozenset({IntentState.PLANNED, IntentState.DURABLE, IntentState.FILLED, IntentState.NOT_SENT})
BOOKED_PURPOSES = frozenset({Purpose.REDUCE, Purpose.CLOSE})   # ruling 1: only an external reduce / close is booked
_S = IntentState
INTENT_TRANSITIONS = {
    _S.PLANNED: frozenset({_S.DURABLE, _S.NOT_SENT}),
    _S.DURABLE: frozenset({_S.SUBMITTED, _S.CANCELLING, _S.NOT_SENT, _S.FILLED}),   # FILLED: a post-hoc booking only
    _S.SUBMITTED: frozenset({_S.WORKING, _S.UNKNOWN, _S.CANCELLING, _S.FILLED, _S.CANCELLED, _S.REJECTED}),
    _S.WORKING: frozenset({_S.UNKNOWN, _S.CANCELLING, _S.FILLED, _S.CANCELLED}),
    _S.UNKNOWN: frozenset({_S.WORKING, _S.CANCELLING, _S.FILLED, _S.CANCELLED, _S.REJECTED}),
    _S.CANCELLING: frozenset({_S.WORKING, _S.UNKNOWN, _S.FILLED, _S.CANCELLED, _S.NOT_SENT}),
    _S.FILLED: frozenset(), _S.CANCELLED: frozenset(), _S.REJECTED: frozenset(), _S.NOT_SENT: frozenset(),
}
PREDECESSORS = {s: frozenset(a for a, nxt in INTENT_TRANSITIONS.items() if s in nxt) for s in IntentState}
del _S


def can_transition(a, b):
    return IntentState(b) in INTENT_TRANSITIONS[IntentState(a)]


POST_HOC_REASON = ReasonCode.RECONCILE_EXTERNAL_CLOSE


def is_post_hoc(intent):
    """r3 item 3a: a lot REDUCE / CLOSE with reason reconcile.external_close books a reduce / close that already
    happened OUTSIDE the bot (REC-02 Q2). It is created by a RECONCILE decision, is never sent, and ends only by
    EXCHANGE_EXTERNAL (or NOT_SENT). Ruling 2: the reason is reconciliation-only - it never reads as permission to send
    (exit.manual is an operator-requested bot close and sends normally). Ruling 1: an external INCREASE is never
    booked (no post-hoc ENTRY / ADD): it stays quarantined and protect-only."""
    return intent.reason is POST_HOC_REASON


def booking_step_ok(intent, to_state):
    """A post-hoc booking only ever moves inside POST_HOC_STATES (never SUBMITTED / WORKING / UNKNOWN / CANCELLING):
    the one lifecycle rule the event chain AND the step-0 journal gate both apply."""
    return not is_post_hoc(intent) or IntentState(to_state) in POST_HOC_STATES


def may_send(intent):
    """Durability phase gate: only a DURABLE intent may be sent (and only a durable one exists outside a Decision);
    a post-hoc booking is never sent."""
    return intent.state is IntentState.DURABLE and not is_post_hoc(intent)


@record
class Arming(Record):
    """Trailing-entry trigger: send at market when price reaches `trigger_price` before `expires_at_ms`."""
    trigger_price: Decimal
    expires_at_ms: int

    def _validate(self, p):
        positive(self.trigger_price, p + '.trigger_price')


@record
class OrderIntent(Record):
    intent_id: str
    account_id: str
    decision_id: str                 # the Decision that created it (authority + audit chain)
    client_order_id: str             # REQUIRED, opaque, caller-supplied (NC-03 derives it from the intent id)
    purpose: Purpose
    order_type: OrderType
    state: IntentState
    symbol: str
    side: Side                       # POSITION side (a close of a LONG is side LONG)
    qty: Decimal
    reason: ReasonCode
    created_at_ms: int
    owner_id: str | None             # the owning lot / ENTRY intent / portfolio id; None for ENTRY (owns itself)
    owner_kind: OwnerKind | None     # what owner_id refers to; None exactly when owner_id is None
    slot_id: str | None       # strategy slot of an ENTRY; None for manual / one-shot
    price: Decimal | None     # limit price (maker only)
    stop_price: Decimal | None
    arm: Arming | None        # trailing entry trigger
    alt_client_order_id: str | None    # PROTECT: algo-order fallback id (owned from the write-ahead record on)
    seen_qty: Decimal | None  # unresolved size seen on the position for a market ENTRY (never booked)
    authorized_by: str | None  # dec_ of an audited operator one-shot authorization (opening risk while paused)
    replaces_intent_id: str | None  # REDUCE / CLOSE cancel-replace: the CANCELLING predecessor this intent replaces

    def _validate(self, p):
        p = f'{p}[{self.intent_id}]'
        check_id(self.intent_id, p + '.intent_id', 'int')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.decision_id, p + '.decision_id', 'dec')
        check_client_id(self.client_order_id, p + '.client_order_id')
        check_symbol(self.symbol, p + '.symbol')
        positive(self.qty, p + '.qty')
        u, t = self.purpose, self.order_type
        req(self.reason.namespace == REASON_NAMESPACE[u] or (self.reason is POST_HOC_REASON and u in BOOKED_PURPOSES),
            p + '.reason', f'a {u} intent needs a {REASON_NAMESPACE[u]}.* reason')
        if u is Purpose.ENTRY:
            req(self.owner_id is None and self.owner_kind is None, p + '.owner_id',
                'an ENTRY owns itself (its result creates the lot)')
        else:
            req(self.owner_kind in OWNER_KINDS[u], p + '.owner_kind', f'a {u} intent is not owned by {self.owner_kind}')
            check_id(self.owner_id, p + '.owner_id', _ID_FAMILY[self.owner_kind])   # format of the declared kind
        if self.orphan:
            req(self.state in CANCEL_ONLY, p + '.state', 'an orphan (portfolio-owned) order is cancel-only work')
        req(u is Purpose.ENTRY or self.slot_id is None, p + '.slot_id', 'only an ENTRY names a strategy slot')
        if self.slot_id is not None:
            check_text(self.slot_id, p + '.slot_id', 32)
        req((t is OrderType.STOP_MARKET) == (u is Purpose.PROTECT), p + '.order_type', 'PROTECT <=> stop_market')
        req(t is not OrderType.LIMIT_POST_ONLY or u in OPENING, p + '.order_type', 'only ENTRY / ADD may rest as a maker')
        if t is OrderType.LIMIT_POST_ONLY:
            req(self.price is not None, p + '.price', 'a limit order has a price')
            positive(self.price, p + '.price')
        else:
            req(self.price is None, p + '.price', 'only a limit order has a price')
        if t is OrderType.STOP_MARKET:
            req(self.stop_price is not None, p + '.stop_price', 'a stop has a stop price')
            positive(self.stop_price, p + '.stop_price')
            if self.alt_client_order_id is not None:
                check_client_id(self.alt_client_order_id, p + '.alt_client_order_id')
                req(self.alt_client_order_id != self.client_order_id, p + '.alt_client_order_id', 'equals the primary id')
        else:
            req(self.stop_price is None, p + '.stop_price', 'only a stop has a stop price')
            req(self.alt_client_order_id is None, p + '.alt_client_order_id', 'only a stop has an algo fallback id')
        if self.arm is not None:
            req(u is Purpose.ENTRY and t is OrderType.MARKET, p + '.arm', 'only a market ENTRY can be armed (trailing)')
            req(self.arm.expires_at_ms > self.created_at_ms, p + '.arm.expires_at_ms', 'expires before it was created')
        if self.seen_qty is not None:
            req(u is Purpose.ENTRY and t is OrderType.MARKET, p + '.seen_qty', 'only for an unresolved market ENTRY')
            non_negative(self.seen_qty, p + '.seen_qty')
            req(self.seen_qty <= self.qty, p + '.seen_qty', 'more than requested')
        if self.authorized_by is not None:
            req(u in OPENING, p + '.authorized_by', 'only opening intents need a one-shot authorization')
            check_id(self.authorized_by, p + '.authorized_by', 'dec')
        if is_post_hoc(self):                            # r3 DRAFT item 3: an external close booked, never sent
            req(u in BOOKED_PURPOSES and self.owner_kind is OwnerKind.LOT and t is OrderType.MARKET,
                p + '.reason', 'reconcile.external_close books an external reduce / close of a lot')
            req(self.state in POST_HOC_STATES, p + '.state', f'a post-hoc booking is never {self.state}')
            req(self.replaces_intent_id is None, p + '.replaces_intent_id', 'a post-hoc booking replaces nothing')
        if self.replaces_intent_id is not None:          # the explicit cancel-replace link (Codex P1 on af4e5f3)
            req(u in (Purpose.REDUCE, Purpose.CLOSE) and self.owner_kind is OwnerKind.LOT, p + '.replaces_intent_id',
                'only a lot REDUCE / CLOSE replaces a predecessor')
            check_id(self.replaces_intent_id, p + '.replaces_intent_id', 'int')
            req(self.replaces_intent_id != self.intent_id, p + '.replaces_intent_id', 'replaces itself')

    @property
    def orphan(self):
        """Owned by the portfolio aggregate because its lot / entry is gone: only cancel work remains."""
        return self.owner_kind is OwnerKind.PORTFOLIO

    @property
    def family(self):
        return OwnerFamily.ORPHAN if self.orphan else FAMILY[self.purpose]

    @property
    def reduce_only(self):
        return self.purpose in REDUCE_ONLY

    @property
    def opening(self):
        return self.purpose in OPENING

    @property
    def terminal(self):
        return self.state in TERMINAL

    @property
    def pullable(self):
        """Opening risk that can still be withdrawn without a fill: a resting maker or a not-yet-sent armed entry."""
        return self.opening and (self.order_type is OrderType.LIMIT_POST_ONLY or
                                 (self.arm is not None and self.state is IntentState.DURABLE))

    @property
    def client_ids(self):
        return (self.client_order_id,) if self.alt_client_order_id is None else (self.client_order_id,
                                                                                   self.alt_client_order_id)


# ----------------------------------------------------------------------------------------------------------- results
class ResultPhase(enum.StrEnum):
    UNKNOWN = 'unknown'      # sent; the answer is lost / the order cannot be read / not found
    KNOWN = 'known'          # the exchange has it, not final: its executed quantity is NOT trusted
    FINAL = 'final'          # the executed quantity is decided


class ResultStage(enum.StrEnum):
    """Durability phase of a result: received -> durable (ResultObserved appended) -> applied to ownership."""
    RECEIVED = 'received'
    DURABLE = 'durable'
    APPLIED = 'applied'


RESULT_STAGE_TRANSITIONS = {ResultStage.RECEIVED: frozenset({ResultStage.DURABLE}),
                            ResultStage.DURABLE: frozenset({ResultStage.APPLIED}), ResultStage.APPLIED: frozenset()}


def may_apply(stage):
    """Only a durable result may change ownership."""
    return ResultStage(stage) is ResultStage.DURABLE


class ExchangeStatus(enum.StrEnum):
    NEW = 'NEW'
    PARTIALLY_FILLED = 'PARTIALLY_FILLED'
    FILLED = 'FILLED'
    CANCELED = 'CANCELED'
    EXPIRED = 'EXPIRED'
    EXPIRED_IN_MATCH = 'EXPIRED_IN_MATCH'
    REJECTED = 'REJECTED'


OPEN_STATUSES = frozenset({ExchangeStatus.NEW, ExchangeStatus.PARTIALLY_FILLED})
FINAL_STATUSES = frozenset(ExchangeStatus) - OPEN_STATUSES


class Lookup(enum.StrEnum):
    NOT_FOUND = 'not_found'          # the lookup did not find the order: proves NOTHING on its own
    UNREADABLE = 'unreadable'


class Evidence(enum.StrEnum):
    EXCHANGE_FINAL = 'exchange_final'                   # a final exchange order record
    EXCHANGE_REFUSED = 'exchange_refused'               # the send was refused synchronously: nothing executed
    NOT_SENT = 'not_sent'                               # never left the process: nothing reached the exchange
    NOT_FOUND_CORROBORATED = 'not_found_corroborated'   # decided "nothing executed": not found + agreeing position reads
    POSITION_ADOPTED = 'position_adopted'               # decided "this quantity executed": adoption + agreeing reads
    EXCHANGE_EXTERNAL = 'exchange_external'             # r3 DRAFT: an external close, venue trade ids as corroboration


EXECUTING_EVIDENCE = frozenset({Evidence.EXCHANGE_FINAL, Evidence.POSITION_ADOPTED, Evidence.EXCHANGE_EXTERNAL})
DECIDED_EVIDENCE = frozenset({Evidence.NOT_FOUND_CORROBORATED, Evidence.POSITION_ADOPTED})


@record
class ExternalTrade(Record):
    """r3 item 3a: one venue trade of an external (manual) close. Its identity is (venue, symbol, trade_id): venue trade
    ids are symbol-scoped (Binance userTrades), so SOL trade 42 and BTC trade 42 are different trades."""
    trade_id: str
    venue: Venue
    symbol: str
    at_ms: int
    qty: Decimal
    price: Decimal

    def _validate(self, p):
        check_ascii_text(self.trade_id, p + '.trade_id', 64)
        check_symbol(self.symbol, p + '.symbol')
        positive(self.qty, p + '.qty')
        positive(self.price, p + '.price')


@record
class PositionRead(Record):
    """One exchange position read (the intent's symbol / side) used as corroboration."""
    at_ms: int
    qty: Decimal

    def _validate(self, p):
        non_negative(self.qty, p + '.qty')


@record
class OrderResult(Record):
    result_id: str
    intent_id: str
    account_id: str
    client_order_id: str
    phase: ResultPhase
    requested_qty: Decimal
    observed_at_ms: int
    exchange_order_id: str | None
    exchange_status: ExchangeStatus | None
    lookup: Lookup | None
    executed_qty: Decimal | None      # FINAL only
    avg_price: Decimal | None
    evidence: Evidence | None         # FINAL only
    corroboration: tuple[PositionRead, ...]
    resolved_by: str | None           # dec_ of the explicit resolution / adoption
    external_trades: tuple[ExternalTrade, ...]   # EXCHANGE_EXTERNAL only: the venue trades of the external close
    supersedes_result_id: str | None  # PR #44 P1-b: a late executed FINAL names the not_found_corroborated it supersedes

    def _validate(self, p):
        p = f'{p}[{self.result_id}]'
        check_id(self.result_id, p + '.result_id', 'res')
        check_id(self.intent_id, p + '.intent_id', 'int')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_client_id(self.client_order_id, p + '.client_order_id')
        positive(self.requested_qty, p + '.requested_qty')
        if self.exchange_order_id is not None:
            check_text(self.exchange_order_id, p + '.exchange_order_id', 64)
        ph, ev, st = self.phase, self.evidence, self.exchange_status
        if ph is not ResultPhase.FINAL:
            req(self.executed_qty is None and self.avg_price is None and ev is None and not self.corroboration
                and self.resolved_by is None, p, f'a {ph} result books nothing (no executed qty / price / evidence)')
            req(self.supersedes_result_id is None, p + '.supersedes_result_id',
                'only an executed FINAL exchange record supersedes a prior result')
            if ph is ResultPhase.UNKNOWN:
                req(st is None, p + '.exchange_status', 'an UNKNOWN result has no exchange status')
            else:
                req(self.lookup is None, p + '.lookup', 'only an UNKNOWN result records a failed lookup')
                req(st in OPEN_STATUSES and self.exchange_order_id is not None, p + '.exchange_status',
                    'KNOWN = an open exchange order (NEW / PARTIALLY_FILLED) with its id')
            return
        req(self.lookup is None, p + '.lookup', 'a final result is not a lookup failure')
        req(ev is not None, p + '.evidence', 'a final result names its evidence')
        if self.supersedes_result_id is not None:        # PR #44 P1-b: a NEW fact that names the prior one
            check_id(self.supersedes_result_id, p + '.supersedes_result_id', 'res')
            req(self.supersedes_result_id != self.result_id, p + '.supersedes_result_id', 'a result never supersedes itself')
            req(ev is Evidence.EXCHANGE_FINAL and self.executed_qty is not None and self.executed_qty > 0,
                p + '.supersedes_result_id', 'only an executed FINAL exchange record supersedes a prior result')
        req(self.executed_qty is not None, p + '.executed_qty', 'a final result decides the executed qty')
        non_negative(self.executed_qty, p + '.executed_qty')
        req(self.executed_qty <= self.requested_qty, p + '.executed_qty', 'more than requested')
        if self.executed_qty > 0:
            req(ev in EXECUTING_EVIDENCE, p + '.evidence', 'only a final exchange record or an adoption books an execution')
            req(self.avg_price is not None and self.avg_price > 0, p + '.avg_price', 'an execution has a price')
        else:
            req(self.avg_price is None, p + '.avg_price', 'nothing executed has no price')
            req(ev is not Evidence.POSITION_ADOPTED, p + '.evidence', 'an adoption adopts a quantity')
        if ev is Evidence.EXCHANGE_FINAL:
            req(st in FINAL_STATUSES and self.exchange_order_id is not None, p + '.exchange_status',
                'needs the final exchange status and order id')
            req(st is not ExchangeStatus.FILLED or self.executed_qty == self.requested_qty, p + '.executed_qty',
                'FILLED executes the whole request')
            req(st is not ExchangeStatus.REJECTED or self.executed_qty == 0, p + '.executed_qty', 'REJECTED executes nothing')
        else:
            req(st is None and self.exchange_order_id is None, p + '.exchange_status', f'{ev}: no exchange order record')
        if ev in (Evidence.EXCHANGE_REFUSED, Evidence.NOT_SENT, Evidence.NOT_FOUND_CORROBORATED):
            req(self.executed_qty == 0, p + '.executed_qty', f'{ev} executes nothing')
        trades = self.external_trades
        if ev is Evidence.EXCHANGE_EXTERNAL:            # r3 DRAFT item 3 (REC-02 Q2)
            check_id(self.resolved_by, p + '.resolved_by', 'dec')
            req(len(trades) >= 1, p + '.external_trades', 'an external close names its venue trades')
            req(len({(x.venue, x.symbol, x.trade_id) for x in trades}) == len(trades), p + '.external_trades',
                'duplicate trade id')
            total = ZERO
            for x in trades:
                total = CTX.add(total, x.qty)
            req(total == self.executed_qty == self.requested_qty, p + '.executed_qty',
                'the booking is exactly the sum of its venue trades')
            req(min(x.price for x in trades) <= self.avg_price <= max(x.price for x in trades), p + '.avg_price',
                'outside the venue trade prices')
            req(all(x.at_ms <= self.observed_at_ms for x in trades), p + '.external_trades', 'a trade after the observation')
            req(not self.corroboration, p + '.corroboration', 'the venue trades are the corroboration')
            return
        req(not trades, p + '.external_trades', 'only an external close carries venue trades')
        reads = self.corroboration
        if ev in DECIDED_EVIDENCE:
            check_id(self.resolved_by, p + '.resolved_by', 'dec')
            req(len(reads) >= 2, p + '.corroboration', 'needs at least two position reads')
            req(all(a.at_ms < b.at_ms for a, b in zip(reads, reads[1:])), p + '.corroboration', 'reads strictly in time')
            req(len({r.qty for r in reads}) == 1, p + '.corroboration', 'the position changed between the reads')
            req(ev is not Evidence.POSITION_ADOPTED or self.executed_qty <= reads[-1].qty, p + '.executed_qty',
                'adopts more than the position holds')
        else:
            req(self.resolved_by is None, p + '.resolved_by', 'only a decided resolution names its decision')
            req(not reads, p + '.corroboration', 'only a decided resolution carries position reads')

    @property
    def booked_qty(self):
        """The quantity that may be booked: only from a FINAL result, else None (never 0 for "unknown")."""
        return self.executed_qty if self.phase is ResultPhase.FINAL else None


def terminal_for(result):
    """The terminal IntentState a FINAL result moves its intent to (None for a non-final result)."""
    if result.phase is not ResultPhase.FINAL:
        return None
    if result.evidence is Evidence.NOT_SENT:
        return IntentState.NOT_SENT
    if result.executed_qty == result.requested_qty:
        return IntentState.FILLED
    if result.evidence is Evidence.EXCHANGE_REFUSED or result.exchange_status is ExchangeStatus.REJECTED:
        return IntentState.REJECTED
    return IntentState.CANCELLED


def supersedes(prior, new):
    """r3 DRAFT item 3b (REC-02 Q2 / R08): exchange evidence wins. A FINAL exchange record of the same owned order that
    shows an execution supersedes an earlier corroborated not-found ("nothing executed") of that intent. Pure.
    (An execution is guaranteed by the record itself: only an executed FINAL exchange record may carry
    supersedes_result_id, and the late record must name the prior one.)"""
    return (prior.phase is ResultPhase.FINAL and prior.evidence is Evidence.NOT_FOUND_CORROBORATED
            and new.phase is ResultPhase.FINAL and new.evidence is Evidence.EXCHANGE_FINAL
            and new.intent_id == prior.intent_id and new.client_order_id == prior.client_order_id
            and new.account_id == prior.account_id and new.observed_at_ms >= prior.observed_at_ms
            and new.supersedes_result_id == prior.result_id and new.result_id != prior.result_id)   # PR #44 P1-b


def check_result_for_intent(intent, result, sent_at_ms):
    """Cross-record rules of a result against its durable intent. `sent_at_ms` is when it was submitted (None = never).
    Raises InvalidRecord."""
    p = f'OrderResult[{result.result_id}]'
    req(result.intent_id == intent.intent_id and result.client_order_id in intent.client_ids, p + '.client_order_id',
        'belongs to another intent')
    req(result.account_id == intent.account_id, p + '.account_id', 'belongs to another account')
    req(result.requested_qty == intent.qty, p + '.requested_qty', 'differs from the intent qty')
    req(result.observed_at_ms >= intent.created_at_ms, p + '.observed_at_ms', 'observed before the intent existed')
    ev = result.evidence
    req((ev is Evidence.EXCHANGE_EXTERNAL) <= is_post_hoc(intent), p + '.evidence',
        'exchange_external only books a post-hoc (reconcile.external_close) intent')
    if is_post_hoc(intent):
        req(sent_at_ms is None and result.phase is ResultPhase.FINAL
            and ev in (Evidence.EXCHANGE_EXTERNAL, Evidence.NOT_SENT), p + '.evidence',
            'a post-hoc booking is never sent and ends exchange_external (or not_sent)')
        req(all(x.symbol == intent.symbol for x in result.external_trades), p + '.external_trades',
            "a venue trade of another symbol than the booking intent's symbol")             # Codex re-review P2-a
        req(ev is not Evidence.EXCHANGE_EXTERNAL or result.resolved_by == intent.decision_id, p + '.resolved_by',
            'an external booking is resolved by the RECONCILE decision that booked it')       # PR #44 P2-2
        return
    if sent_at_ms is None:
        req(result.phase is ResultPhase.FINAL and ev is Evidence.NOT_SENT, p + '.evidence',
            'an intent that was never submitted can only end NOT_SENT')
        return
    req(ev is not Evidence.NOT_SENT, p + '.evidence', 'the intent was submitted')
    if ev in DECIDED_EVIDENCE:
        req(result.corroboration[0].at_ms >= sent_at_ms + NOT_FOUND_WINDOW_MS, p + '.corroboration',
            'position reads inside the visibility window corroborate nothing')
    if ev is Evidence.POSITION_ADOPTED:
        req(intent.opening, p + '.evidence', 'only opening risk is adopted')
