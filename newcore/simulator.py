"""NC-08 pure deterministic simulator and replay primitives.

This module owns no files, network, clock, random source, runtime, PAPER, UI,
or strategy behavior.  Callers inject immutable action objects, dataset/model
versions, causal evidence, exact Decimal hooks, identities, and timestamps.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from dataclasses import dataclass
from decimal import (MAX_EMAX, MIN_EMIN, Context, Decimal, DecimalException,
                     ROUND_HALF_EVEN, localcontext)

from newcore.domain.codec import canonical_bytes as domain_canonical_bytes
from newcore.domain.codec import contract_sha256
from newcore.domain.orders import (IntentState, OrderIntent, OrderType, OwnerKind,
                                   Purpose, Side)
from newcore.domain.reasons import ReasonCode
from newcore.management import (CleanupAction, CloseAction, FlattenRequired,
                                ManagementPlan, PathOrder, PositionSide,
                                ProtectionRepair, ReduceAction,
                                RiskIncreaseProposal, StopChange)


class SimulationMode(enum.StrEnum):
    LIVE = 'live'
    REPLAY = 'replay'
    BACKTEST = 'backtest'


class ReplayStatus(enum.StrEnum):
    COMPLETE = 'complete'
    AMBIGUOUS = 'ambiguous'
    INCOMPLETE = 'incomplete'
    INVALID = 'invalid'


class TranslationStatus(enum.StrEnum):
    READY = 'ready'
    NO_INTENT = 'no_intent'
    AUTHORIZATION_REQUIRED = 'authorization_required'
    INVALID = 'invalid'


class EventKind(enum.StrEnum):
    MARKET = 'market'
    FILL = 'fill'
    FUNDING = 'funding'


class Liquidity(enum.StrEnum):
    MAKER = 'maker'
    TAKER = 'taker'


class ReplayReason(enum.StrEnum):
    OK = 'ok'
    PATH_AMBIGUOUS = 'path_ambiguous'
    FILL_INCOMPLETE = 'fill_incomplete'
    COST_COVERAGE_MISSING = 'cost_coverage_missing'
    FUNDING_COVERAGE_MISSING = 'funding_coverage_missing'
    AUTHORIZATION_REQUIRED = 'authorization_required'
    CLEANUP_ONLY = 'cleanup_only'
    INPUT_INVALID = 'input_invalid'


class SimulationInputError(ValueError):
    """Stable validation failure that never retains raw rejected input."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str):
    raise SimulationInputError(code)


_ARITHMETIC_CONTEXT = Context(prec=64, rounding=ROUND_HALF_EVEN,
                              Emin=MIN_EMIN, Emax=MAX_EMAX)


def _id(value: object, code: str):
    if type(value) is not str or not 1 <= len(value) <= 96:
        _fail(code)


def _family_id(value: object, code: str, prefix: str):
    stem = prefix + '_'
    if (type(value) is not str or not value.startswith(stem)
            or len(value) != len(stem) + 32
            or any(char not in '0123456789abcdef' for char in value[len(stem):])):
        _fail(code)


def _client_id(value: object, code: str):
    allowed = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-')
    if type(value) is not str or not 1 <= len(value) <= 36 or any(char not in allowed for char in value):
        _fail(code)


def _integer(value: object, code: str, *, minimum: int = 0):
    if type(value) is not int or value < minimum or value > 2 ** 63 - 1:
        _fail(code)


def _decimal(value: object, code: str, *, positive=False, nonnegative=False):
    if type(value) is not Decimal or not value.is_finite():
        _fail(code)
    if positive and value <= 0:
        _fail(code)
    if nonnegative and value < 0:
        _fail(code)


def _digest(value: object, code: str):
    if (type(value) is not str or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        _fail(code)


def _canonical_decimal(value: Decimal) -> str:
    sign, digits, exponent = value.as_tuple()
    if not value.is_finite():
        _fail('decimal.not_finite')
    if value == 0:
        return '0'
    digits = list(digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient = ''.join(str(digit) for digit in digits) or '0'
    return ('-' if sign else '') + coefficient + 'e' + str(exponent)


def _json_value(value: object):
    if type(value) is Decimal:
        return {'$decimal': _canonical_decimal(value)}
    if isinstance(value, enum.Enum):
        return {'$enum': type(value).__module__ + '.' + type(value).__qualname__,
                'value': value.value}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {'$type': type(value).__module__ + '.' + type(value).__qualname__,
                'fields': {field.name: _json_value(getattr(value, field.name))
                           for field in dataclasses.fields(value)}}
    if type(value) is tuple:
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    _fail('canonical.unsupported_type')


def canonical_bytes(value: object) -> bytes:
    """Canonical simulator bytes; NC-01 documents keep the NC-01 codec bytes."""
    if type(value) is OrderIntent:
        return domain_canonical_bytes(value)
    return json.dumps(_json_value(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def canonical_sha256(value: object) -> str:
    if type(value) is OrderIntent:
        return contract_sha256(value)
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class DatasetManifest:
    schema_version: int
    dataset_id: str
    dataset_version: str
    content_sha256: str
    source_class: str
    symbol: str
    timeframe_ms: int
    start_ms: int
    end_ms: int
    record_count: int
    ordering_version: str
    fill_model_version: str
    path_model_version: str
    cost_model_version: str
    funding_model_version: str
    instrument_rules_version: str
    translator_version: str

    def __post_init__(self):
        _integer(self.schema_version, 'manifest.schema_version', minimum=1)
        for name in ('dataset_id', 'dataset_version', 'source_class', 'symbol',
                     'ordering_version', 'fill_model_version', 'path_model_version',
                     'cost_model_version', 'funding_model_version',
                     'instrument_rules_version', 'translator_version'):
            _id(getattr(self, name), 'manifest.' + name)
        _digest(self.content_sha256, 'manifest.content_sha256')
        _integer(self.timeframe_ms, 'manifest.timeframe_ms', minimum=1)
        _integer(self.start_ms, 'manifest.start_ms', minimum=1)
        _integer(self.end_ms, 'manifest.end_ms', minimum=1)
        _integer(self.record_count, 'manifest.record_count', minimum=1)
        if self.end_ms < self.start_ms:
            _fail('manifest.coverage')


def dataset_matches(manifest: DatasetManifest, payload: object) -> bool:
    if type(manifest) is not DatasetManifest or type(payload) is not bytes:
        return False
    return hashlib.sha256(payload).hexdigest() == manifest.content_sha256


@dataclass(frozen=True, slots=True, kw_only=True)
class TranslationContext:
    account_id: str
    symbol: str
    side: Side
    owner_id: str
    owner_kind: OwnerKind
    intent_id: str
    decision_id: str
    client_order_id: str
    created_at_ms: int
    reason: ReasonCode
    position_quantity: Decimal
    cancel_intent_id: str | None
    instrument_rules_version: str
    input_generations: tuple[int, ...]
    nc06_authorization_id: str | None

    def __post_init__(self):
        if type(self.side) is not Side or type(self.owner_kind) is not OwnerKind:
            _fail('translation.identity_type')
        _family_id(self.account_id, 'translation.account_id', 'acct')
        _family_id(self.intent_id, 'translation.intent_id', 'int')
        _family_id(self.decision_id, 'translation.decision_id', 'dec')
        owner_family = {OwnerKind.LOT: 'lot', OwnerKind.ENTRY_INTENT: 'int',
                        OwnerKind.PORTFOLIO: 'pf'}[self.owner_kind]
        _family_id(self.owner_id, 'translation.owner_id', owner_family)
        _client_id(self.client_order_id, 'translation.client_order_id')
        _id(self.symbol, 'translation.symbol')
        if type(self.reason) is not ReasonCode:
            _fail('translation.reason')
        _integer(self.created_at_ms, 'translation.created_at_ms', minimum=1)
        _decimal(self.position_quantity, 'translation.position_quantity', positive=True)
        if self.cancel_intent_id is not None:
            _family_id(self.cancel_intent_id, 'translation.cancel_intent_id', 'int')
        _id(self.instrument_rules_version, 'translation.instrument_rules_version')
        if (type(self.input_generations) is not tuple or not self.input_generations
                or not all(type(row) is int and 0 < row <= 2 ** 63 - 1
                           for row in self.input_generations)):
            _fail('translation.input_generations')
        if self.nc06_authorization_id is not None:
            _family_id(self.nc06_authorization_id,
                       'translation.nc06_authorization_id', 'dec')


@dataclass(frozen=True, slots=True)
class TranslationOutcome:
    status: TranslationStatus
    reason: ReplayReason
    action_sha256: str
    context_sha256: str
    intent: OrderIntent | None
    cancel_after_working: bool
    cancel_intent_id: str | None


def _intent_for_action(action: object, context: TranslationContext) -> OrderIntent | None:
    purpose: Purpose
    order_type: OrderType
    quantity: Decimal
    stop_price: Decimal | None = None
    if type(action) is ProtectionRepair:
        purpose, order_type = Purpose.PROTECT, OrderType.STOP_MARKET
        quantity, stop_price = action.quantity, action.stop_price
    elif type(action) is StopChange:
        purpose, order_type = Purpose.PROTECT, OrderType.STOP_MARKET
        quantity, stop_price = context.position_quantity, action.stop_price
    elif type(action) in (CloseAction, FlattenRequired):
        purpose, order_type = Purpose.CLOSE, OrderType.MARKET
        quantity = action.quantity
    elif type(action) is ReduceAction:
        purpose, order_type = Purpose.REDUCE, OrderType.MARKET
        quantity = action.quantity
    else:
        return None
    return OrderIntent(
        intent_id=context.intent_id, account_id=context.account_id,
        decision_id=context.decision_id, client_order_id=context.client_order_id,
        purpose=purpose, order_type=order_type, state=IntentState.PLANNED,
        symbol=context.symbol, side=context.side, qty=quantity, reason=context.reason,
        created_at_ms=context.created_at_ms, owner_id=context.owner_id,
        owner_kind=context.owner_kind, slot_id=None, price=None,
        stop_price=stop_price, arm=None, alt_client_order_id=None, seen_qty=None,
        authorized_by=None, replaces_intent_id=None)


def translate_action(action: object, context: object,
                     mode: object) -> TranslationOutcome:
    """Pure mode-independent action translation.  No runtime or venue send occurs."""
    if type(mode) is not SimulationMode:
        return TranslationOutcome(TranslationStatus.INVALID, ReplayReason.INPUT_INVALID,
                                  '', '', None, False, None)
    try:
        action_sha = canonical_sha256(action)
        if type(action) is OrderIntent:
            return TranslationOutcome(TranslationStatus.READY, ReplayReason.OK,
                                      action_sha, '', action, False, None)
        if type(context) is not TranslationContext:
            raise SimulationInputError('translation.context')
        context_sha = canonical_sha256(context)
        if type(action) is RiskIncreaseProposal:
            return TranslationOutcome(TranslationStatus.AUTHORIZATION_REQUIRED,
                                      ReplayReason.AUTHORIZATION_REQUIRED,
                                      action_sha, context_sha, None, False, None)
        if type(action) is CleanupAction:
            return TranslationOutcome(TranslationStatus.NO_INTENT, ReplayReason.CLEANUP_ONLY,
                                      action_sha, context_sha, None, False,
                                      context.cancel_intent_id)
        intent = _intent_for_action(action, context)
        if intent is None:
            raise SimulationInputError('translation.action_type')
        replace = type(action) in (ProtectionRepair, StopChange)
        return TranslationOutcome(TranslationStatus.READY, ReplayReason.OK,
                                  action_sha, context_sha, intent, replace,
                                  context.cancel_intent_id if replace else None)
    except Exception:
        return TranslationOutcome(TranslationStatus.INVALID, ReplayReason.INPUT_INVALID,
                                  '', '', None, False, None)


def translate_plan(plan: object, contexts: object,
                   mode: object) -> tuple[TranslationOutcome, ...]:
    if (type(plan) is not ManagementPlan or type(contexts) is not tuple
            or len(contexts) != len(plan.actions)):
        return (TranslationOutcome(TranslationStatus.INVALID, ReplayReason.INPUT_INVALID,
                                   '', '', None, False, None),)
    return tuple(translate_action(action, context, mode)
                 for action, context in zip(plan.actions, contexts))


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplayEvent:
    sequence: int
    evidence_at_ms: int
    available_at_ms: int
    kind: EventKind
    price: Decimal | None

    def __post_init__(self):
        _integer(self.sequence, 'event.sequence', minimum=1)
        _integer(self.evidence_at_ms, 'event.evidence_at_ms', minimum=1)
        _integer(self.available_at_ms, 'event.available_at_ms', minimum=1)
        if self.available_at_ms < self.evidence_at_ms:
            _fail('event.availability')
        if type(self.kind) is not EventKind:
            _fail('event.kind')
        if self.price is not None:
            _decimal(self.price, 'event.price', positive=True)


def causal_prefix(events: object, cursor_ms: object) -> tuple[ReplayEvent, ...]:
    if type(events) is not tuple or not all(type(row) is ReplayEvent for row in events):
        _fail('events.type')
    _integer(cursor_ms, 'events.cursor_ms', minimum=1)
    previous_available = 0
    for index, row in enumerate(events, 1):
        if row.sequence != index:
            _fail('events.sequence')
        if row.available_at_ms < previous_available:
            _fail('events.availability_order')
        previous_available = row.available_at_ms
    return tuple(row for row in events if row.available_at_ms <= cursor_ms)


@dataclass(frozen=True, slots=True, kw_only=True)
class PathObservation:
    side: PositionSide
    high: Decimal
    low: Decimal
    close: Decimal
    stop_price: Decimal
    target_price: Decimal
    order: PathOrder

    def __post_init__(self):
        if type(self.side) is not PositionSide or type(self.order) is not PathOrder:
            _fail('path.type')
        for name in ('high', 'low', 'close', 'stop_price', 'target_price'):
            _decimal(getattr(self, name), 'path.' + name, positive=True)
        if self.low > self.high or not self.low <= self.close <= self.high:
            _fail('path.range')


@dataclass(frozen=True, slots=True)
class PathBranch:
    order: PathOrder
    events: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PathResolution:
    status: ReplayStatus
    branches: tuple[PathBranch, ...]


def resolve_paths(observation: PathObservation) -> PathResolution:
    if type(observation) is not PathObservation:
        _fail('path.observation')
    if observation.side is PositionSide.LONG:
        stop = observation.low <= observation.stop_price
        target = observation.high >= observation.target_price
    else:
        stop = observation.high >= observation.stop_price
        target = observation.low <= observation.target_price
    if stop and target:
        if observation.order in (PathOrder.NONE, PathOrder.UNORDERED):
            return PathResolution(ReplayStatus.AMBIGUOUS, (
                PathBranch(PathOrder.STOP_FIRST, ('stop', 'target')),
                PathBranch(PathOrder.TARGET_FIRST, ('target', 'stop')),
            ))
        events = ('stop', 'target') if observation.order is PathOrder.STOP_FIRST else ('target', 'stop')
        return PathResolution(ReplayStatus.COMPLETE,
                              (PathBranch(observation.order, events),))
    if stop:
        return PathResolution(ReplayStatus.COMPLETE,
                              (PathBranch(PathOrder.STOP_FIRST, ('stop',)),))
    if target:
        return PathResolution(ReplayStatus.COMPLETE,
                              (PathBranch(PathOrder.TARGET_FIRST, ('target',)),))
    return PathResolution(ReplayStatus.COMPLETE,
                          (PathBranch(PathOrder.NONE, ()),))


@dataclass(frozen=True, slots=True, kw_only=True)
class FillEvidence:
    sequence: int
    at_ms: int
    known: bool
    quantity: Decimal
    price: Decimal | None
    liquidity: Liquidity | None

    def __post_init__(self):
        _integer(self.sequence, 'fill.sequence', minimum=1)
        _integer(self.at_ms, 'fill.at_ms', minimum=1)
        if type(self.known) is not bool:
            _fail('fill.known')
        _decimal(self.quantity, 'fill.quantity', nonnegative=True)
        if self.known:
            if self.quantity <= 0 or self.price is None or type(self.liquidity) is not Liquidity:
                _fail('fill.known_shape')
            _decimal(self.price, 'fill.price', positive=True)
        elif self.quantity != 0 or self.price is not None or self.liquidity is not None:
            _fail('fill.unknown_shape')


@dataclass(frozen=True, slots=True)
class FillSummary:
    status: ReplayStatus
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    average_price: Decimal | None
    fills: tuple[FillEvidence, ...]


def summarize_fills(requested_quantity: object,
                    fills: object) -> FillSummary:
    _decimal(requested_quantity, 'fills.requested_quantity', positive=True)
    if type(fills) is not tuple or not all(type(row) is FillEvidence for row in fills):
        _fail('fills.type')
    if tuple(row.sequence for row in fills) != tuple(range(1, len(fills) + 1)):
        _fail('fills.sequence')
    if any(not row.known for row in fills):
        return FillSummary(ReplayStatus.INCOMPLETE, requested_quantity, Decimal(0),
                           requested_quantity, None, fills)
    with localcontext(_ARITHMETIC_CONTEXT):
        try:
            filled = sum((row.quantity for row in fills), Decimal(0))
            if filled > requested_quantity:
                _fail('fills.overfill')
            if filled == 0:
                return FillSummary(ReplayStatus.INCOMPLETE, requested_quantity, Decimal(0),
                                   requested_quantity, None, fills)
            average = sum((row.quantity * row.price for row in fills), Decimal(0)) / filled
            remaining = requested_quantity - filled
        except DecimalException:
            _fail('fills.arithmetic')
    status = ReplayStatus.COMPLETE if remaining == 0 else ReplayStatus.INCOMPLETE
    return FillSummary(status, requested_quantity, filled, remaining, average, fills)


@dataclass(frozen=True, slots=True, kw_only=True)
class CostModel:
    model_id: str
    version: str
    settlement_asset: str
    maker_rate: Decimal
    taker_rate: Decimal
    slippage_bps: Decimal

    def __post_init__(self):
        _id(self.model_id, 'cost.model_id')
        _id(self.version, 'cost.version')
        _id(self.settlement_asset, 'cost.settlement_asset')
        _decimal(self.maker_rate, 'cost.maker_rate', nonnegative=True)
        _decimal(self.taker_rate, 'cost.taker_rate', nonnegative=True)
        _decimal(self.slippage_bps, 'cost.slippage_bps', nonnegative=True)


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    model_id: str
    version: str
    settlement_asset: str
    fee: Decimal
    slippage: Decimal


def calculate_costs(fills: object, model: object) -> CostBreakdown:
    if (type(fills) is not tuple or not all(type(row) is FillEvidence for row in fills)
            or type(model) is not CostModel or any(not row.known for row in fills)):
        _fail('cost.coverage')
    fee = Decimal(0)
    slippage = Decimal(0)
    with localcontext(_ARITHMETIC_CONTEXT):
        try:
            for row in fills:
                notional = row.quantity * row.price
                rate = model.maker_rate if row.liquidity is Liquidity.MAKER else model.taker_rate
                fee += notional * rate
                slippage += notional * model.slippage_bps / Decimal(10_000)
        except DecimalException:
            _fail('cost.arithmetic')
    return CostBreakdown(model.model_id, model.version, model.settlement_asset,
                         fee, slippage)


@dataclass(frozen=True, slots=True, kw_only=True)
class FundingEvent:
    sequence: int
    at_ms: int
    rate: Decimal
    mark_price: Decimal

    def __post_init__(self):
        _integer(self.sequence, 'funding.sequence', minimum=1)
        _integer(self.at_ms, 'funding.at_ms', minimum=1)
        _decimal(self.rate, 'funding.rate')
        _decimal(self.mark_price, 'funding.mark_price', positive=True)


@dataclass(frozen=True, slots=True)
class FundingSummary:
    model_id: str
    model_version: str
    settlement_asset: str
    convention: str
    payments: tuple[Decimal, ...]
    total: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class FundingModel:
    model_id: str
    version: str
    settlement_asset: str
    convention: str

    def __post_init__(self):
        for name in ('model_id', 'version', 'settlement_asset', 'convention'):
            _id(getattr(self, name), 'funding.' + name)


def calculate_funding(side: object, quantity: object, events: object, *,
                      opened_at_ms: object, closed_at_ms: object,
                      model: object) -> FundingSummary:
    if type(side) is not Side:
        _fail('funding.side')
    _decimal(quantity, 'funding.quantity', positive=True)
    if type(events) is not tuple or not all(type(row) is FundingEvent for row in events):
        _fail('funding.events')
    _integer(opened_at_ms, 'funding.opened_at_ms', minimum=1)
    _integer(closed_at_ms, 'funding.closed_at_ms', minimum=1)
    if type(model) is not FundingModel:
        _fail('funding.model')
    if closed_at_ms < opened_at_ms:
        _fail('funding.window')
    if tuple(row.sequence for row in events) != tuple(range(1, len(events) + 1)):
        _fail('funding.sequence')
    sign = Decimal(-1) if side is Side.LONG else Decimal(1)
    with localcontext(_ARITHMETIC_CONTEXT):
        try:
            payments = tuple(sign * quantity * row.mark_price * row.rate for row in events
                             if opened_at_ms < row.at_ms <= closed_at_ms)
            total = sum(payments, Decimal(0))
        except DecimalException:
            _fail('funding.arithmetic')
    return FundingSummary(model.model_id, model.version, model.settlement_asset,
                          model.convention, payments, total)


@dataclass(frozen=True, slots=True, init=False)
class ReplayResult:
    run_id: str
    status: ReplayStatus
    reasons: tuple[ReplayReason, ...]
    manifest: DatasetManifest | None
    manifest_sha256: str
    action_sha256: str
    branches: tuple[PathBranch, ...]
    fills: FillSummary
    costs: CostBreakdown | None
    funding: FundingSummary | None
    gross_pnl: Decimal | None
    net_pnl: Decimal | None

    def __init__(self, *args, **kwargs):
        raise TypeError('ReplayResult is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('ReplayResult is final')


def _construct(cls, **values):
    row = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(row, name, value)
    return row


def build_result(*, manifest: object, action: object, path: object,
                 fills: object, costs: object, funding: object,
                 gross_pnl: object) -> ReplayResult:
    try:
        if (type(manifest) is not DatasetManifest or type(path) is not PathResolution
                or type(fills) is not FillSummary):
            _fail('result.input')
        _decimal(gross_pnl, 'result.gross_pnl')
        manifest_sha = canonical_sha256(manifest)
        action_sha = canonical_sha256(action)
        reasons: list[ReplayReason] = []
        status = ReplayStatus.COMPLETE
        net: Decimal | None = None
        gross: Decimal | None = gross_pnl
        if path.status is ReplayStatus.AMBIGUOUS:
            status = ReplayStatus.AMBIGUOUS
            reasons.append(ReplayReason.PATH_AMBIGUOUS)
            gross = None
        elif fills.status is not ReplayStatus.COMPLETE:
            status = ReplayStatus.INCOMPLETE
            reasons.append(ReplayReason.FILL_INCOMPLETE)
        if costs is None:
            if status is ReplayStatus.COMPLETE:
                status = ReplayStatus.INCOMPLETE
            reasons.append(ReplayReason.COST_COVERAGE_MISSING)
        elif type(costs) is not CostBreakdown:
            _fail('result.costs')
        if funding is None:
            if status is ReplayStatus.COMPLETE:
                status = ReplayStatus.INCOMPLETE
            reasons.append(ReplayReason.FUNDING_COVERAGE_MISSING)
        elif type(funding) is not FundingSummary:
            _fail('result.funding')
        if status is ReplayStatus.COMPLETE:
            with localcontext(_ARITHMETIC_CONTEXT):
                net = gross_pnl - costs.fee - costs.slippage + funding.total
        material = (manifest_sha, action_sha, status, tuple(reasons), path.branches,
                    fills, costs, funding, gross, net)
        run_id = 'sim_' + canonical_sha256(material)[:32]
        return _construct(ReplayResult, run_id=run_id, status=status,
                          reasons=tuple(reasons) or (ReplayReason.OK,),
                          manifest=manifest, manifest_sha256=manifest_sha,
                          action_sha256=action_sha,
                          branches=path.branches, fills=fills, costs=costs,
                          funding=funding, gross_pnl=gross, net_pnl=net)
    except Exception:
        empty = FillSummary(ReplayStatus.INCOMPLETE, Decimal(0), Decimal(0),
                            Decimal(0), None, ())
        return _construct(ReplayResult, run_id='sim_' + '0' * 32,
                          status=ReplayStatus.INVALID,
                          reasons=(ReplayReason.INPUT_INVALID,),
                          manifest=None, manifest_sha256='', action_sha256='', branches=(),
                          fills=empty, costs=None, funding=None,
                          gross_pnl=None, net_pnl=None)


__all__ = [
    'CostBreakdown', 'CostModel', 'DatasetManifest', 'EventKind', 'FillEvidence',
    'FillSummary', 'FundingEvent', 'FundingModel', 'FundingSummary', 'Liquidity', 'PathBranch',
    'PathObservation', 'PathResolution', 'ReplayEvent', 'ReplayReason',
    'ReplayResult', 'ReplayStatus', 'SimulationInputError', 'SimulationMode',
    'TranslationContext', 'TranslationOutcome', 'TranslationStatus',
    'build_result', 'calculate_costs', 'calculate_funding', 'canonical_bytes',
    'canonical_sha256', 'causal_prefix', 'dataset_matches', 'resolve_paths',
    'summarize_fills', 'translate_action', 'translate_plan',
]
