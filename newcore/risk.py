"""NC-06 pure deterministic risk-increase authorization gateway.

Only ENTRY and risk-increasing ADD requests enter this module.  Reductions,
closes and protection are explicitly outside the gate and therefore cannot be
blocked by it.  The implementation owns no clock, network, store, worker,
strategy, runtime, UI, or order-submission behavior.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext


class RiskAction(enum.StrEnum):
    ENTRY = 'entry'
    ADD = 'add'
    REDUCE = 'reduce'
    CLOSE = 'close'
    PROTECT = 'protect'


class PositionSide(enum.StrEnum):
    LONG = 'LONG'
    SHORT = 'SHORT'


class Environment(enum.StrEnum):
    PAPER = 'paper'
    TESTNET = 'testnet'
    MAINNET = 'mainnet'


class RefusalCode(enum.StrEnum):
    INPUT_INVALID = 'input_invalid'
    ACCOUNT_UNCONFIRMED = 'account_unconfirmed'
    ACCOUNT_MISMATCH = 'account_mismatch'
    ENVIRONMENT_MISMATCH = 'environment_mismatch'
    STALE_ACCOUNT = 'stale_account'
    STALE_PORTFOLIO = 'stale_portfolio'
    STALE_POLICY = 'stale_policy'
    STALE_FILTER = 'stale_filter'
    GENERATION_MISMATCH = 'generation_mismatch'
    FILTER_UNVERIFIED = 'filter_unverified'
    FILTER_CONTRADICTION = 'filter_contradiction'
    SYMBOL_MISMATCH = 'symbol_mismatch'
    DUPLICATE_REQUEST = 'duplicate_request'
    ALLOCATION_MISSING = 'allocation_missing'
    CORRELATION_MAPPING_MISSING = 'correlation_mapping_missing'
    CORRELATION_CAP_MISSING = 'correlation_cap_missing'
    HEDGE_MODE_REQUIRED = 'hedge_mode_required'
    ENTRY_TICK_INVALID = 'entry_tick_invalid'
    STOP_TICK_INVALID = 'stop_tick_invalid'
    STOP_DIRECTION_INVALID = 'stop_direction_invalid'
    CAPITAL_UNAVAILABLE = 'capital_unavailable'
    NO_CAPACITY = 'no_capacity'
    BELOW_MIN_QUANTITY = 'below_min_quantity'
    BELOW_MIN_NOTIONAL = 'below_min_notional'


class Ceiling(enum.StrEnum):
    REQUESTED_RISK = 'requested_risk'
    TRADE_RISK = 'trade_risk'
    PORTFOLIO_RISK = 'portfolio_risk'
    SYMBOL_SIDE_RISK = 'symbol_side_risk'
    ALLOCATION_RISK = 'allocation_risk'
    ALLOCATION_NOTIONAL = 'allocation_notional'
    ACCOUNT_GROSS = 'account_gross'
    ACCOUNT_NET = 'account_net'
    SYMBOL = 'symbol'
    SYMBOL_SIDE = 'symbol_side'
    CORRELATION = 'correlation'
    LEVERAGE = 'leverage'
    AVAILABLE_MARGIN = 'available_margin'
    VENUE_MAX_QUANTITY = 'venue_max_quantity'
    VENUE_MAX_NOTIONAL = 'venue_max_notional'
    VENUE_STEP = 'venue_step'
    VENUE_MIN_QUANTITY = 'venue_min_quantity'
    VENUE_MIN_NOTIONAL = 'venue_min_notional'


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountRiskSnapshot:
    account_id: str
    environment: Environment
    generation: int
    confirmed: bool
    fresh: bool
    hedge_mode: bool
    equity: Decimal
    available_margin: Decimal
    reserved_capital: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class Exposure:
    symbol: str
    side: PositionSide
    strategy_id: str
    book_id: str
    quantity: Decimal
    notional: Decimal
    risk: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class OpeningReservation:
    request_id: str
    authorization_id: str
    symbol: str
    side: PositionSide
    strategy_id: str
    book_id: str
    quantity: Decimal
    notional: Decimal
    risk: Decimal
    margin: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class PortfolioRiskSnapshot:
    account_id: str
    generation: int
    fresh: bool
    exposures: tuple[Exposure, ...]
    reservations: tuple[OpeningReservation, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Allocation:
    strategy_id: str
    book_id: str
    notional_fraction: Decimal
    risk_fraction: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class CorrelationAssignment:
    symbol: str
    bucket: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CorrelationCap:
    bucket: str
    exposure_fraction: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskPolicySnapshot:
    policy_id: str
    account_id: str
    environment: Environment
    generation: int
    fresh: bool
    capital_cap: Decimal
    max_trade_risk_fraction: Decimal
    max_portfolio_risk_fraction: Decimal
    max_symbol_side_risk_fraction: Decimal
    max_gross_multiple: Decimal
    max_net_multiple: Decimal
    max_symbol_fraction: Decimal
    max_symbol_side_fraction: Decimal
    max_leverage: Decimal
    allocations: tuple[Allocation, ...]
    correlation_map: tuple[CorrelationAssignment, ...]
    correlation_caps: tuple[CorrelationCap, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueRiskFilters:
    filter_id: str
    symbol: str
    generation: int
    fresh: bool
    verified: bool
    step_size: Decimal
    tick_size: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal
    max_notional: Decimal
    max_leverage: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskRequest:
    request_id: str
    account_id: str
    environment: Environment
    account_generation: int
    portfolio_generation: int
    policy_generation: int
    filter_generation: int
    action: RiskAction
    strategy_id: str
    book_id: str
    symbol: str
    side: PositionSide
    entry_price: Decimal
    stop_price: Decimal
    requested_risk: Decimal
    requested_leverage: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class FollowerProfile:
    profile_id: str
    generation: int
    fresh: bool
    capital: Decimal
    reserved_capital: Decimal
    available_margin: Decimal
    reserved_margin: Decimal
    gross_notional: Decimal
    net_notional: Decimal
    symbol_notional: Decimal
    symbol_side_notional: Decimal
    correlation_notional: Decimal
    allocation_notional: Decimal
    portfolio_risk: Decimal
    symbol_side_risk: Decimal
    allocation_risk: Decimal
    leverage_ceiling: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskEvaluation:
    account: AccountRiskSnapshot
    portfolio: PortfolioRiskSnapshot
    policy: RiskPolicySnapshot
    filters: VenueRiskFilters
    request: RiskRequest
    followers: tuple[FollowerProfile, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class FollowerDiagnostic:
    profile_id: str
    profile_generation: int
    capital: Decimal
    exact_quantity: Decimal
    exact_feasible: bool
    bindings: tuple[Ceiling, ...]
    reasons: tuple[RefusalCode, ...]


@dataclass(frozen=True, slots=True, init=False)
class ApprovedDecision:
    decision_id: str
    authorization_id: str
    request_id: str
    account_id: str
    account_generation: int
    portfolio_generation: int
    policy_id: str
    policy_generation: int
    filter_id: str
    filter_generation: int
    quantity: Decimal
    notional: Decimal
    risk: Decimal
    margin: Decimal
    leverage: Decimal
    controlled_capital: Decimal
    bindings: tuple[Ceiling, ...]
    followers: tuple[FollowerDiagnostic, ...]

    def __init__(self, *args, **kwargs):
        raise TypeError('ApprovedDecision is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('ApprovedDecision is final')


@dataclass(frozen=True, slots=True, init=False)
class RefusedDecision:
    decision_id: str
    request_id: str
    account_id: str
    account_generation: int
    portfolio_generation: int
    policy_id: str
    policy_generation: int
    filter_id: str
    filter_generation: int
    reasons: tuple[RefusalCode, ...]
    bindings: tuple[Ceiling, ...]

    def __init__(self, *args, **kwargs):
        raise TypeError('RefusedDecision is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('RefusedDecision is final')


RiskDecision = ApprovedDecision | RefusedDecision


def requires_authorization(action: RiskAction) -> bool:
    """Risk-reducing actions bypass NC-06 and can never be refused here."""
    try:
        value = RiskAction(action)
    except (TypeError, ValueError):
        return True
    return value in (RiskAction.ENTRY, RiskAction.ADD)


def _is_id(value: object, prefix: str | None = None) -> bool:
    return (type(value) is str and 1 <= len(value) <= 64
            and (prefix is None or value.startswith(prefix + '_')))


def _is_symbol(value: object) -> bool:
    return (type(value) is str and 2 <= len(value) <= 32 and value.isascii()
            and value.isalnum() and value.upper() == value)


def _is_generation(value: object) -> bool:
    return type(value) is int and 0 < value <= 2 ** 63 - 1


def _is_decimal(value: object, *, positive=False, nonnegative=False, signed=False) -> bool:
    if type(value) is not Decimal or not value.is_finite():
        return False
    if positive and value <= 0:
        return False
    if nonnegative and value < 0:
        return False
    return signed or value >= 0


def _fraction(value: object) -> bool:
    return _is_decimal(value, positive=True) and value <= 1


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return '0'
    return format(value.normalize(), 'f')


def _canonical(value: object) -> str:
    if type(value) is Decimal:
        return 'd:' + _canonical_decimal(value)
    if isinstance(value, enum.Enum):
        return 'e:' + str(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        parts = [f.name + '=' + _canonical(getattr(value, f.name)) for f in dataclasses.fields(value)]
        return type(value).__name__ + '(' + '|'.join(parts) + ')'
    if type(value) is tuple:
        return 't[' + '|'.join(_canonical(item) for item in value) + ']'
    if type(value) is str:
        return f's{len(value)}:{value}'
    if type(value) is bool:
        return 'b:1' if value else 'b:0'
    if type(value) is int:
        return 'i:' + str(value)
    if value is None:
        return 'n:'
    return 'invalid:' + type(value).__name__


def _digest(evaluation: object, tag: str) -> str:
    raw = ('nc06-v1|' + tag + '|' + _canonical(evaluation)).encode('utf-8')
    return 'dec_' + hashlib.sha256(raw).hexdigest()[:32]


def _master_digest(evaluation: object, tag: str) -> str:
    if type(evaluation) is RiskEvaluation:
        evaluation = dataclasses.replace(evaluation, followers=())
    return _digest(evaluation, tag)


def _construct(cls, **values):
    row = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(row, name, value)
    return row


def _context(evaluation: object) -> dict[str, object]:
    if type(evaluation) is not RiskEvaluation:
        return dict(request_id='', account_id='', account_generation=0,
                    portfolio_generation=0, policy_id='', policy_generation=0,
                    filter_id='', filter_generation=0)
    a, p, policy, f, r = (evaluation.account, evaluation.portfolio,
                           evaluation.policy, evaluation.filters, evaluation.request)
    return dict(
        request_id=r.request_id if type(r) is RiskRequest and _is_id(r.request_id, 'req') else '',
        account_id=a.account_id if type(a) is AccountRiskSnapshot and _is_id(a.account_id, 'acct') else '',
        account_generation=a.generation if type(a) is AccountRiskSnapshot and _is_generation(a.generation) else 0,
        portfolio_generation=p.generation if type(p) is PortfolioRiskSnapshot and _is_generation(p.generation) else 0,
        policy_id=policy.policy_id if type(policy) is RiskPolicySnapshot and _is_id(policy.policy_id) else '',
        policy_generation=policy.generation if type(policy) is RiskPolicySnapshot and _is_generation(policy.generation) else 0,
        filter_id=f.filter_id if type(f) is VenueRiskFilters and _is_id(f.filter_id) else '',
        filter_generation=f.generation if type(f) is VenueRiskFilters and _is_generation(f.generation) else 0,
    )


def _refuse(evaluation: object, reasons: tuple[RefusalCode, ...],
            bindings: tuple[Ceiling, ...] = ()) -> RefusedDecision:
    context = _context(evaluation)
    return _construct(
        RefusedDecision, decision_id=_master_digest(evaluation, 'refused:' + ','.join(reasons)),
        reasons=reasons, bindings=bindings, **context)


def _valid_exposure(row: object) -> bool:
    return (type(row) is Exposure and _is_symbol(row.symbol) and type(row.side) is PositionSide
            and _is_id(row.strategy_id) and _is_id(row.book_id)
            and _is_decimal(row.quantity, positive=True)
            and _is_decimal(row.notional, positive=True)
            and _is_decimal(row.risk, nonnegative=True))


def _valid_reservation(row: object) -> bool:
    return (type(row) is OpeningReservation and _is_id(row.request_id, 'req')
            and _is_id(row.authorization_id, 'dec') and _is_symbol(row.symbol)
            and type(row.side) is PositionSide and _is_id(row.strategy_id) and _is_id(row.book_id)
            and _is_decimal(row.quantity, positive=True)
            and _is_decimal(row.notional, positive=True)
            and _is_decimal(row.risk, nonnegative=True)
            and _is_decimal(row.margin, nonnegative=True))


def _shape_reasons(evaluation: object) -> tuple[RefusalCode, ...]:
    if type(evaluation) is not RiskEvaluation:
        return (RefusalCode.INPUT_INVALID,)
    a, p, policy, f, r, followers = (evaluation.account, evaluation.portfolio, evaluation.policy,
                                      evaluation.filters, evaluation.request, evaluation.followers)
    if (type(a) is not AccountRiskSnapshot or type(p) is not PortfolioRiskSnapshot
            or type(policy) is not RiskPolicySnapshot or type(f) is not VenueRiskFilters
            or type(r) is not RiskRequest or type(followers) is not tuple):
        return (RefusalCode.INPUT_INVALID,)
    scalar_ok = (
        _is_id(a.account_id, 'acct') and type(a.environment) is Environment and _is_generation(a.generation)
        and type(a.confirmed) is bool and type(a.fresh) is bool and type(a.hedge_mode) is bool
        and _is_decimal(a.equity, positive=True) and _is_decimal(a.available_margin, nonnegative=True)
        and _is_decimal(a.reserved_capital, nonnegative=True)
        and _is_id(p.account_id, 'acct') and _is_generation(p.generation) and type(p.fresh) is bool
        and type(p.exposures) is tuple and type(p.reservations) is tuple
        and all(_valid_exposure(x) for x in p.exposures)
        and all(_valid_reservation(x) for x in p.reservations)
        and len({x.request_id for x in p.reservations}) == len(p.reservations)
        and len({x.authorization_id for x in p.reservations}) == len(p.reservations)
        and _is_id(policy.policy_id) and _is_id(policy.account_id, 'acct')
        and type(policy.environment) is Environment and _is_generation(policy.generation) and type(policy.fresh) is bool
        and _is_decimal(policy.capital_cap, positive=True)
        and _fraction(policy.max_trade_risk_fraction) and _fraction(policy.max_portfolio_risk_fraction)
        and _fraction(policy.max_symbol_side_risk_fraction)
        and _is_decimal(policy.max_gross_multiple, positive=True)
        and _is_decimal(policy.max_net_multiple, positive=True)
        and _fraction(policy.max_symbol_fraction) and _fraction(policy.max_symbol_side_fraction)
        and _is_decimal(policy.max_leverage, positive=True)
        and type(policy.allocations) is tuple and type(policy.correlation_map) is tuple
        and type(policy.correlation_caps) is tuple
        and all(type(x) is Allocation and _is_id(x.strategy_id) and _is_id(x.book_id)
                and _fraction(x.notional_fraction) and _fraction(x.risk_fraction)
                for x in policy.allocations)
        and all(type(x) is CorrelationAssignment and _is_symbol(x.symbol) and _is_id(x.bucket)
                for x in policy.correlation_map)
        and all(type(x) is CorrelationCap and _is_id(x.bucket) and _fraction(x.exposure_fraction)
                for x in policy.correlation_caps)
        and len({(x.strategy_id, x.book_id) for x in policy.allocations}) == len(policy.allocations)
        and len({x.symbol for x in policy.correlation_map}) == len(policy.correlation_map)
        and len({x.bucket for x in policy.correlation_caps}) == len(policy.correlation_caps)
        and _is_id(f.filter_id) and _is_symbol(f.symbol) and _is_generation(f.generation)
        and type(f.fresh) is bool and type(f.verified) is bool
        and _is_decimal(f.step_size, positive=True) and _is_decimal(f.tick_size, positive=True)
        and _is_decimal(f.min_quantity, positive=True) and _is_decimal(f.max_quantity, positive=True)
        and _is_decimal(f.min_notional, positive=True) and _is_decimal(f.max_notional, positive=True)
        and _is_decimal(f.max_leverage, positive=True)
        and _is_id(r.request_id, 'req') and _is_id(r.account_id, 'acct') and type(r.environment) is Environment
        and _is_generation(r.account_generation) and _is_generation(r.portfolio_generation)
        and _is_generation(r.policy_generation)
        and _is_generation(r.filter_generation) and type(r.action) is RiskAction
        and r.action in (RiskAction.ENTRY, RiskAction.ADD)
        and _is_id(r.strategy_id) and _is_id(r.book_id) and _is_symbol(r.symbol)
        and type(r.side) is PositionSide and _is_decimal(r.entry_price, positive=True)
        and _is_decimal(r.stop_price, positive=True) and _is_decimal(r.requested_risk, positive=True)
        and _is_decimal(r.requested_leverage, positive=True)
    )
    if not scalar_ok:
        return (RefusalCode.INPUT_INVALID,)
    return ()


def _valid_follower(x: object, seen: set[str]) -> bool:
    return (type(x) is FollowerProfile and _is_id(x.profile_id) and x.profile_id not in seen
            and _is_generation(x.generation) and type(x.fresh) is bool
            and _is_decimal(x.capital, positive=True)
            and Decimal('1000') <= x.capital <= Decimal('4000')
            and _is_decimal(x.reserved_capital, nonnegative=True)
            and _is_decimal(x.available_margin, nonnegative=True)
            and _is_decimal(x.reserved_margin, nonnegative=True)
            and _is_decimal(x.gross_notional, nonnegative=True)
            and _is_decimal(x.net_notional, signed=True)
            and _is_decimal(x.symbol_notional, nonnegative=True)
            and _is_decimal(x.symbol_side_notional, nonnegative=True)
            and _is_decimal(x.correlation_notional, nonnegative=True)
            and _is_decimal(x.allocation_notional, nonnegative=True)
            and _is_decimal(x.portfolio_risk, nonnegative=True)
            and _is_decimal(x.symbol_side_risk, nonnegative=True)
            and _is_decimal(x.allocation_risk, nonnegative=True)
            and _is_decimal(x.leverage_ceiling, positive=True))


def _tick_valid(price: Decimal, tick: Decimal) -> bool:
    return price % tick == 0


def _sum(rows, field, predicate=lambda _row: True) -> Decimal:
    return sum((getattr(row, field) for row in rows if predicate(row)), Decimal('0'))


def _remaining(cap: Decimal, used: Decimal) -> Decimal:
    return max(Decimal('0'), cap - used)


def _qty_bound(notional: Decimal, price: Decimal) -> Decimal:
    return max(Decimal('0'), notional) / price


def _base_checks(evaluation: RiskEvaluation) -> tuple[tuple[RefusalCode, ...], Allocation | None,
                                                        str | None, CorrelationCap | None]:
    a, p, policy, f, r = (evaluation.account, evaluation.portfolio, evaluation.policy,
                           evaluation.filters, evaluation.request)
    reasons: list[RefusalCode] = []
    if not a.confirmed:
        reasons.append(RefusalCode.ACCOUNT_UNCONFIRMED)
    if not a.fresh:
        reasons.append(RefusalCode.STALE_ACCOUNT)
    if not p.fresh:
        reasons.append(RefusalCode.STALE_PORTFOLIO)
    if not policy.fresh:
        reasons.append(RefusalCode.STALE_POLICY)
    if not f.fresh:
        reasons.append(RefusalCode.STALE_FILTER)
    if not f.verified:
        reasons.append(RefusalCode.FILTER_UNVERIFIED)
    if len({a.account_id, p.account_id, policy.account_id, r.account_id}) != 1:
        reasons.append(RefusalCode.ACCOUNT_MISMATCH)
    if len({a.environment, policy.environment, r.environment}) != 1:
        reasons.append(RefusalCode.ENVIRONMENT_MISMATCH)
    if (r.account_generation != a.generation or r.portfolio_generation != p.generation
            or r.policy_generation != policy.generation
            or r.filter_generation != f.generation):
        reasons.append(RefusalCode.GENERATION_MISMATCH)
    if r.symbol != f.symbol:
        reasons.append(RefusalCode.SYMBOL_MISMATCH)
    if r.side is PositionSide.SHORT and not a.hedge_mode:
        reasons.append(RefusalCode.HEDGE_MODE_REQUIRED)
    if f.min_quantity > f.max_quantity or f.min_notional > f.max_notional:
        reasons.append(RefusalCode.FILTER_CONTRADICTION)
    if not _tick_valid(r.entry_price, f.tick_size):
        reasons.append(RefusalCode.ENTRY_TICK_INVALID)
    if not _tick_valid(r.stop_price, f.tick_size):
        reasons.append(RefusalCode.STOP_TICK_INVALID)
    if ((r.side is PositionSide.LONG and r.stop_price >= r.entry_price)
            or (r.side is PositionSide.SHORT and r.stop_price <= r.entry_price)):
        reasons.append(RefusalCode.STOP_DIRECTION_INVALID)
    if any(x.request_id == r.request_id for x in p.reservations):
        reasons.append(RefusalCode.DUPLICATE_REQUEST)
    allocations = [x for x in policy.allocations
                   if (x.strategy_id, x.book_id) == (r.strategy_id, r.book_id)]
    allocation = allocations[0] if len(allocations) == 1 else None
    if allocation is None:
        reasons.append(RefusalCode.ALLOCATION_MISSING)
    assignments = [x.bucket for x in policy.correlation_map if x.symbol == r.symbol]
    bucket = assignments[0] if len(assignments) == 1 else None
    if bucket is None:
        reasons.append(RefusalCode.CORRELATION_MAPPING_MISSING)
    caps = [x for x in policy.correlation_caps if x.bucket == bucket]
    cap = caps[0] if len(caps) == 1 else None
    if bucket is not None and cap is None:
        reasons.append(RefusalCode.CORRELATION_CAP_MISSING)
    for symbol in {x.symbol for x in p.exposures + p.reservations}:
        existing_assignments = [x.bucket for x in policy.correlation_map if x.symbol == symbol]
        if len(existing_assignments) != 1:
            reasons.append(RefusalCode.CORRELATION_MAPPING_MISSING)
        elif len([x for x in policy.correlation_caps if x.bucket == existing_assignments[0]]) != 1:
            reasons.append(RefusalCode.CORRELATION_CAP_MISSING)
    return tuple(dict.fromkeys(reasons)), allocation, bucket, cap


def _bounds(evaluation: RiskEvaluation, *, controlled: Decimal, allocation: Allocation,
            bucket: str, correlation_cap: CorrelationCap,
            aggregate: FollowerProfile | None = None) -> tuple[Decimal, list[tuple[Ceiling, Decimal]], Decimal]:
    a, p, policy, f, r = (evaluation.account, evaluation.portfolio, evaluation.policy,
                           evaluation.filters, evaluation.request)
    rows = p.exposures + p.reservations
    if aggregate is None:
        gross = _sum(rows, 'notional')
        long_notional = _sum(rows, 'notional', lambda x: x.side is PositionSide.LONG)
        short_notional = _sum(rows, 'notional', lambda x: x.side is PositionSide.SHORT)
        net = long_notional - short_notional
        symbol = _sum(rows, 'notional', lambda x: x.symbol == r.symbol)
        symbol_side = _sum(rows, 'notional', lambda x: x.symbol == r.symbol and x.side is r.side)
        allocation_notional = _sum(rows, 'notional', lambda x: (x.strategy_id, x.book_id)
                                   == (r.strategy_id, r.book_id))
        portfolio_risk = _sum(rows, 'risk')
        side_risk = _sum(rows, 'risk', lambda x: x.symbol == r.symbol and x.side is r.side)
        allocation_risk = _sum(rows, 'risk', lambda x: (x.strategy_id, x.book_id)
                               == (r.strategy_id, r.book_id))
        bucket_symbols = {x.symbol for x in policy.correlation_map if x.bucket == bucket}
        correlation = _sum(rows, 'notional', lambda x: x.symbol in bucket_symbols)
        reserved_margin = _sum(p.reservations, 'margin')
        available_margin = min(_remaining(a.available_margin, reserved_margin), controlled)
        leverage = min(r.requested_leverage, policy.max_leverage, f.max_leverage)
    else:
        gross, net = aggregate.gross_notional, aggregate.net_notional
        symbol, symbol_side = aggregate.symbol_notional, aggregate.symbol_side_notional
        allocation_notional, portfolio_risk = aggregate.allocation_notional, aggregate.portfolio_risk
        side_risk, allocation_risk = aggregate.symbol_side_risk, aggregate.allocation_risk
        correlation = aggregate.correlation_notional
        available_margin = min(_remaining(aggregate.available_margin, aggregate.reserved_margin), controlled)
        leverage = min(r.requested_leverage, policy.max_leverage, f.max_leverage,
                       aggregate.leverage_ceiling)
    distance = abs(r.entry_price - r.stop_price)
    direction_net_room = (controlled * policy.max_net_multiple - net
                          if r.side is PositionSide.LONG
                          else controlled * policy.max_net_multiple + net)
    qbounds = [
        (Ceiling.REQUESTED_RISK, r.requested_risk / distance),
        (Ceiling.TRADE_RISK, controlled * policy.max_trade_risk_fraction / distance),
        (Ceiling.PORTFOLIO_RISK,
         _remaining(controlled * policy.max_portfolio_risk_fraction, portfolio_risk) / distance),
        (Ceiling.SYMBOL_SIDE_RISK,
         _remaining(controlled * policy.max_symbol_side_risk_fraction, side_risk) / distance),
        (Ceiling.ALLOCATION_RISK,
         _remaining(controlled * allocation.risk_fraction, allocation_risk) / distance),
        (Ceiling.ALLOCATION_NOTIONAL,
         _qty_bound(_remaining(controlled * allocation.notional_fraction, allocation_notional), r.entry_price)),
        (Ceiling.ACCOUNT_GROSS,
         _qty_bound(_remaining(controlled * policy.max_gross_multiple, gross), r.entry_price)),
        (Ceiling.ACCOUNT_NET, _qty_bound(max(Decimal('0'), direction_net_room), r.entry_price)),
        (Ceiling.SYMBOL,
         _qty_bound(_remaining(controlled * policy.max_symbol_fraction, symbol), r.entry_price)),
        (Ceiling.SYMBOL_SIDE,
         _qty_bound(_remaining(controlled * policy.max_symbol_side_fraction, symbol_side), r.entry_price)),
        (Ceiling.CORRELATION,
         _qty_bound(_remaining(controlled * correlation_cap.exposure_fraction, correlation), r.entry_price)),
        (Ceiling.AVAILABLE_MARGIN, _qty_bound(available_margin * leverage, r.entry_price)),
        (Ceiling.VENUE_MAX_QUANTITY, f.max_quantity),
        (Ceiling.VENUE_MAX_NOTIONAL, f.max_notional / r.entry_price),
    ]
    return leverage, qbounds, distance


def _follower_diagnostics(evaluation: RiskEvaluation, quantity: Decimal, allocation: Allocation,
                          bucket: str, cap: CorrelationCap) -> tuple[FollowerDiagnostic, ...]:
    out: list[FollowerDiagnostic] = []
    f, policy, request = evaluation.filters, evaluation.policy, evaluation.request
    seen: set[str] = set()
    for index, profile in enumerate(evaluation.followers):
        if not _valid_follower(profile, seen):
            profile_id = (profile.profile_id if type(profile) is FollowerProfile
                          and type(profile.profile_id) is str else f'invalid-{index}')
            generation = (profile.generation if type(profile) is FollowerProfile
                          and type(profile.generation) is int else 0)
            capital = (profile.capital if type(profile) is FollowerProfile
                       and type(profile.capital) is Decimal and profile.capital.is_finite()
                       else Decimal('0'))
            out.append(FollowerDiagnostic(
                profile_id=profile_id, profile_generation=generation, capital=capital,
                exact_quantity=quantity, exact_feasible=False, bindings=(),
                reasons=(RefusalCode.INPUT_INVALID,)))
            continue
        seen.add(profile.profile_id)
        reasons: list[RefusalCode] = []
        bindings: list[Ceiling] = []
        if not profile.fresh:
            reasons.append(RefusalCode.STALE_ACCOUNT)
        controlled = min(profile.capital, policy.capital_cap) - profile.reserved_capital
        if controlled <= 0:
            reasons.append(RefusalCode.CAPITAL_UNAVAILABLE)
        else:
            _leverage, bounds, _distance = _bounds(
                evaluation, controlled=controlled, allocation=allocation,
                bucket=bucket, correlation_cap=cap, aggregate=profile)
            failed = [name for name, value in bounds if quantity > value]
            bindings.extend(failed)
            notional = quantity * request.entry_price
            if quantity < f.min_quantity:
                reasons.append(RefusalCode.BELOW_MIN_QUANTITY)
                bindings.append(Ceiling.VENUE_MIN_QUANTITY)
            if notional < f.min_notional:
                reasons.append(RefusalCode.BELOW_MIN_NOTIONAL)
                bindings.append(Ceiling.VENUE_MIN_NOTIONAL)
            if failed:
                reasons.append(RefusalCode.NO_CAPACITY)
        out.append(FollowerDiagnostic(
            profile_id=profile.profile_id, profile_generation=profile.generation,
            capital=profile.capital, exact_quantity=quantity,
            exact_feasible=not reasons, bindings=tuple(dict.fromkeys(bindings)),
            reasons=tuple(dict.fromkeys(reasons))))
    return tuple(out)


def _evaluate(evaluation: object) -> RiskDecision:
    shape = _shape_reasons(evaluation)
    if shape:
        return _refuse(evaluation, shape)
    assert type(evaluation) is RiskEvaluation
    reasons, allocation, bucket, correlation_cap = _base_checks(evaluation)
    if reasons:
        return _refuse(evaluation, reasons)
    assert allocation is not None and bucket is not None and correlation_cap is not None
    a, policy, f, r = evaluation.account, evaluation.policy, evaluation.filters, evaluation.request
    controlled = min(a.equity, policy.capital_cap) - a.reserved_capital
    if controlled <= 0:
        return _refuse(evaluation, (RefusalCode.CAPITAL_UNAVAILABLE,))
    leverage, bounds, distance = _bounds(
        evaluation, controlled=controlled, allocation=allocation,
        bucket=bucket, correlation_cap=correlation_cap)
    candidate = min(value for _name, value in bounds)
    bindings = [name for name, value in bounds if value == candidate]
    if leverage < r.requested_leverage:
        bindings.append(Ceiling.LEVERAGE)
    if candidate <= 0:
        return _refuse(evaluation, (RefusalCode.NO_CAPACITY,), tuple(bindings))
    steps = (candidate / f.step_size).to_integral_value(rounding=ROUND_FLOOR)
    quantity = steps * f.step_size
    if quantity < candidate:
        bindings.append(Ceiling.VENUE_STEP)
    if quantity <= 0 or quantity < f.min_quantity:
        return _refuse(evaluation, (RefusalCode.BELOW_MIN_QUANTITY,),
                       tuple(dict.fromkeys(bindings + [Ceiling.VENUE_MIN_QUANTITY])))
    notional = quantity * r.entry_price
    if notional < f.min_notional:
        return _refuse(evaluation, (RefusalCode.BELOW_MIN_NOTIONAL,),
                       tuple(dict.fromkeys(bindings + [Ceiling.VENUE_MIN_NOTIONAL])))
    risk = quantity * distance
    margin = notional / leverage
    followers = _follower_diagnostics(evaluation, quantity, allocation, bucket, correlation_cap)
    decision_id = _master_digest(evaluation, 'approved')
    context = _context(evaluation)
    return _construct(
        ApprovedDecision, decision_id=decision_id, authorization_id=decision_id,
        quantity=quantity, notional=notional, risk=risk, margin=margin,
        leverage=leverage, controlled_capital=controlled,
        bindings=tuple(dict.fromkeys(bindings)), followers=followers, **context)


def evaluate(evaluation: object) -> RiskDecision:
    """Return a deterministic closed approved/refused decision for one opening request."""
    with localcontext() as context:
        context.prec = 64
        context.rounding = ROUND_HALF_EVEN
        return _evaluate(evaluation)


def authorization_is_current(decision: object, evaluation: object,
                             consumed_authorizations: tuple[str, ...] = ()) -> bool:
    """Validate exact inputs/generations/output and enforce one-shot consumption."""
    if (type(decision) is not ApprovedDecision or type(evaluation) is not RiskEvaluation
            or type(consumed_authorizations) is not tuple
            or any(type(item) is not str for item in consumed_authorizations)
            or decision.authorization_id in consumed_authorizations):
        return False
    expected = evaluate(evaluation)
    if type(expected) is not ApprovedDecision:
        return False
    executable = ('decision_id', 'authorization_id', 'request_id', 'account_id',
                  'account_generation', 'portfolio_generation', 'policy_id',
                  'policy_generation', 'filter_id', 'filter_generation', 'quantity',
                  'notional', 'risk', 'margin', 'leverage', 'controlled_capital', 'bindings')
    return all(getattr(decision, name) == getattr(expected, name) for name in executable)


__all__ = [
    'AccountRiskSnapshot', 'Allocation', 'ApprovedDecision', 'Ceiling', 'CorrelationAssignment',
    'CorrelationCap', 'Exposure', 'FollowerDiagnostic', 'FollowerProfile', 'OpeningReservation',
    'Environment', 'PortfolioRiskSnapshot', 'PositionSide', 'RefusalCode', 'RefusedDecision', 'RiskAction',
    'RiskDecision', 'RiskEvaluation', 'RiskPolicySnapshot', 'RiskRequest', 'VenueRiskFilters',
    'authorization_is_current', 'evaluate', 'requires_authorization',
]
