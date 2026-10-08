"""Order intents and order results (rulings 1, 3 and 7).

OrderIntent is the write-ahead record: it is durable BEFORE the send (IntentRecorded event), for every purpose, closes
and stops included. There is no separate EntryIntent: an unconfirmed market entry, a resting maker entry and an armed
trailing entry are all OrderIntents with purpose ENTRY and a lifecycle `state`. The client order id is REQUIRED, so an
id-less, quantity-only pending order cannot be represented. Protective orders use a deterministic client id, so a retry
re-sends the same id and the exchange refuses a duplicate.

Ownership families (explicit, derived from purpose): ENTRY (opening risk not yet a lot), LOT (add/reduce/close of a lot),
PROTECTION (the stop of a lot or of an unconfirmed entry) and ORPHAN (owned orders queued for cancellation,
portfolio.OrphanCancel).

OrderResult is what the venue told us, durable BEFORE it is applied (ResultObserved event). Only a FINAL result books an
executed quantity, and a FINAL result names its evidence. A bare not-found is not evidence: it is an UNKNOWN result with
`lookup=NOT_FOUND`. Nothing executed after a not-found needs NOT_FOUND_CORROBORATED, i.e. at least two position reads past
the visibility window that agree.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .base import (Record, check_cid, check_id, check_symbol, dec_str, deterministic_cid, non_negative, positive,
                   record, req)
from .reasons import ReasonCode

NOT_FOUND_WINDOW_MS = 20_000          # legacy visibility window: a lookup earlier than this proves even less


class Side(enum.StrEnum):
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


class OwnerFamily(enum.StrEnum):
    ENTRY = 'entry'
    LOT = 'lot'
    PROTECTION = 'protection'
    ORPHAN = 'orphan'


FAMILY = {Purpose.ENTRY: OwnerFamily.ENTRY, Purpose.ADD: OwnerFamily.LOT, Purpose.REDUCE: OwnerFamily.LOT,
          Purpose.CLOSE: OwnerFamily.LOT, Purpose.PROTECT: OwnerFamily.PROTECTION}
REASON_STAGE = {Purpose.ENTRY: 'entry', Purpose.ADD: 'entry', Purpose.REDUCE: 'exit', Purpose.CLOSE: 'exit',
                Purpose.PROTECT: 'protect'}


class OrderType(enum.StrEnum):
    MARKET = 'market'
    LIMIT_POST_ONLY = 'limit_gtx'     # maker
    STOP_MARKET = 'stop_market'       # protective stop


class IntentState(enum.StrEnum):
    ARMED = 'armed'                   # trailing entry: durable, nothing on the exchange yet
    SENDING = 'sending'               # durable; the send may or may not have reached the exchange
    WORKING = 'working'               # the exchange acknowledged it and it is not final (resting maker / stop)
    UNRESOLVED = 'unresolved'         # the answer was lost or unreadable
    CANCELLING = 'cancelling'         # cancel requested (or an armed intent being dropped)
    CANCEL_UNRESOLVED = 'cancel_unresolved'


SENT_STATES = frozenset({IntentState.SENDING, IntentState.WORKING, IntentState.UNRESOLVED})
CANCEL_STATES = frozenset({IntentState.CANCELLING, IntentState.CANCEL_UNRESOLVED})
# allowed lifecycle steps; leaving the portfolio happens only through a FINAL OrderResult
INTENT_TRANSITIONS = {
    IntentState.ARMED: frozenset({IntentState.SENDING, IntentState.CANCELLING}),
    IntentState.SENDING: frozenset({IntentState.WORKING, IntentState.UNRESOLVED, IntentState.CANCELLING}),
    IntentState.UNRESOLVED: frozenset({IntentState.WORKING, IntentState.CANCELLING}),
    IntentState.WORKING: frozenset({IntentState.CANCELLING}),
    IntentState.CANCELLING: frozenset({IntentState.CANCEL_UNRESOLVED}),
    IntentState.CANCEL_UNRESOLVED: frozenset({IntentState.CANCELLING}),
}


def protect_cid(account_id, owner_id, side, qty, stop_price, *, alt=False):
    """Deterministic client id of a protective stop: the same protection request always gets the same id."""
    return deterministic_cid('q' if alt else 'p', account_id, owner_id, side, dec_str(qty), dec_str(stop_price))


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
    decision_id: str                 # the Decision that created it (audit chain)
    client_order_id: str             # REQUIRED
    purpose: Purpose
    order_type: OrderType
    state: IntentState
    symbol: str
    side: Side                       # POSITION side (a close of a LONG is side LONG)
    qty: Decimal
    reason: ReasonCode
    created_at_ms: int
    owner_id: str | None = None      # lot_ (ADD/REDUCE/CLOSE/PROTECT) or int_ (PROTECT of an unconfirmed entry); None for ENTRY
    slot_id: str | None = None       # strategy slot of an ENTRY; None for manual / one-shot
    price: Decimal | None = None     # limit price (maker only)
    stop_price: Decimal | None = None
    arm: Arming | None = None        # trailing entry trigger
    alt_client_order_id: str | None = None    # algo-order fallback id of a stop (deterministic too)
    seen_qty: Decimal | None = None  # unresolved size seen on the position for a market ENTRY (never booked)
    authorized_by: str | None = None  # dec_ of an audited operator one-shot authorization (opening risk while paused)

    def _validate(self, p):
        p = f'{p}[{self.intent_id}]'
        check_id(self.intent_id, p + '.intent_id', 'int')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.decision_id, p + '.decision_id', 'dec')
        check_cid(self.client_order_id, p + '.client_order_id')
        check_symbol(self.symbol, p + '.symbol')
        positive(self.qty, p + '.qty')
        u = self.purpose
        req(self.reason.stage == REASON_STAGE[u], p + '.reason', f'a {u} intent needs a {REASON_STAGE[u]}.* reason')
        # owner per family
        if u is Purpose.ENTRY:
            req(self.owner_id is None, p + '.owner_id', 'an ENTRY owns itself (its result creates the lot)')
        elif u is Purpose.PROTECT:
            check_id(self.owner_id, p + '.owner_id', 'lot', 'int')
        else:
            check_id(self.owner_id, p + '.owner_id', 'lot')
        req(u is Purpose.ENTRY or self.slot_id is None, p + '.slot_id', 'only an ENTRY names a strategy slot')
        req(self.slot_id is None or 0 < len(self.slot_id) <= 32 and self.slot_id.isprintable(), p + '.slot_id', '1..32 chars')
        # order type
        t = self.order_type
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
            req(self.client_order_id == protect_cid(self.account_id, self.owner_id, self.side, self.qty, self.stop_price),
                p + '.client_order_id', 'a protective stop uses its deterministic client id')
            req(self.alt_client_order_id is None or self.alt_client_order_id == protect_cid(
                self.account_id, self.owner_id, self.side, self.qty, self.stop_price, alt=True),
                p + '.alt_client_order_id', 'the algo fallback id is deterministic too')
        else:
            req(self.stop_price is None, p + '.stop_price', 'only a stop has a stop price')
            req(self.alt_client_order_id is None, p + '.alt_client_order_id', 'only a stop has an algo fallback id')
        # trailing / unresolved size / one-shot
        if self.arm is not None:
            req(u is Purpose.ENTRY and t is OrderType.MARKET, p + '.arm', 'only a market ENTRY can be armed (trailing)')
            req(self.arm.expires_at_ms > self.created_at_ms, p + '.arm.expires_at_ms', 'expires before it was created')
        req(self.state is not IntentState.ARMED or self.arm is not None, p + '.state', 'ARMED needs a trailing trigger')
        if self.seen_qty is not None:
            req(u is Purpose.ENTRY and t is OrderType.MARKET, p + '.seen_qty', 'only for an unresolved market ENTRY')
            non_negative(self.seen_qty, p + '.seen_qty')
            req(self.seen_qty <= self.qty, p + '.seen_qty', 'more than requested')
        if self.authorized_by is not None:
            req(u in OPENING, p + '.authorized_by', 'only opening intents need a one-shot authorization')
            check_id(self.authorized_by, p + '.authorized_by', 'dec')

    @property
    def family(self):
        return FAMILY[self.purpose]

    @property
    def reduce_only(self):
        return self.purpose in REDUCE_ONLY

    @property
    def opening(self):
        return self.purpose in OPENING

    @property
    def pullable(self):
        """An opening intent that can still be withdrawn without a fill: a resting maker or an armed trailing entry."""
        return self.opening and (self.order_type is OrderType.LIMIT_POST_ONLY or self.state is IntentState.ARMED)

    @property
    def client_ids(self):
        return (self.client_order_id,) if self.alt_client_order_id is None else (self.client_order_id,
                                                                                   self.alt_client_order_id)


# ----------------------------------------------------------------------------------------------------------- results
class ResultPhase(enum.StrEnum):
    UNKNOWN = 'unknown'      # sent; the answer is lost / the order cannot be read
    KNOWN = 'known'          # the exchange has it, not final: its executed quantity is NOT trusted
    FINAL = 'final'          # the executed quantity is decided


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
    NOT_FOUND_CORROBORATED = 'not_found_corroborated'   # not found AND >= 2 agreeing position reads past the window
    POSITION_ADOPTED = 'position_adopted'               # explicitly adopted (operator / reconciliation decision)
    NOT_SENT = 'not_sent'                               # never left ARMED: nothing reached the exchange


EXECUTING_EVIDENCE = frozenset({Evidence.EXCHANGE_FINAL, Evidence.POSITION_ADOPTED})


@record
class PositionRead(Record):
    """One exchange position read (the intent's symbol / side) used to corroborate a not-found."""
    at_ms: int
    qty: Decimal

    def _validate(self, p):
        non_negative(self.qty, p + '.qty')


@record
class OrderResult(Record):
    intent_id: str
    account_id: str
    client_order_id: str
    phase: ResultPhase
    requested_qty: Decimal
    observed_at_ms: int
    exchange_order_id: str | None = None
    exchange_status: ExchangeStatus | None = None
    lookup: Lookup | None = None
    executed_qty: Decimal | None = None      # FINAL only
    avg_price: Decimal | None = None
    evidence: Evidence | None = None         # FINAL only
    corroboration: tuple[PositionRead, ...] = ()
    adopted_by: str | None = None            # dec_ of the explicit adoption

    def _validate(self, p):
        p = f'{p}[{self.intent_id}]'
        check_id(self.intent_id, p + '.intent_id', 'int')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_cid(self.client_order_id, p + '.client_order_id')
        positive(self.requested_qty, p + '.requested_qty')
        req(self.exchange_order_id is None or self.exchange_order_id.isdigit() and len(self.exchange_order_id) <= 24,
            p + '.exchange_order_id', 'an exchange order id is digits')
        ph, ev, st = self.phase, self.evidence, self.exchange_status
        if ph is not ResultPhase.FINAL:
            req(self.executed_qty is None and self.avg_price is None and ev is None and not self.corroboration
                and self.adopted_by is None, p, f'a {ph} result books nothing (no executed qty / price / evidence)')
        if ph is ResultPhase.UNKNOWN:
            req(st is None, p + '.exchange_status', 'an UNKNOWN result has no exchange status')
        else:
            req(self.lookup is None, p + '.lookup', 'only an UNKNOWN result records a failed lookup')
        if ph is ResultPhase.KNOWN:
            req(st in OPEN_STATUSES and self.exchange_order_id is not None, p + '.exchange_status',
                'KNOWN = an open exchange order (NEW / PARTIALLY_FILLED) with its id')
            return
        if ph is ResultPhase.UNKNOWN:
            return
        # FINAL
        req(ev is not None, p + '.evidence', 'a final result names its evidence')
        req(self.executed_qty is not None, p + '.executed_qty', 'a final result decides the executed qty')
        non_negative(self.executed_qty, p + '.executed_qty')
        req(self.executed_qty <= self.requested_qty, p + '.executed_qty', 'more than requested')
        if self.executed_qty > 0:
            req(ev in EXECUTING_EVIDENCE, p + '.evidence', 'only a final exchange record or an adoption books an execution')
            req(self.avg_price is not None and self.avg_price > 0, p + '.avg_price', 'an execution has a price')
        else:
            req(self.avg_price is None, p + '.avg_price', 'nothing executed has no price')
        if ev is Evidence.EXCHANGE_FINAL:
            req(st in FINAL_STATUSES and self.exchange_order_id is not None, p + '.exchange_status',
                'needs the final exchange status and order id')
            req(st is not ExchangeStatus.FILLED or self.executed_qty == self.requested_qty, p + '.executed_qty',
                'FILLED executes the whole request')
            req(st is not ExchangeStatus.REJECTED or self.executed_qty == 0, p + '.executed_qty', 'REJECTED executes nothing')
        else:
            req(st is None and self.exchange_order_id is None, p + '.exchange_status',
                f'{ev}: there is no exchange order record')
        if ev is Evidence.POSITION_ADOPTED:
            check_id(self.adopted_by, p + '.adopted_by', 'dec')
            req(self.executed_qty > 0, p + '.executed_qty', 'an adoption adopts a quantity')
        else:
            req(self.adopted_by is None, p + '.adopted_by', 'only an adoption names its decision')
        if ev in (Evidence.EXCHANGE_REFUSED, Evidence.NOT_SENT, Evidence.NOT_FOUND_CORROBORATED):
            req(self.executed_qty == 0, p + '.executed_qty', f'{ev} executes nothing')
        reads = self.corroboration
        if ev is Evidence.NOT_FOUND_CORROBORATED:
            req(len(reads) >= 2, p + '.corroboration', 'needs at least two position reads')
            req(all(a.at_ms < b.at_ms for a, b in zip(reads, reads[1:])), p + '.corroboration', 'reads strictly in time')
            req(len({r.qty for r in reads}) == 1, p + '.corroboration', 'the position changed between the reads')
        else:
            req(not reads, p + '.corroboration', 'only a corroborated not-found carries position reads')

    @property
    def booked_qty(self):
        """The quantity that may be booked: only from a FINAL result (final record or adoption), else None."""
        return self.executed_qty if self.phase is ResultPhase.FINAL else None


def check_result_for_intent(intent, result, sent_at_ms):
    """Cross-record rules of a result against its durable intent. `sent_at_ms` is when the intent left ARMED/was sent
    (None = never sent). Raises InvalidRecord."""
    p = f'OrderResult[{result.intent_id}]'
    req(result.intent_id == intent.intent_id and result.client_order_id in intent.client_ids, p + '.client_order_id',
        'belongs to another intent')
    req(result.account_id == intent.account_id, p + '.account_id', 'belongs to another account')
    req(result.requested_qty == intent.qty, p + '.requested_qty', 'differs from the intent qty')
    req(result.observed_at_ms >= intent.created_at_ms, p + '.observed_at_ms', 'observed before the intent existed')
    ev = result.evidence
    if sent_at_ms is None:
        req(result.phase is ResultPhase.FINAL and ev is Evidence.NOT_SENT, p + '.evidence',
            'an intent that was never sent can only end NOT_SENT')
        return
    req(ev is not Evidence.NOT_SENT, p + '.evidence', 'the intent was sent')
    if ev is Evidence.NOT_FOUND_CORROBORATED:
        req(result.corroboration[0].at_ms >= sent_at_ms + NOT_FOUND_WINDOW_MS, p + '.corroboration',
            'position reads inside the visibility window corroborate nothing')
    if ev is Evidence.POSITION_ADOPTED:
        req(intent.opening, p + '.evidence', 'only opening risk is adopted')


