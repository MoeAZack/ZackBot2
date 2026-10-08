"""VenuePort: the fixed boundary every venue adapter satisfies (STEP0_INTERFACE.md section 4).

Satisfied by FakeVenue (slice S1, bar-driven) and by TestnetVenue wrapping newcore.venue's BinanceTestnetTransport
(nc-venue-testnet). Synchronous, called outside state locks, ONE venue request per order call, never retries and never
falls back on its own: the Runner owns retry and the classic -> algo stop route. Every call returns a typed outcome;
none raises for a venue answer (a bad argument raises PortValueError before anything is sent).

Order outcome kinds are the transport's OrderOutcomeKind values one to one. Mapping to NC-01 OrderResult (by the caller):
  KNOWN -> phase KNOWN (executed qty NOT trusted)      FINAL -> phase FINAL, evidence exchange_final
  REJECTED (submit) -> FINAL exchange_refused, 0 executed   REJECTED (cancel / query) -> no result; the order is unchanged
  UNKNOWN -> phase UNKNOWN                             NOT_FOUND -> phase UNKNOWN + lookup not_found (books NOTHING)
  ACKNOWLEDGED -> no result; confirm with query()
Reads return ReadOutcome: OK (value) / REJECTED / UNKNOWN. UNKNOWN never carries a value: unknown is never empty.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from newcore.domain import Side

from .values import (ROUTES, check_choice, check_client_id, check_decimal, check_ms, check_symbol, check_text, plain,
                     req)


class OutcomeKind(enum.StrEnum):        # values equal newcore.venue.outcomes.OrderOutcomeKind
    KNOWN = 'known'
    FINAL = 'final'
    ACKNOWLEDGED = 'acknowledged'
    REJECTED = 'rejected'
    UNKNOWN = 'unknown'
    NOT_FOUND = 'not_found'


class ReadKind(enum.StrEnum):           # values equal newcore.venue.outcomes.ReadKind
    OK = 'ok'
    REJECTED = 'rejected'
    UNKNOWN = 'unknown'


SIDES = tuple(s.value for s in Side)        # NC-01 position sides


def _side(obj, name='position_side'):
    object.__setattr__(obj, name, plain(getattr(obj, name)))
    check_choice(getattr(obj, name), f'{type(obj).__name__}.{name}', SIDES)


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRef:
    """Addresses one order by its deterministic client id. route = 'classic' (/fapi/v1/order) or 'algo' (/algoOrder)."""
    symbol: str
    client_id: str
    route: str = 'classic'

    def __post_init__(self):
        check_symbol(self.symbol, 'OrderRef.symbol')
        check_client_id(self.client_id, 'OrderRef.client_id')
        object.__setattr__(self, 'route', plain(self.route))
        check_choice(self.route, 'OrderRef.route', ROUTES)


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketOrder:
    """reduce=False opens / adds on position_side; reduce=True reduces / closes it and can never add exposure (hedge
    mode: opposite side on the same positionSide, no reduceOnly flag; one-way: reduceOnly=true)."""
    ref: OrderRef
    position_side: str
    qty: Decimal                 # already quantized DOWN to the step by the caller
    reduce: bool

    def __post_init__(self):
        req(isinstance(self.ref, OrderRef) and self.ref.route == 'classic', 'MarketOrder.ref', 'a classic OrderRef')
        _side(self)
        check_decimal(self.qty, 'MarketOrder.qty', positive=True)
        req(type(self.reduce) is bool, 'MarketOrder.reduce', 'a bool')


@dataclass(frozen=True, slots=True, kw_only=True)
class StopOrder:
    """Reduce-only protective STOP_MARKET on the mark price for qty of position_side; ref.route picks the endpoint."""
    ref: OrderRef
    position_side: str
    qty: Decimal
    stop_price: Decimal

    def __post_init__(self):
        req(isinstance(self.ref, OrderRef), 'StopOrder.ref', 'an OrderRef')
        _side(self)
        check_decimal(self.qty, 'StopOrder.qty', positive=True)
        check_decimal(self.stop_price, 'StopOrder.stop_price', positive=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderOutcome:
    kind: OutcomeKind
    ref: OrderRef
    observed_at_ms: int                       # injected clock, integer UTC ms
    status: str | None = None                 # venue status verbatim (KNOWN / FINAL)
    exchange_order_id: str | None = None      # KNOWN / FINAL; for a triggered algo stop: the child order id
    executed_qty: Decimal | None = None       # FINAL only
    avg_price: Decimal | None = None          # FINAL with executed_qty > 0 only
    error_code: int | None = None             # REJECTED / NOT_FOUND (required), UNKNOWN (ambiguous code, optional)
    detail: str | None = None                 # stable token (e.g. 'timeout', 'echo_mismatch', 'algo_route'), never text

    def __post_init__(self):
        p = 'OrderOutcome'
        object.__setattr__(self, 'kind', OutcomeKind(plain(self.kind)))
        k = self.kind
        req(isinstance(self.ref, OrderRef), p + '.ref', 'an OrderRef')
        check_ms(self.observed_at_ms, p + '.observed_at_ms')
        record = k in (OutcomeKind.KNOWN, OutcomeKind.FINAL)
        req((self.status is not None and self.exchange_order_id is not None) == record, p + '.status',
            'status and exchange order id exactly on KNOWN / FINAL')
        if record:
            check_text(self.status, p + '.status', 32)
            check_text(self.exchange_order_id, p + '.exchange_order_id', 64)
        req((self.executed_qty is not None) == (k is OutcomeKind.FINAL), p + '.executed_qty',
            'only a FINAL outcome carries an executed quantity (never KNOWN / UNKNOWN / NOT_FOUND)')
        if self.executed_qty is not None:
            check_decimal(self.executed_qty, p + '.executed_qty', nonneg=True)
            req((self.avg_price is not None) == (self.executed_qty > 0), p + '.avg_price', 'price iff something executed')
        else:
            req(self.avg_price is None, p + '.avg_price', 'FINAL only')
        if self.avg_price is not None:
            check_decimal(self.avg_price, p + '.avg_price', positive=True)
        if k in (OutcomeKind.REJECTED, OutcomeKind.NOT_FOUND):
            req(type(self.error_code) is int, p + '.error_code', f'{k} names the venue error code')
        elif k is not OutcomeKind.UNKNOWN:
            req(self.error_code is None, p + '.error_code', f'no error code on {k}')
        if self.detail is not None:
            check_text(self.detail, p + '.detail', 32)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadOutcome:
    kind: ReadKind
    observed_at_ms: int
    value: tuple | None = None                # OK only (an empty tuple is a TRUSTED empty)
    error_code: int | None = None             # REJECTED only
    detail: str | None = None

    def __post_init__(self):
        object.__setattr__(self, 'kind', ReadKind(plain(self.kind)))
        check_ms(self.observed_at_ms, 'ReadOutcome.observed_at_ms')
        ok = self.kind is ReadKind.OK
        req((type(self.value) is tuple) == ok and (self.value is None) == (not ok), 'ReadOutcome.value',
            'a tuple exactly when OK (UNKNOWN / REJECTED are never empty)')
        req((type(self.error_code) is int) == (self.kind is ReadKind.REJECTED), 'ReadOutcome.error_code',
            'exactly on REJECTED')
        if self.detail is not None:
            check_text(self.detail, 'ReadOutcome.detail', 32)


@dataclass(frozen=True, slots=True, kw_only=True)
class VenuePosition:
    symbol: str
    side: str                    # position side; never inferred from a quantity sign
    qty: Decimal                 # magnitude, >= 0
    entry_price: Decimal         # >= 0 (0 when flat)

    def __post_init__(self):
        check_symbol(self.symbol, 'VenuePosition.symbol')
        _side(self, 'side')
        check_decimal(self.qty, 'VenuePosition.qty', nonneg=True)
        check_decimal(self.entry_price, 'VenuePosition.entry_price', nonneg=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueOrder:
    """An open order (classic or algo), owned or foreign: ownership is decided by matching client ids to the journal."""
    ref: OrderRef
    exchange_order_id: str
    position_side: str
    reduce: bool
    order_type: str              # venue type verbatim (MARKET, STOP_MARKET, LIMIT, ...)
    status: str
    qty: Decimal                 # 0 only for a closePosition order
    close_position: bool
    stop_price: Decimal | None

    def __post_init__(self):
        req(isinstance(self.ref, OrderRef), 'VenueOrder.ref', 'an OrderRef')
        check_text(self.exchange_order_id, 'VenueOrder.exchange_order_id', 64)
        _side(self)
        req(type(self.reduce) is bool and type(self.close_position) is bool, 'VenueOrder.reduce', 'bools')
        check_text(self.order_type, 'VenueOrder.order_type', 32)
        check_text(self.status, 'VenueOrder.status', 32)
        check_decimal(self.qty, 'VenueOrder.qty', nonneg=True)
        req(self.qty > 0 or self.close_position, 'VenueOrder.qty', 'zero only on a closePosition order')
        if self.stop_price is not None:
            check_decimal(self.stop_price, 'VenueOrder.stop_price', positive=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueFill:
    """One execution (Binance userTrades row): the ledger's fee and price truth."""
    trade_id: str
    exchange_order_id: str
    symbol: str
    position_side: str
    qty: Decimal
    price: Decimal
    fee: Decimal                 # signed as the venue reports it
    fee_asset: str
    realized_pnl: Decimal
    maker: bool
    at_ms: int

    def __post_init__(self):
        check_text(self.trade_id, 'VenueFill.trade_id', 64)
        check_text(self.exchange_order_id, 'VenueFill.exchange_order_id', 64)
        check_symbol(self.symbol, 'VenueFill.symbol')
        _side(self)
        check_decimal(self.qty, 'VenueFill.qty', positive=True)
        check_decimal(self.price, 'VenueFill.price', positive=True)
        check_decimal(self.fee, 'VenueFill.fee')
        check_text(self.fee_asset, 'VenueFill.fee_asset', 16)
        check_decimal(self.realized_pnl, 'VenueFill.realized_pnl')
        req(type(self.maker) is bool, 'VenueFill.maker', 'a bool')
        check_ms(self.at_ms, 'VenueFill.at_ms')


@runtime_checkable
class VenuePort(Protocol):
    def submit_market(self, order: MarketOrder) -> OrderOutcome: ...

    def submit_stop(self, order: StopOrder) -> OrderOutcome: ...

    def cancel(self, ref: OrderRef) -> OrderOutcome: ...

    def query(self, ref: OrderRef) -> OrderOutcome: ...

    def positions(self, symbol: str | None = None) -> ReadOutcome:
        """value: tuple[VenuePosition, ...] (both sides of each symbol in hedge mode)."""
        ...

    def open_orders(self, symbol: str | None = None) -> ReadOutcome:
        """value: tuple[VenueOrder, ...] covering classic AND algo open orders; a partial read is UNKNOWN, not OK."""
        ...

    def fills(self, symbol: str, exchange_order_id: str) -> ReadOutcome:
        """value: tuple[VenueFill, ...] of one exchange order, oldest first."""
        ...
