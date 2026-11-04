"""REC-02 pure exchange-truth reconciliation planner.

The module has one deep interface: :func:`plan_reconciliation`.  It accepts a
sealed projection of durable local truth, an exhaustively paged venue snapshot,
and typed freshness configuration.  It returns an immutable plan and performs
no I/O, persistence, scheduling, or exchange action.
"""
from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, DecimalException
from typing import Any

from newcore.domain.account import Account, AccountBinding, BindingConfirmation, BindingState
from newcore.domain.base import canonical_decimal, dec_str
from newcore.domain.orders import IntentState, Purpose, Side
from newcore.domain.reasons import ReasonCode


_ID_RE = re.compile(r"(?:acct|int|rec)_[0-9a-f]{32}")
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9._:/-]{1,36}")
_SYMBOL_RE = re.compile(r"[A-Z0-9]{2,30}")
_TEXT_ID_RE = re.compile(r"[A-Za-z0-9._:/-]{1,64}")
ZERO = Decimal("0")


class ReconciliationError(ValueError):
    """Stable invalid-input error; never includes raw venue payloads."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ReconciliationError(code)


def _exact(value: Any, typ: type, code: str) -> Any:
    if type(value) is not typ:
        _fail(code)
    return value


def _integer(value: Any, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > 2**63 - 1:
        _fail(code)
    return value


def _decimal(value: Any, code: str, *, positive: bool = False) -> Decimal:
    try:
        canonical = canonical_decimal(value, code)
    except Exception:
        _fail(code)
    if canonical <= ZERO if positive else canonical < ZERO:
        _fail(code)
    return canonical


def _identifier(value: Any, prefix: str, code: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None or not value.startswith(prefix + "_"):
        _fail(code)
    return value


def _client_id(value: Any, code: str) -> str:
    if type(value) is not str or _CLIENT_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _symbol(value: Any, code: str) -> str:
    if type(value) is not str or _SYMBOL_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _text_id(value: Any, code: str) -> str:
    if type(value) is not str or _TEXT_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


class SnapshotComponent(enum.StrEnum):
    POSITIONS = "positions"
    OPEN_ORDERS = "open_orders"
    USER_TRADES = "user_trades"


class VenueOrderKind(enum.StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"


class VenueOrderState(enum.StrEnum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"


class TradeEffect(enum.StrEnum):
    ADD = "add"
    REDUCE = "reduce"


class PlanStatus(enum.StrEnum):
    FLAT_CONFIRMED = "flat_confirmed"
    PROTECTED_CONFIRMED = "protected_confirmed"
    HOLD = "hold"


class FindingCode(enum.StrEnum):
    BINDING_NOT_CONFIRMED = "binding_not_confirmed"
    BINDING_MISMATCH = "binding_mismatch"
    SNAPSHOT_COMPONENT_MISSING = "snapshot_component_missing"
    SNAPSHOT_MIXED_GENERATION = "snapshot_mixed_generation"
    SNAPSHOT_PAGE_GAP = "snapshot_page_gap"
    SNAPSHOT_NOT_COMPLETE = "snapshot_not_complete"
    SNAPSHOT_BACKWARD = "snapshot_backward"
    SNAPSHOT_STALE = "snapshot_stale"
    SNAPSHOT_FUTURE = "snapshot_future"
    SNAPSHOT_RECORD_KIND = "snapshot_record_kind"
    DUPLICATE_POSITION = "duplicate_position"
    DUPLICATE_ORDER = "duplicate_order"
    DUPLICATE_TRADE = "duplicate_trade"
    LOCAL_POSITION_ONLY = "local_position_only"
    VENUE_POSITION_ONLY = "venue_position_only"
    MANUAL_ADD = "manual_add"
    EXTERNAL_REDUCTION = "external_reduction"
    CONTRADICTORY_POSITION = "contradictory_position"
    LOCAL_ORDER_ONLY = "local_order_only"
    FOREIGN_ORDER = "foreign_order"
    ORDER_ID_MISMATCH = "order_id_mismatch"
    ORDER_QUANTITY_MISMATCH = "order_quantity_mismatch"
    ORDER_SEMANTICS_MISMATCH = "order_semantics_mismatch"
    OPENING_ORDER_PENDING = "opening_order_pending"
    ORPHAN_ORDER = "orphan_order"
    PARTIAL_FILL = "partial_fill"
    LATE_FILL = "late_fill"
    TRADE_STALE = "trade_stale"
    TRADE_FUTURE = "trade_future"
    TRADE_PURPOSE_MISMATCH = "trade_purpose_mismatch"
    TRADE_DELTA_MISMATCH = "trade_delta_mismatch"
    FILL_EXCEEDS_REQUESTED = "fill_exceeds_requested"
    PROTECTION_MISSING = "protection_missing"
    PROTECTION_UNDERSIZED = "protection_undersized"
    STOP_FILLED_OFFLINE = "stop_filled_offline"


class ActionKind(enum.StrEnum):
    QUERY_ORDER = "query_order"
    ADOPT_EXTERNAL_REDUCTION = "adopt_external_reduction"
    ADOPT_LATE_FILL = "adopt_late_fill"
    QUARANTINE = "quarantine"
    PROTECT_REQUIRED = "protect_required"
    FLATTEN_REQUIRED = "flatten_required"
    OWNER_REVIEW = "owner_review"


@dataclass(frozen=True, slots=True)
class LocalPosition:
    symbol: str
    side: Side
    qty: Decimal

    def __post_init__(self) -> None:
        _symbol(self.symbol, "validation.local_position.symbol")
        _exact(self.side, Side, "validation.local_position.side")
        _decimal(self.qty, "validation.local_position.qty", positive=True)


@dataclass(frozen=True, slots=True)
class LocalOrder:
    intent_id: str
    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    side: Side
    purpose: Purpose
    state: IntentState
    requested_qty: Decimal
    executed_qty: Decimal

    def __post_init__(self) -> None:
        _identifier(self.intent_id, "int", "validation.local_order.intent_id")
        _client_id(self.client_order_id, "validation.local_order.client_order_id")
        if self.exchange_order_id is not None:
            _text_id(self.exchange_order_id, "validation.local_order.exchange_order_id")
        _symbol(self.symbol, "validation.local_order.symbol")
        _exact(self.side, Side, "validation.local_order.side")
        _exact(self.purpose, Purpose, "validation.local_order.purpose")
        _exact(self.state, IntentState, "validation.local_order.state")
        _decimal(self.requested_qty, "validation.local_order.requested_qty", positive=True)
        _decimal(self.executed_qty, "validation.local_order.executed_qty")
        if self.executed_qty > self.requested_qty:
            _fail("validation.local_order.executed_qty")


@dataclass(frozen=True, slots=True)
class DurableState:
    account: Account
    portfolio_generation: int
    last_venue_observed_at_ms: int
    positions: tuple[LocalPosition, ...]
    orders: tuple[LocalOrder, ...]

    def __post_init__(self) -> None:
        _exact(self.account, Account, "validation.local.account")
        _integer(self.portfolio_generation, "validation.local.portfolio_generation")
        _integer(self.last_venue_observed_at_ms, "validation.local.last_observed")
        _exact(self.positions, tuple, "validation.local.positions")
        _exact(self.orders, tuple, "validation.local.orders")
        if any(type(x) is not LocalPosition for x in self.positions):
            _fail("validation.local.positions")
        if any(type(x) is not LocalOrder for x in self.orders):
            _fail("validation.local.orders")
        position_keys = [(x.symbol, x.side) for x in self.positions]
        if len(position_keys) != len(set(position_keys)):
            _fail("validation.local.positions_duplicate")
        intent_ids = [x.intent_id for x in self.orders]
        client_ids = [x.client_order_id for x in self.orders]
        if len(intent_ids) != len(set(intent_ids)) or len(client_ids) != len(set(client_ids)):
            _fail("validation.local.orders_duplicate")


@dataclass(frozen=True, slots=True)
class VenuePosition:
    symbol: str
    side: Side
    qty: Decimal

    def __post_init__(self) -> None:
        _symbol(self.symbol, "validation.venue_position.symbol")
        _exact(self.side, Side, "validation.venue_position.side")
        _decimal(self.qty, "validation.venue_position.qty", positive=True)


@dataclass(frozen=True, slots=True)
class VenueOrder:
    exchange_order_id: str
    client_order_id: str
    symbol: str
    side: Side
    kind: VenueOrderKind
    state: VenueOrderState
    reduce_only: bool
    requested_qty: Decimal
    executed_qty: Decimal

    def __post_init__(self) -> None:
        _text_id(self.exchange_order_id, "validation.venue_order.exchange_order_id")
        _client_id(self.client_order_id, "validation.venue_order.client_order_id")
        _symbol(self.symbol, "validation.venue_order.symbol")
        _exact(self.side, Side, "validation.venue_order.side")
        _exact(self.kind, VenueOrderKind, "validation.venue_order.kind")
        _exact(self.state, VenueOrderState, "validation.venue_order.state")
        _exact(self.reduce_only, bool, "validation.venue_order.reduce_only")
        _decimal(self.requested_qty, "validation.venue_order.requested_qty", positive=True)
        _decimal(self.executed_qty, "validation.venue_order.executed_qty")
        if self.executed_qty >= self.requested_qty:
            _fail("validation.venue_order.executed_qty")

    @property
    def remaining_qty(self) -> Decimal:
        return self.requested_qty - self.executed_qty


@dataclass(frozen=True, slots=True)
class VenueTrade:
    trade_id: str
    client_order_id: str | None
    symbol: str
    side: Side
    effect: TradeEffect
    qty: Decimal
    price: Decimal
    at_ms: int

    def __post_init__(self) -> None:
        _text_id(self.trade_id, "validation.venue_trade.trade_id")
        if self.client_order_id is not None:
            _client_id(self.client_order_id, "validation.venue_trade.client_order_id")
        _symbol(self.symbol, "validation.venue_trade.symbol")
        _exact(self.side, Side, "validation.venue_trade.side")
        _exact(self.effect, TradeEffect, "validation.venue_trade.effect")
        _decimal(self.qty, "validation.venue_trade.qty", positive=True)
        _decimal(self.price, "validation.venue_trade.price", positive=True)
        _integer(self.at_ms, "validation.venue_trade.at_ms", minimum=1)


SnapshotRecord = VenuePosition | VenueOrder | VenueTrade


@dataclass(frozen=True, slots=True)
class EvidencePage:
    component: SnapshotComponent
    account_id: str
    binding: AccountBinding
    snapshot_generation: int
    page_number: int
    observed_at_ms: int
    complete: bool
    records: tuple[SnapshotRecord, ...]

    def __post_init__(self) -> None:
        _exact(self.component, SnapshotComponent, "validation.page.component")
        _identifier(self.account_id, "acct", "validation.page.account_id")
        _exact(self.binding, AccountBinding, "validation.page.binding")
        _integer(self.snapshot_generation, "validation.page.snapshot_generation", minimum=1)
        _integer(self.page_number, "validation.page.page_number", minimum=1)
        _integer(self.observed_at_ms, "validation.page.observed_at_ms", minimum=1)
        _exact(self.complete, bool, "validation.page.complete")
        _exact(self.records, tuple, "validation.page.records")
        if any(type(x) not in (VenuePosition, VenueOrder, VenueTrade) for x in self.records):
            _fail("validation.page.records")


@dataclass(frozen=True, slots=True)
class VenueSnapshot:
    pages: tuple[EvidencePage, ...]

    def __post_init__(self) -> None:
        _exact(self.pages, tuple, "validation.snapshot.pages")
        if any(type(x) is not EvidencePage for x in self.pages):
            _fail("validation.snapshot.pages")


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    now_ms: int
    max_snapshot_age_s: int

    def __post_init__(self) -> None:
        _integer(self.now_ms, "validation.config.now_ms", minimum=1)
        _integer(self.max_snapshot_age_s, "validation.config.max_snapshot_age_s", minimum=1)


@dataclass(frozen=True, slots=True)
class Finding:
    code: FindingCode
    reason: ReasonCode
    symbol: str | None = None
    side: Side | None = None
    subject_id: str | None = None

    def __post_init__(self) -> None:
        _exact(self.code, FindingCode, "validation.finding.code")
        _exact(self.reason, ReasonCode, "validation.finding.reason")
        if self.symbol is not None:
            _symbol(self.symbol, "validation.finding.symbol")
        if self.side is not None:
            _exact(self.side, Side, "validation.finding.side")
        if (self.symbol is None) != (self.side is None):
            _fail("validation.finding.instrument")
        if self.subject_id is not None and type(self.subject_id) is not str:
            _fail("validation.finding.subject_id")


@dataclass(frozen=True, slots=True)
class PlannedAction:
    kind: ActionKind
    reason: ReasonCode
    symbol: str | None = None
    side: Side | None = None
    qty: Decimal | None = None
    subject_id: str | None = None

    def __post_init__(self) -> None:
        _exact(self.kind, ActionKind, "validation.action.kind")
        _exact(self.reason, ReasonCode, "validation.action.reason")
        if self.symbol is not None:
            _symbol(self.symbol, "validation.action.symbol")
        if self.side is not None:
            _exact(self.side, Side, "validation.action.side")
        if (self.symbol is None) != (self.side is None):
            _fail("validation.action.instrument")
        if self.qty is not None:
            _decimal(self.qty, "validation.action.qty", positive=True)
        if self.kind in (ActionKind.PROTECT_REQUIRED, ActionKind.FLATTEN_REQUIRED):
            if self.symbol is None or self.qty is None:
                _fail("validation.action.corroborated_position")
        if self.subject_id is not None and type(self.subject_id) is not str:
            _fail("validation.action.subject_id")


@dataclass(frozen=True, slots=True)
class ReconciliationPlan:
    reconciliation_id: str
    account_id: str
    portfolio_generation: int
    snapshot_generation: int | None
    evidence_sha256: str
    status: PlanStatus
    reason: ReasonCode
    findings: tuple[Finding, ...]
    actions: tuple[PlannedAction, ...]

    def __post_init__(self) -> None:
        _identifier(self.reconciliation_id, "rec", "validation.plan.reconciliation_id")
        _identifier(self.account_id, "acct", "validation.plan.account_id")
        _integer(self.portfolio_generation, "validation.plan.portfolio_generation")
        if self.snapshot_generation is not None:
            _integer(self.snapshot_generation, "validation.plan.snapshot_generation", minimum=1)
        if type(self.evidence_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256) is None:
            _fail("validation.plan.evidence_sha256")
        _exact(self.status, PlanStatus, "validation.plan.status")
        _exact(self.reason, ReasonCode, "validation.plan.reason")
        _exact(self.findings, tuple, "validation.plan.findings")
        _exact(self.actions, tuple, "validation.plan.actions")
        if any(type(x) is not Finding for x in self.findings):
            _fail("validation.plan.findings")
        if any(type(x) is not PlannedAction for x in self.actions):
            _fail("validation.plan.actions")
        if self.status is not PlanStatus.HOLD and (self.findings or self.actions):
            _fail("validation.plan.closed")


_REASON = {
    FindingCode.BINDING_NOT_CONFIRMED: ReasonCode.BINDING_UNCONFIRMED,
    FindingCode.BINDING_MISMATCH: ReasonCode.BINDING_MISMATCH,
    FindingCode.SNAPSHOT_BACKWARD: ReasonCode.RECONCILE_STALE_READ,
    FindingCode.SNAPSHOT_STALE: ReasonCode.RECONCILE_STALE_READ,
    FindingCode.SNAPSHOT_FUTURE: ReasonCode.RECONCILE_STALE_READ,
    FindingCode.VENUE_POSITION_ONLY: ReasonCode.RECONCILE_FOREIGN_QUARANTINE,
    FindingCode.MANUAL_ADD: ReasonCode.RECONCILE_MANUAL_ADD,
    FindingCode.EXTERNAL_REDUCTION: ReasonCode.RECONCILE_MANUAL_CLOSE,
    FindingCode.FOREIGN_ORDER: ReasonCode.OWNERSHIP_FOREIGN_ORDER,
    FindingCode.PARTIAL_FILL: ReasonCode.EVIDENCE_PARTIAL_FILL,
    FindingCode.LATE_FILL: ReasonCode.RECONCILE_LATE_FILL,
    FindingCode.TRADE_STALE: ReasonCode.RECONCILE_STALE_READ,
    FindingCode.TRADE_FUTURE: ReasonCode.RECONCILE_STALE_READ,
    FindingCode.TRADE_PURPOSE_MISMATCH: ReasonCode.RECONCILE_UNRECONCILED,
    FindingCode.TRADE_DELTA_MISMATCH: ReasonCode.RECONCILE_UNRECONCILED,
    FindingCode.FILL_EXCEEDS_REQUESTED: ReasonCode.RECONCILE_UNRECONCILED,
    FindingCode.PROTECTION_MISSING: ReasonCode.PROTECT_PLACE,
    FindingCode.PROTECTION_UNDERSIZED: ReasonCode.PROTECT_UNDERSIZED,
    FindingCode.STOP_FILLED_OFFLINE: ReasonCode.RECONCILE_LATE_FILL,
}


def _reason(code: FindingCode) -> ReasonCode:
    return _REASON.get(code, ReasonCode.RECONCILE_UNRECONCILED)


def _seal_binding(value: AccountBinding) -> AccountBinding:
    _exact(value, AccountBinding, "validation.binding")
    return AccountBinding(venue=value.venue, environment=value.environment, settlement_asset=value.settlement_asset,
                          key_digest=value.key_digest, exchange_uid=value.exchange_uid)


def _seal_account(value: Account) -> Account:
    _exact(value, Account, "validation.account")
    confirmation = value.confirmation
    if confirmation is not None:
        _exact(confirmation, BindingConfirmation, "validation.account.confirmation")
        confirmation = BindingConfirmation(account_id=confirmation.account_id,
                                           old_key_digest=confirmation.old_key_digest,
                                           new_key_digest=confirmation.new_key_digest,
                                           typed_phrase=confirmation.typed_phrase,
                                           confirmed_at_ms=confirmation.confirmed_at_ms)
    proposed = None if value.proposed_binding is None else _seal_binding(value.proposed_binding)
    return Account(account_id=value.account_id, label=value.label, hedge_mode=value.hedge_mode,
                   binding=_seal_binding(value.binding), binding_state=value.binding_state,
                   proposed_binding=proposed, confirmation=confirmation)


def _seal_local_position(value: LocalPosition) -> LocalPosition:
    _exact(value, LocalPosition, "validation.local_position")
    return LocalPosition(value.symbol, value.side, value.qty)


def _seal_local_order(value: LocalOrder) -> LocalOrder:
    _exact(value, LocalOrder, "validation.local_order")
    return LocalOrder(value.intent_id, value.client_order_id, value.exchange_order_id, value.symbol, value.side,
                      value.purpose, value.state, value.requested_qty, value.executed_qty)


def _seal_state(value: DurableState) -> DurableState:
    _exact(value, DurableState, "validation.local")
    positions = tuple(sorted((_seal_local_position(x) for x in value.positions),
                             key=lambda x: (x.symbol, x.side.value)))
    orders = tuple(sorted((_seal_local_order(x) for x in value.orders), key=lambda x: x.intent_id))
    return DurableState(_seal_account(value.account), value.portfolio_generation, value.last_venue_observed_at_ms,
                        positions, orders)


def _seal_record(value: SnapshotRecord) -> SnapshotRecord:
    if type(value) is VenuePosition:
        return VenuePosition(value.symbol, value.side, value.qty)
    if type(value) is VenueOrder:
        return VenueOrder(value.exchange_order_id, value.client_order_id, value.symbol, value.side, value.kind,
                          value.state, value.reduce_only, value.requested_qty, value.executed_qty)
    if type(value) is VenueTrade:
        return VenueTrade(value.trade_id, value.client_order_id, value.symbol, value.side, value.effect, value.qty,
                          value.price, value.at_ms)
    _fail("validation.page.record")


def _seal_page(value: EvidencePage) -> EvidencePage:
    _exact(value, EvidencePage, "validation.page")
    records = tuple(_seal_record(x) for x in value.records)
    if value.component is SnapshotComponent.POSITIONS:
        records = tuple(sorted(records, key=lambda x: (x.symbol, x.side.value)))
    elif value.component is SnapshotComponent.OPEN_ORDERS:
        records = tuple(sorted(records, key=lambda x: (x.client_order_id, x.exchange_order_id)))
    return EvidencePage(value.component, value.account_id, _seal_binding(value.binding), value.snapshot_generation,
                        value.page_number, value.observed_at_ms, value.complete,
                        records)


def _seal_snapshot(value: VenueSnapshot) -> VenueSnapshot:
    _exact(value, VenueSnapshot, "validation.snapshot")
    pages = tuple(sorted((_seal_page(x) for x in value.pages), key=lambda x: (x.component.value, x.page_number)))
    return VenueSnapshot(pages)


def _seal_config(value: PlannerConfig) -> PlannerConfig:
    _exact(value, PlannerConfig, "validation.config")
    return PlannerConfig(value.now_ms, value.max_snapshot_age_s)


def _finding(code: FindingCode, symbol: str | None = None, side: Side | None = None,
             subject_id: str | None = None) -> Finding:
    return Finding(code, _reason(code), symbol, side, subject_id)


def _action(kind: ActionKind, reason: ReasonCode, symbol: str | None = None, side: Side | None = None,
            qty: Decimal | None = None, subject_id: str | None = None) -> PlannedAction:
    return PlannedAction(kind, reason, symbol, side, qty, subject_id)


def _canonical(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if type(value) is Decimal:
        return dec_str(value)
    if is_dataclass(value):
        return {f.name: _canonical(getattr(value, f.name)) for f in fields(value)}
    if type(value) is tuple:
        return [_canonical(x) for x in value]
    if value is None or type(value) in (str, int, bool):
        return value
    _fail("validation.canonical")


def _sort_findings(items: list[Finding]) -> tuple[Finding, ...]:
    unique = {(x.code, x.symbol, x.side, x.subject_id): x for x in items}
    return tuple(unique[k] for k in sorted(unique, key=lambda k: (k[0].value, k[1] or "", (k[2].value if k[2] else ""),
                                                                  k[3] or "")))


def _sort_actions(items: list[PlannedAction]) -> tuple[PlannedAction, ...]:
    unique = {(x.kind, x.symbol, x.side, x.qty, x.subject_id): x for x in items}
    return tuple(unique[k] for k in sorted(unique, key=lambda k: (k[0].value, k[1] or "", (k[2].value if k[2] else ""),
                                                                  format(k[3], "f") if k[3] is not None else "",
                                                                  k[4] or "")))


def _make_plan(state: DurableState, generation: int | None, evidence_sha256: str,
               status: PlanStatus, reason: ReasonCode,
               findings: list[Finding] | tuple[Finding, ...] = (),
               actions: list[PlannedAction] | tuple[PlannedAction, ...] = ()) -> ReconciliationPlan:
    fs = _sort_findings(list(findings))
    acts = _sort_actions(list(actions))
    payload = {
        "account_id": state.account.account_id,
        "portfolio_generation": state.portfolio_generation,
        "snapshot_generation": generation,
        "evidence_sha256": evidence_sha256,
        "status": status.value,
        "reason": reason.value,
        "findings": _canonical(fs),
        "actions": _canonical(acts),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    rec_id = "rec_" + hashlib.sha256(raw).hexdigest()[:32]
    return ReconciliationPlan(rec_id, state.account.account_id, state.portfolio_generation, generation, evidence_sha256,
                              status, reason, fs, acts)


def _coherence(state: DurableState, snapshot: VenueSnapshot, config: PlannerConfig) -> tuple[list[Finding], int | None]:
    findings: list[Finding] = []
    if state.account.binding_state is not BindingState.CONFIRMED:
        findings.append(_finding(FindingCode.BINDING_NOT_CONFIRMED))
    by_component = {kind: [] for kind in SnapshotComponent}
    generations = set()
    for page in snapshot.pages:
        generations.add(page.snapshot_generation)
        if page.account_id != state.account.account_id or page.binding != state.account.binding:
            findings.append(_finding(FindingCode.BINDING_MISMATCH))
        by_component[page.component].append(page)
        expected = {SnapshotComponent.POSITIONS: VenuePosition,
                    SnapshotComponent.OPEN_ORDERS: VenueOrder,
                    SnapshotComponent.USER_TRADES: VenueTrade}[page.component]
        if any(type(x) is not expected for x in page.records):
            findings.append(_finding(FindingCode.SNAPSHOT_RECORD_KIND))
    generation = next(iter(generations)) if len(generations) == 1 else None
    if len(generations) != 1:
        findings.append(_finding(FindingCode.SNAPSHOT_MIXED_GENERATION))
    age_limit_ms = config.max_snapshot_age_s * 1000
    for kind, pages in by_component.items():
        pages.sort(key=lambda x: x.page_number)
        if not pages:
            findings.append(_finding(FindingCode.SNAPSHOT_COMPONENT_MISSING))
            continue
        if [x.page_number for x in pages] != list(range(1, len(pages) + 1)):
            findings.append(_finding(FindingCode.SNAPSHOT_PAGE_GAP))
        if not pages[-1].complete or any(x.complete for x in pages[:-1]):
            findings.append(_finding(FindingCode.SNAPSHOT_NOT_COMPLETE))
        observed = [x.observed_at_ms for x in pages]
        if observed != sorted(observed) or any(x < state.last_venue_observed_at_ms for x in observed):
            findings.append(_finding(FindingCode.SNAPSHOT_BACKWARD))
        if any(x > config.now_ms for x in observed):
            findings.append(_finding(FindingCode.SNAPSHOT_FUTURE))
        if any(config.now_ms - x > age_limit_ms for x in observed):
            findings.append(_finding(FindingCode.SNAPSHOT_STALE))
        if kind is SnapshotComponent.USER_TRADES:
            for page in pages:
                for trade in page.records:
                    if trade.at_ms <= state.last_venue_observed_at_ms:
                        findings.append(_finding(FindingCode.TRADE_STALE, trade.symbol, trade.side, trade.trade_id))
                    if trade.at_ms > page.observed_at_ms or trade.at_ms > config.now_ms:
                        findings.append(_finding(FindingCode.TRADE_FUTURE, trade.symbol, trade.side, trade.trade_id))
    return findings, generation


def _flatten(snapshot: VenueSnapshot) -> tuple[list[VenuePosition], list[VenueOrder], list[VenueTrade]]:
    positions: list[VenuePosition] = []
    orders: list[VenueOrder] = []
    trades: list[VenueTrade] = []
    for page in sorted(snapshot.pages, key=lambda x: (x.component.value, x.page_number)):
        if page.component is SnapshotComponent.POSITIONS:
            positions.extend(page.records)  # type: ignore[arg-type]
        elif page.component is SnapshotComponent.OPEN_ORDERS:
            orders.extend(page.records)  # type: ignore[arg-type]
        else:
            trades.extend(page.records)  # type: ignore[arg-type]
    return positions, orders, trades


def _duplicate_findings(positions: list[VenuePosition], orders: list[VenueOrder],
                        trades: list[VenueTrade]) -> list[Finding]:
    findings: list[Finding] = []
    position_keys = [(x.symbol, x.side) for x in positions]
    order_ids = [x.exchange_order_id for x in orders]
    order_clients = [x.client_order_id for x in orders]
    trade_ids = [x.trade_id for x in trades]
    if len(position_keys) != len(set(position_keys)):
        findings.append(_finding(FindingCode.DUPLICATE_POSITION))
    if len(order_ids) != len(set(order_ids)) or len(order_clients) != len(set(order_clients)):
        findings.append(_finding(FindingCode.DUPLICATE_ORDER))
    if len(trade_ids) != len(set(trade_ids)):
        findings.append(_finding(FindingCode.DUPLICATE_TRADE))
    ordered_trades = sorted(trades, key=lambda x: (x.at_ms, x.trade_id))
    if trades != ordered_trades:
        findings.append(_finding(FindingCode.SNAPSHOT_BACKWARD))
    return findings


def _expected_effect(purpose: Purpose) -> TradeEffect:
    return TradeEffect.ADD if purpose in (Purpose.ENTRY, Purpose.ADD) else TradeEffect.REDUCE


def _owned_trade_proof(order: LocalOrder, trades: list[VenueTrade]) -> tuple[Decimal, bool, bool]:
    """Return fresh delta, purpose compatibility, and requested-quantity bound."""
    owned = [trade for trade in trades if trade.client_order_id == order.client_order_id]
    compatible = all(trade.effect is _expected_effect(order.purpose)
                     and (trade.symbol, trade.side) == (order.symbol, order.side) for trade in owned)
    delta = sum((trade.qty for trade in owned), ZERO)
    bounded = order.executed_qty + delta <= order.requested_qty
    return delta, compatible, bounded


def plan_reconciliation(local: DurableState, snapshot: VenueSnapshot,
                        config: PlannerConfig) -> ReconciliationPlan:
    """Return a deterministic, immutable reconciliation plan.

    Incoherent evidence returns HOLD without deriving any position action.  A
    FLATTEN_REQUIRED action is emitted only for a corroborated residual
    position after an owned protective-stop fill.
    """
    state = _seal_state(local)
    venue = _seal_snapshot(snapshot)
    cfg = _seal_config(config)
    evidence_bytes = json.dumps(_canonical((state, venue, cfg)), sort_keys=True, separators=(",", ":"),
                                ensure_ascii=True).encode("ascii")
    evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
    coherence, generation = _coherence(state, venue, cfg)
    if coherence:
        reason = coherence[0].reason
        return _make_plan(state, generation, evidence_sha256, PlanStatus.HOLD, reason, coherence)

    positions, orders, trades = _flatten(venue)
    duplicate = _duplicate_findings(positions, orders, trades)
    if duplicate:
        return _make_plan(state, generation, evidence_sha256, PlanStatus.HOLD, duplicate[0].reason, duplicate)

    findings: list[Finding] = []
    actions: list[PlannedAction] = []
    local_positions = {(x.symbol, x.side): x for x in state.positions}
    venue_positions = {(x.symbol, x.side): x for x in positions}
    local_orders = {x.client_order_id: x for x in state.orders}
    venue_orders = {x.client_order_id: x for x in orders}

    try:
        reductions: dict[tuple[str, Side], Decimal] = {}
        for trade in trades:
            owned = local_orders.get(trade.client_order_id) if trade.client_order_id is not None else None
            if trade.effect is TradeEffect.REDUCE and (owned is None or _expected_effect(owned.purpose) is TradeEffect.REDUCE):
                key = (trade.symbol, trade.side)
                reductions[key] = reductions.get(key, ZERO) + trade.qty

        for key in sorted(set(local_positions) | set(venue_positions), key=lambda x: (x[0], x[1].value)):
            local_position = local_positions.get(key)
            venue_position = venue_positions.get(key)
            lqty = local_position.qty if local_position else ZERO
            vqty = venue_position.qty if venue_position else ZERO
            symbol, side = key
            if local_position is None:
                findings.append(_finding(FindingCode.VENUE_POSITION_ONLY, symbol, side))
                actions.append(_action(ActionKind.QUARANTINE, ReasonCode.RECONCILE_FOREIGN_QUARANTINE,
                                       symbol, side, vqty))
                actions.append(_action(ActionKind.OWNER_REVIEW, ReasonCode.RECONCILE_FOREIGN_QUARANTINE,
                                       symbol, side, vqty))
            elif venue_position is None:
                reduced = reductions.get(key, ZERO)
                if reduced == lqty:
                    code = FindingCode.STOP_FILLED_OFFLINE if any(
                        t.effect is TradeEffect.REDUCE and t.client_order_id in local_orders
                        and local_orders[t.client_order_id].purpose is Purpose.PROTECT
                        for t in trades if (t.symbol, t.side) == key
                    ) else FindingCode.EXTERNAL_REDUCTION
                    findings.append(_finding(code, symbol, side))
                    actions.append(_action(ActionKind.ADOPT_EXTERNAL_REDUCTION, _reason(code), symbol, side, lqty))
                else:
                    findings.append(_finding(FindingCode.LOCAL_POSITION_ONLY, symbol, side))
            elif vqty > lqty:
                findings.append(_finding(FindingCode.MANUAL_ADD, symbol, side))
                actions.append(_action(ActionKind.QUARANTINE, ReasonCode.RECONCILE_MANUAL_ADD, symbol, side,
                                       vqty - lqty))
            elif vqty < lqty:
                difference = lqty - vqty
                if reductions.get(key, ZERO) == difference:
                    findings.append(_finding(FindingCode.EXTERNAL_REDUCTION, symbol, side))
                    actions.append(_action(ActionKind.ADOPT_EXTERNAL_REDUCTION, ReasonCode.RECONCILE_MANUAL_CLOSE,
                                           symbol, side, difference))
                else:
                    findings.append(_finding(FindingCode.CONTRADICTORY_POSITION, symbol, side))

        for client_id in sorted(set(local_orders) | set(venue_orders)):
            local_order = local_orders.get(client_id)
            venue_order = venue_orders.get(client_id)
            if local_order is None:
                findings.append(_finding(FindingCode.FOREIGN_ORDER, venue_order.symbol, venue_order.side,
                                         venue_order.exchange_order_id))
                actions.append(_action(ActionKind.QUARANTINE, ReasonCode.RECONCILE_FOREIGN_QUARANTINE,
                                       venue_order.symbol, venue_order.side, venue_order.remaining_qty,
                                       venue_order.exchange_order_id))
                continue
            if venue_order is None:
                owned_trades = [t for t in trades if t.client_order_id == client_id]
                delta, compatible, bounded = _owned_trade_proof(local_order, trades)
                if owned_trades and not compatible:
                    findings.append(_finding(FindingCode.TRADE_PURPOSE_MISMATCH, local_order.symbol, local_order.side,
                                             local_order.intent_id))
                elif owned_trades and not bounded:
                    findings.append(_finding(FindingCode.FILL_EXCEEDS_REQUESTED, local_order.symbol, local_order.side,
                                             local_order.intent_id))
                elif delta > ZERO:
                    findings.append(_finding(FindingCode.LATE_FILL, local_order.symbol, local_order.side,
                                             local_order.intent_id))
                    actions.append(_action(ActionKind.ADOPT_LATE_FILL, ReasonCode.RECONCILE_LATE_FILL,
                                           local_order.symbol, local_order.side, delta, local_order.intent_id))
                elif local_order.state not in (IntentState.CANCELLED, IntentState.FILLED, IntentState.REJECTED,
                                                IntentState.NOT_SENT):
                    findings.append(_finding(FindingCode.LOCAL_ORDER_ONLY, local_order.symbol, local_order.side,
                                             local_order.intent_id))
                    actions.append(_action(ActionKind.QUERY_ORDER, ReasonCode.EVIDENCE_NOT_FOUND_UNCORROBORATED,
                                           local_order.symbol, local_order.side, subject_id=local_order.intent_id))
                continue
            if local_order.exchange_order_id is not None and local_order.exchange_order_id != venue_order.exchange_order_id:
                findings.append(_finding(FindingCode.ORDER_ID_MISMATCH, local_order.symbol, local_order.side,
                                         local_order.intent_id))
            if (local_order.symbol, local_order.side) != (venue_order.symbol, venue_order.side) or \
                    local_order.requested_qty != venue_order.requested_qty:
                findings.append(_finding(FindingCode.ORDER_QUANTITY_MISMATCH, local_order.symbol, local_order.side,
                                         local_order.intent_id))
            elif venue_order.executed_qty > local_order.executed_qty:
                delta, compatible, bounded = _owned_trade_proof(local_order, trades)
                expected_delta = venue_order.executed_qty - local_order.executed_qty
                if not compatible:
                    findings.append(_finding(FindingCode.TRADE_PURPOSE_MISMATCH, local_order.symbol, local_order.side,
                                             local_order.intent_id))
                elif not bounded:
                    findings.append(_finding(FindingCode.FILL_EXCEEDS_REQUESTED, local_order.symbol, local_order.side,
                                             local_order.intent_id))
                elif delta == expected_delta:
                    findings.append(_finding(FindingCode.PARTIAL_FILL, local_order.symbol, local_order.side,
                                             local_order.intent_id))
                    actions.append(_action(ActionKind.ADOPT_LATE_FILL, ReasonCode.RECONCILE_LATE_FILL,
                                           local_order.symbol, local_order.side, expected_delta, local_order.intent_id))
                else:
                    findings.append(_finding(FindingCode.TRADE_DELTA_MISMATCH, local_order.symbol, local_order.side,
                                             local_order.intent_id))
            elif venue_order.executed_qty < local_order.executed_qty:
                findings.append(_finding(FindingCode.TRADE_DELTA_MISMATCH, local_order.symbol, local_order.side,
                                         local_order.intent_id))
            venue_is_protection = venue_order.kind is VenueOrderKind.STOP_MARKET and venue_order.reduce_only
            if (local_order.purpose is Purpose.PROTECT) != venue_is_protection:
                findings.append(_finding(FindingCode.ORDER_SEMANTICS_MISMATCH, local_order.symbol, local_order.side,
                                         local_order.intent_id))
            key = (venue_order.symbol, venue_order.side)
            if local_order.purpose in (Purpose.ENTRY, Purpose.ADD):
                findings.append(_finding(FindingCode.OPENING_ORDER_PENDING, local_order.symbol, local_order.side,
                                         local_order.intent_id))
            elif key not in venue_positions:
                findings.append(_finding(FindingCode.ORPHAN_ORDER, local_order.symbol, local_order.side,
                                         local_order.intent_id))
                actions.append(_action(ActionKind.QUERY_ORDER, ReasonCode.RECONCILE_UNRECONCILED,
                                       local_order.symbol, local_order.side, subject_id=local_order.intent_id))

        for key, position in venue_positions.items():
            symbol, side = key
            coverage = ZERO
            for order in orders:
                owned = local_orders.get(order.client_order_id)
                if ((order.symbol, order.side) == key and order.kind is VenueOrderKind.STOP_MARKET and order.reduce_only
                        and owned is not None and owned.purpose is Purpose.PROTECT
                        and (owned.exchange_order_id is None or owned.exchange_order_id == order.exchange_order_id)):
                    coverage += order.remaining_qty
            protection_delta = ZERO
            for local_order in local_orders.values():
                if local_order.purpose is not Purpose.PROTECT or (local_order.symbol, local_order.side) != key:
                    continue
                delta, compatible, bounded = _owned_trade_proof(local_order, trades)
                if compatible and bounded:
                    protection_delta += delta
            local_position = local_positions.get(key)
            exact_stop_residual = (local_position is not None and protection_delta > ZERO
                                   and local_position.qty - protection_delta == position.qty)
            if coverage < position.qty:
                code = FindingCode.PROTECTION_MISSING if coverage == ZERO else FindingCode.PROTECTION_UNDERSIZED
                findings.append(_finding(code, symbol, side))
                if exact_stop_residual:
                    findings.append(_finding(FindingCode.STOP_FILLED_OFFLINE, symbol, side))
                    actions.append(_action(ActionKind.FLATTEN_REQUIRED, ReasonCode.RECONCILE_LATE_FILL,
                                           symbol, side, position.qty))
                elif key in local_positions:
                    actions.append(_action(ActionKind.PROTECT_REQUIRED, _reason(code), symbol, side,
                                           position.qty - coverage))
                else:
                    actions.append(_action(ActionKind.OWNER_REVIEW, ReasonCode.RECONCILE_FOREIGN_QUARANTINE,
                                           symbol, side, position.qty))
    except DecimalException:
        findings.append(_finding(FindingCode.CONTRADICTORY_POSITION))

    if findings or actions:
        reason = _sort_findings(findings)[0].reason if findings else ReasonCode.RECONCILE_UNRECONCILED
        return _make_plan(state, generation, evidence_sha256, PlanStatus.HOLD, reason, findings, actions)
    if not venue_positions and not venue_orders:
        return _make_plan(state, generation, evidence_sha256, PlanStatus.FLAT_CONFIRMED,
                          ReasonCode.RECONCILE_FLAT_SNAPSHOT)
    return _make_plan(state, generation, evidence_sha256, PlanStatus.PROTECTED_CONFIRMED,
                      ReasonCode.RECONCILE_MATCH)


__all__ = [
    "ActionKind", "DurableState", "EvidencePage", "Finding", "FindingCode", "LocalOrder", "LocalPosition",
    "PlannerConfig", "PlannedAction", "PlanStatus", "ReconciliationError", "ReconciliationPlan",
    "SnapshotComponent", "TradeEffect", "VenueOrder", "VenueOrderKind", "VenueOrderState", "VenuePosition",
    "VenueSnapshot", "VenueTrade", "plan_reconciliation",
]
