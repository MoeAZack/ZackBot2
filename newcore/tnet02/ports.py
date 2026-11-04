"""TNET-02 narrow injected ports the real adapter needs beyond NEWCORE's ``VenuePort`` and NC-04 ``AccountContext``.

Every port returns frozen, validated records or raises.  There is no network, filesystem, clock or sleep here; the
concrete implementations are injected by the CLI (real run) or by tests (hostile fakes).
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Protocol

from newcore.domain.base import canonical_decimal
from newcore.domain.orders import IntentState, Purpose, Side


_SYMBOL = re.compile(r"[A-Z0-9]{2,30}")
_INTENT = re.compile(r"int_[0-9a-f]{32}")
_CLIENT = re.compile(r"zbn1[oa]-[a-z2-7]{26}")
_ORDER_ID = re.compile(r"[1-9][0-9]{0,30}")
_LOCAL = re.compile(r"(?:boot|case)_[0-9a-f]{32}")
ZERO = Decimal("0")


class PortError(ValueError):
    """Stable code only; never carries venue text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise PortError(code) from None


def _dec(value: object, code: str, *, positive: bool = False) -> Decimal:
    try:
        out = canonical_decimal(value, code)
    except Exception:
        _fail(code)
    if out <= ZERO if positive else out < ZERO:
        _fail(code)
    return out


def _ms(value: object, code: str) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        _fail(code)
    return value


def _symbol(value: object, code: str) -> str:
    if type(value) is not str or _SYMBOL.fullmatch(value) is None:
        _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class SymbolFilters:
    """Binance order filters for one symbol (PRICE_FILTER, MARKET_LOT_SIZE, MIN_NOTIONAL)."""

    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal
    source_sha256: str

    def __post_init__(self) -> None:
        _symbol(self.symbol, "filters.symbol")
        for name in ("tick_size", "step_size", "min_quantity", "max_quantity", "min_notional"):
            _dec(getattr(self, name), f"filters.{name}", positive=True)
        if self.min_quantity > self.max_quantity:
            _fail("filters.quantity_range")
        if type(self.source_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None:
            _fail("filters.source_sha256")


@dataclass(frozen=True, slots=True)
class SymbolFacts:
    symbol: str
    mark_price: Decimal
    leverage: int

    def __post_init__(self) -> None:
        _symbol(self.symbol, "facts.symbol")
        _dec(self.mark_price, "facts.mark_price", positive=True)
        if type(self.leverage) is not int or not 1 <= self.leverage <= 125:
            _fail("facts.leverage")


@dataclass(frozen=True, slots=True)
class AccountFacts:
    """One fresh read of account-level facts for the case symbols."""

    observed_at_ms: int
    hedge_mode: bool
    settlement_asset: str
    equity: Decimal
    available_margin: Decimal
    symbols: tuple[SymbolFacts, ...]

    def __post_init__(self) -> None:
        _ms(self.observed_at_ms, "facts.observed_at_ms")
        if type(self.hedge_mode) is not bool:
            _fail("facts.hedge_mode")
        if type(self.settlement_asset) is not str or not self.settlement_asset.isalnum():
            _fail("facts.settlement_asset")
        _dec(self.equity, "facts.equity", positive=True)
        _dec(self.available_margin, "facts.available_margin")
        if type(self.symbols) is not tuple or any(type(x) is not SymbolFacts for x in self.symbols):
            _fail("facts.symbols")
        names = [x.symbol for x in self.symbols]
        if len(set(names)) != len(names):
            _fail("facts.symbols")

    def symbol(self, symbol: str) -> SymbolFacts:
        rows = [x for x in self.symbols if x.symbol == symbol]
        if len(rows) != 1:
            _fail("facts.symbol_missing")
        return rows[0]


class AccountPort(Protocol):
    def facts(self, symbols: tuple[str, ...]) -> AccountFacts:
        """Fresh mark price and leverage per symbol plus wallet facts; raises when anything is unknown."""
        ...


class FiltersPort(Protocol):
    def filters(self, symbol: str) -> SymbolFilters: ...


Clock = Callable[[], int]
"""Wall clock in integer UTC milliseconds."""

Sleeper = Callable[[int], None]
"""Blocks for the given milliseconds (real) or advances a fake clock (tests)."""


class RecordKind(enum.StrEnum):
    ORDER = "order"
    POSITION = "position"


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """Write-ahead record of one owned order state or one owned position quantity for a case."""

    kind: RecordKind
    sequence: int
    case_id: str
    boot_id: str
    symbol: str
    side: Side
    intent_id: str | None
    client_id: str | None
    route: str | None
    purpose: Purpose | None
    state: IntentState | None
    requested_qty: Decimal
    executed_qty: Decimal
    exchange_order_id: str | None
    stop_price: Decimal | None

    def __post_init__(self) -> None:
        if type(self.kind) is not RecordKind:
            _fail("journal.kind")
        if type(self.sequence) is not int or not 1 <= self.sequence <= 2**31:
            _fail("journal.sequence")
        for name in ("case_id", "boot_id"):
            value = getattr(self, name)
            if type(value) is not str or _LOCAL.fullmatch(value) is None:
                _fail(f"journal.{name}")
        _symbol(self.symbol, "journal.symbol")
        if type(self.side) is not Side:
            _fail("journal.side")
        _dec(self.executed_qty, "journal.executed_qty")
        if self.kind is RecordKind.POSITION:
            _dec(self.requested_qty, "journal.requested_qty")
            if any(getattr(self, name) is not None for name in (
                    "intent_id", "client_id", "route", "purpose", "state", "exchange_order_id", "stop_price")):
                _fail("journal.position_fields")
            if self.requested_qty != self.executed_qty:
                _fail("journal.position_qty")
            return
        _dec(self.requested_qty, "journal.requested_qty", positive=True)
        if type(self.intent_id) is not str or _INTENT.fullmatch(self.intent_id) is None:
            _fail("journal.intent_id")
        if type(self.client_id) is not str or _CLIENT.fullmatch(self.client_id) is None:
            _fail("journal.client_id")
        if self.route not in ("classic", "algo") or self.client_id[4] != ("o" if self.route == "classic" else "a"):
            _fail("journal.route")
        if type(self.purpose) is not Purpose or type(self.state) is not IntentState:
            _fail("journal.purpose_state")
        if self.executed_qty > self.requested_qty:
            _fail("journal.executed_qty")
        if self.exchange_order_id is not None and (type(self.exchange_order_id) is not str
                                                   or _ORDER_ID.fullmatch(self.exchange_order_id) is None):
            _fail("journal.exchange_order_id")
        if (self.stop_price is None) != (self.purpose is not Purpose.PROTECT):
            _fail("journal.stop_price")
        if self.stop_price is not None:
            _dec(self.stop_price, "journal.stop_price", positive=True)


class CaseJournal(Protocol):
    def append(self, record: JournalRecord) -> None:
        """Durably append or raise; a raise means the record is not durable."""
        ...

    def records(self) -> tuple[JournalRecord, ...]: ...

    def close(self) -> None: ...


class JournalOpener(Protocol):
    def open(self, case_id: str, boot_id: str, *, reopen: bool) -> CaseJournal:
        """``reopen=False`` creates a fresh case journal; ``reopen=True`` loads the existing one or raises."""
        ...


__all__ = ["AccountFacts", "AccountPort", "CaseJournal", "Clock", "FiltersPort", "JournalOpener", "JournalRecord",
           "PortError", "RecordKind", "Sleeper", "SymbolFacts", "SymbolFilters"]
