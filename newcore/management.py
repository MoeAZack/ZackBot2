"""NC-07 pure deterministic position-management transition planner.

The planner consumes frozen, generation-bound facts for one lot and returns a
closed immutable plan.  It owns no clock, market-data fetch, persistence,
order submission, fill inference, retry, runtime, strategy, or UI behavior.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_CEILING, ROUND_FLOOR, localcontext


class PositionSide(enum.StrEnum):
    LONG = 'LONG'
    SHORT = 'SHORT'


class ProtectionStatus(enum.StrEnum):
    WORKING = 'working'
    MISSING = 'missing'
    UNDERSIZED = 'undersized'
    UNKNOWN = 'unknown'


class PendingKind(enum.StrEnum):
    NONE = 'none'
    PROTECTION = 'protection'
    CLOSE = 'close'
    REDUCE = 'reduce'
    STOP_CHANGE = 'stop_change'
    ADD = 'add'
    UNKNOWN = 'unknown'


class PathOrder(enum.StrEnum):
    NONE = 'none'
    UNORDERED = 'unordered'
    STOP_FIRST = 'stop_first'
    TARGET_FIRST = 'target_first'


class IncreaseKind(enum.StrEnum):
    DCA = 'dca'
    PYRAMID = 'pyramid'


class PlanStatus(enum.StrEnum):
    ACTIONS = 'actions'
    HOLD = 'hold'
    RECONCILE = 'reconcile'
    AMBIGUOUS = 'ambiguous'


class PlanReason(enum.StrEnum):
    INPUT_INVALID = 'input_invalid'
    STALE_INPUT = 'stale_input'
    GENERATION_MISMATCH = 'generation_mismatch'
    LOT_FINISHED = 'lot_finished'
    CLEANUP_REQUIRED = 'cleanup_required'
    PROTECTION_MISSING = 'protection_missing'
    PROTECTION_UNDERSIZED = 'protection_undersized'
    PROTECTION_UNKNOWN = 'protection_unknown'
    FLATTEN_REQUIRED = 'flatten_required'
    PENDING_INTENT = 'pending_intent'
    FORCED_EXIT = 'forced_exit'
    STRATEGY_EXIT = 'strategy_exit'
    TARGET_REDUCE = 'target_reduce'
    BREAKEVEN = 'breakeven'
    TRAIL = 'trail'
    TIME_EXIT = 'time_exit'
    RISK_INCREASE_PROPOSAL = 'risk_increase_proposal'
    STOP_CONFIRMATION_REQUIRED = 'stop_confirmation_required'
    TARGET_CONFIRMATION_REQUIRED = 'target_confirmation_required'
    AMBIGUOUS_TOUCH = 'ambiguous_touch'
    NO_CHANGE = 'no_change'


class Binding(enum.StrEnum):
    PROTECTION = 'protection'
    PENDING_INTENT = 'pending_intent'
    FORCED_EXIT = 'forced_exit'
    STRATEGY_EXIT = 'strategy_exit'
    TARGET = 'target'
    RUNNER = 'runner'
    BREAKEVEN = 'breakeven'
    TRAIL = 'trail'
    TIME = 'time'
    ADD_COUNT = 'add_count'
    CUMULATIVE_ADD_RISK = 'cumulative_add_risk'
    NC06_AUTHORIZATION = 'nc06_authorization'


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetRule:
    level_id: str
    trigger_price: Decimal
    reduce_fraction: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskIncreaseRule:
    level_id: str
    kind: IncreaseKind
    trigger_price: Decimal
    requested_risk: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementPolicy:
    policy_id: str
    version: str
    generation: int
    fresh: bool
    tick_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    targets: tuple[TargetRule, ...]
    preserve_runner: bool
    runner_quantity: Decimal
    breakeven_trigger_r: Decimal | None
    breakeven_offset_ticks: Decimal
    trail_trigger_r: Decimal | None
    trail_distance_r: Decimal | None
    max_holding_bars: int | None
    max_add_count: int
    max_single_add_risk: Decimal
    max_cumulative_add_risk: Decimal
    risk_increases: tuple[RiskIncreaseRule, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class LotState:
    lot_id: str
    strategy_id: str
    generation: int
    fresh: bool
    side: PositionSide
    quantity: Decimal
    initial_quantity: Decimal
    entry_price: Decimal
    initial_stop: Decimal
    initial_risk_per_unit: Decimal
    completed_target_levels: tuple[str, ...]
    completed_risk_levels: tuple[str, ...]
    breakeven_applied: bool
    trail_applied: bool
    completed_bars_held: int
    add_count: int
    cumulative_add_risk: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketObservation:
    observation_id: str
    generation: int
    fresh: bool
    completed: bool
    high: Decimal
    low: Decimal
    mark: Decimal
    path_order: PathOrder
    forced_exit_confirmed: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectionState:
    protection_id: str
    generation: int
    fresh: bool
    status: ProtectionStatus
    confirmed_stop: Decimal | None
    confirmed_quantity: Decimal
    confirmed_target_levels: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyExitFact:
    version: str
    generation: int
    fresh: bool
    exit_requested: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingSummary:
    generation: int
    fresh: bool
    kind: PendingKind


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagementEvaluation:
    lot_generation: int
    policy_generation: int
    market_generation: int
    protection_generation: int
    strategy_exit_generation: int
    pending_generation: int
    lot: LotState
    policy: ManagementPolicy
    market: MarketObservation
    protection: ProtectionState
    strategy_exit: StrategyExitFact
    pending: PendingSummary


@dataclass(frozen=True, slots=True, init=False)
class ProtectionRepair:
    stop_price: Decimal
    quantity: Decimal
    place_before_cancel: bool

    def __init__(self, *args, **kwargs):
        raise TypeError('ProtectionRepair is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('ProtectionRepair is final')


@dataclass(frozen=True, slots=True, init=False)
class CloseAction:
    quantity: Decimal

    def __init__(self, *args, **kwargs):
        raise TypeError('CloseAction is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('CloseAction is final')


@dataclass(frozen=True, slots=True, init=False)
class ReduceAction:
    level_id: str
    target_price: Decimal
    quantity: Decimal
    remaining_quantity: Decimal

    def __init__(self, *args, **kwargs):
        raise TypeError('ReduceAction is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('ReduceAction is final')


@dataclass(frozen=True, slots=True, init=False)
class StopChange:
    stop_price: Decimal
    replaces_stop: Decimal
    place_before_cancel: bool

    def __init__(self, *args, **kwargs):
        raise TypeError('StopChange is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('StopChange is final')


@dataclass(frozen=True, slots=True, init=False)
class CleanupAction:
    cancel_protection: bool

    def __init__(self, *args, **kwargs):
        raise TypeError('CleanupAction is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('CleanupAction is final')


@dataclass(frozen=True, slots=True, init=False)
class FlattenRequired:
    quantity: Decimal

    def __init__(self, *args, **kwargs):
        raise TypeError('FlattenRequired is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('FlattenRequired is final')


@dataclass(frozen=True, slots=True, init=False)
class RiskIncreaseProposal:
    kind: IncreaseKind
    level_id: str
    requested_risk: Decimal
    policy_generation: int
    requires_nc06_authorization: bool

    def __init__(self, *args, **kwargs):
        raise TypeError('RiskIncreaseProposal is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('RiskIncreaseProposal is final')


ManagementAction = (ProtectionRepair | CloseAction | ReduceAction | StopChange
                    | CleanupAction | FlattenRequired | RiskIncreaseProposal)


@dataclass(frozen=True, slots=True, init=False)
class ManagementPlan:
    plan_id: str
    status: PlanStatus
    reason: PlanReason
    lot_id: str
    policy_id: str
    policy_version: str
    lot_generation: int
    policy_generation: int
    market_generation: int
    protection_generation: int
    strategy_exit_generation: int
    pending_generation: int
    bindings: tuple[Binding, ...]
    actions: tuple[ManagementAction, ...]

    def __init__(self, *args, **kwargs):
        raise TypeError('ManagementPlan is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('ManagementPlan is final')


def _construct(cls, **values):
    row = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(row, name, value)
    return row


def _is_id(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 64


def _is_generation(value: object) -> bool:
    return type(value) is int and 0 < value <= 2 ** 63 - 1


def _is_count(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2 ** 31 - 1


def _decimal(value: object, *, positive=False, nonnegative=False) -> bool:
    if type(value) is not Decimal or not value.is_finite():
        return False
    if positive and value <= 0:
        return False
    if nonnegative and value < 0:
        return False
    return True


def _fraction(value: object) -> bool:
    return _decimal(value, positive=True) and value <= 1


def _canonical_decimal(value: Decimal) -> str:
    sign, digits, exponent = value.as_tuple()
    if not value.is_finite():
        return ('-' if sign else '') + str(exponent) + ':' + ''.join(map(str, digits))
    if value == 0:
        return '0'
    digits = list(digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return ('-' if sign else '') + (''.join(map(str, digits)) or '0') + 'e' + str(exponent)


def _canonical(value: object) -> str:
    if type(value) is Decimal:
        return 'd:' + _canonical_decimal(value)
    if isinstance(value, enum.Enum):
        return 'e:' + str(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return type(value).__name__ + '(' + '|'.join(
            f.name + '=' + _canonical(getattr(value, f.name))
            for f in dataclasses.fields(value)) + ')'
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


def _context(evaluation: object) -> dict[str, object]:
    empty = dict(lot_id='', policy_id='', policy_version='', lot_generation=0,
                 policy_generation=0, market_generation=0, protection_generation=0,
                 strategy_exit_generation=0, pending_generation=0)
    if type(evaluation) is not ManagementEvaluation:
        return empty
    lot, policy = evaluation.lot, evaluation.policy
    return dict(
        lot_id=lot.lot_id if type(lot) is LotState and _is_id(lot.lot_id) else '',
        policy_id=policy.policy_id if type(policy) is ManagementPolicy and _is_id(policy.policy_id) else '',
        policy_version=(policy.version if type(policy) is ManagementPolicy
                        and _is_id(policy.version) else ''),
        lot_generation=evaluation.lot_generation if _is_generation(evaluation.lot_generation) else 0,
        policy_generation=(evaluation.policy_generation
                           if _is_generation(evaluation.policy_generation) else 0),
        market_generation=(evaluation.market_generation
                           if _is_generation(evaluation.market_generation) else 0),
        protection_generation=(evaluation.protection_generation
                               if _is_generation(evaluation.protection_generation) else 0),
        strategy_exit_generation=(evaluation.strategy_exit_generation
                                  if _is_generation(evaluation.strategy_exit_generation) else 0),
        pending_generation=(evaluation.pending_generation
                            if _is_generation(evaluation.pending_generation) else 0),
    )


def _plan(evaluation: object, status: PlanStatus, reason: PlanReason, *,
          bindings: tuple[Binding, ...] = (),
          actions: tuple[ManagementAction, ...] = ()) -> ManagementPlan:
    context = _context(evaluation)
    material = _canonical((evaluation, status, reason, bindings, actions))
    plan_id = 'mgt_' + hashlib.sha256(('nc07-v1|' + material).encode('utf-8')).hexdigest()[:32]
    return _construct(ManagementPlan, plan_id=plan_id, status=status, reason=reason,
                      bindings=bindings, actions=actions, **context)


def _valid_target(row: object) -> bool:
    return (type(row) is TargetRule and _is_id(row.level_id)
            and _decimal(row.trigger_price, positive=True)
            and _fraction(row.reduce_fraction))


def _valid_increase(row: object) -> bool:
    return (type(row) is RiskIncreaseRule and _is_id(row.level_id)
            and type(row.kind) is IncreaseKind
            and _decimal(row.trigger_price, positive=True)
            and _decimal(row.requested_risk, positive=True))


def _valid_shape(evaluation: object) -> bool:
    if type(evaluation) is not ManagementEvaluation:
        return False
    lot, policy, market, protection, strategy, pending = (
        evaluation.lot, evaluation.policy, evaluation.market, evaluation.protection,
        evaluation.strategy_exit, evaluation.pending)
    if (type(lot) is not LotState or type(policy) is not ManagementPolicy
            or type(market) is not MarketObservation or type(protection) is not ProtectionState
            or type(strategy) is not StrategyExitFact or type(pending) is not PendingSummary):
        return False
    expected = (evaluation.lot_generation, evaluation.policy_generation,
                evaluation.market_generation, evaluation.protection_generation,
                evaluation.strategy_exit_generation, evaluation.pending_generation)
    if not all(_is_generation(value) for value in expected):
        return False
    if not (
        _is_id(lot.lot_id) and _is_id(lot.strategy_id) and _is_generation(lot.generation)
        and type(lot.fresh) is bool and type(lot.side) is PositionSide
        and _decimal(lot.quantity, nonnegative=True)
        and _decimal(lot.initial_quantity, positive=True)
        and _decimal(lot.entry_price, positive=True) and _decimal(lot.initial_stop, positive=True)
        and _decimal(lot.initial_risk_per_unit, positive=True)
        and type(lot.completed_target_levels) is tuple
        and type(lot.completed_risk_levels) is tuple
        and all(_is_id(item) for item in lot.completed_target_levels + lot.completed_risk_levels)
        and len(set(lot.completed_target_levels)) == len(lot.completed_target_levels)
        and len(set(lot.completed_risk_levels)) == len(lot.completed_risk_levels)
        and type(lot.breakeven_applied) is bool and type(lot.trail_applied) is bool
        and _is_count(lot.completed_bars_held) and _is_count(lot.add_count)
        and _decimal(lot.cumulative_add_risk, nonnegative=True)
        and _is_id(policy.policy_id) and _is_id(policy.version)
        and _is_generation(policy.generation) and type(policy.fresh) is bool
        and _decimal(policy.tick_size, positive=True)
        and _decimal(policy.quantity_step, positive=True)
        and _decimal(policy.min_quantity, positive=True)
        and type(policy.targets) is tuple and all(_valid_target(row) for row in policy.targets)
        and len({row.level_id for row in policy.targets}) == len(policy.targets)
        and type(policy.preserve_runner) is bool
        and _decimal(policy.runner_quantity, nonnegative=True)
        and (policy.breakeven_trigger_r is None
             or _decimal(policy.breakeven_trigger_r, positive=True))
        and _decimal(policy.breakeven_offset_ticks, nonnegative=True)
        and (policy.trail_trigger_r is None or _decimal(policy.trail_trigger_r, positive=True))
        and (policy.trail_distance_r is None or _decimal(policy.trail_distance_r, positive=True))
        and ((policy.trail_trigger_r is None) == (policy.trail_distance_r is None))
        and (policy.max_holding_bars is None
             or (type(policy.max_holding_bars) is int and policy.max_holding_bars > 0))
        and _is_count(policy.max_add_count)
        and _decimal(policy.max_single_add_risk, nonnegative=True)
        and _decimal(policy.max_cumulative_add_risk, nonnegative=True)
        and type(policy.risk_increases) is tuple
        and all(_valid_increase(row) for row in policy.risk_increases)
        and len({row.level_id for row in policy.risk_increases}) == len(policy.risk_increases)
        and _is_id(market.observation_id) and _is_generation(market.generation)
        and type(market.fresh) is bool and type(market.completed) is bool
        and _decimal(market.high, positive=True) and _decimal(market.low, positive=True)
        and _decimal(market.mark, positive=True) and type(market.path_order) is PathOrder
        and type(market.forced_exit_confirmed) is bool
        and _is_id(protection.protection_id) and _is_generation(protection.generation)
        and type(protection.fresh) is bool and type(protection.status) is ProtectionStatus
        and (protection.confirmed_stop is None
             or _decimal(protection.confirmed_stop, positive=True))
        and _decimal(protection.confirmed_quantity, nonnegative=True)
        and type(protection.confirmed_target_levels) is tuple
        and all(_is_id(item) for item in protection.confirmed_target_levels)
        and len(set(protection.confirmed_target_levels)) == len(protection.confirmed_target_levels)
        and _is_id(strategy.version) and _is_generation(strategy.generation)
        and type(strategy.fresh) is bool and type(strategy.exit_requested) is bool
        and _is_generation(pending.generation) and type(pending.fresh) is bool
        and type(pending.kind) is PendingKind
    ):
        return False
    if policy.preserve_runner and policy.runner_quantity < policy.min_quantity:
        return False
    if lot.side is PositionSide.LONG and not lot.initial_stop < lot.entry_price:
        return False
    if lot.side is PositionSide.SHORT and not lot.initial_stop > lot.entry_price:
        return False
    if abs(lot.entry_price - lot.initial_stop) != lot.initial_risk_per_unit:
        return False
    if market.low > market.high:
        return False
    if protection.status in (ProtectionStatus.WORKING, ProtectionStatus.UNDERSIZED):
        if protection.confirmed_stop is None or protection.confirmed_quantity <= 0:
            return False
    elif protection.status is ProtectionStatus.MISSING:
        if protection.confirmed_stop is not None or protection.confirmed_quantity != 0:
            return False
    risks: dict[IncreaseKind, list[Decimal]] = {kind: [] for kind in IncreaseKind}
    for row in policy.risk_increases:
        if row.requested_risk > policy.max_single_add_risk:
            return False
        risks[row.kind].append(row.requested_risk)
    if any(any(later > earlier for earlier, later in zip(rows, rows[1:]))
           for rows in risks.values()):
        return False
    target_prices = [row.trigger_price for row in policy.targets]
    dca_prices = [row.trigger_price for row in policy.risk_increases
                  if row.kind is IncreaseKind.DCA]
    pyramid_prices = [row.trigger_price for row in policy.risk_increases
                      if row.kind is IncreaseKind.PYRAMID]
    if lot.side is PositionSide.LONG:
        if (any(price <= lot.entry_price for price in target_prices + pyramid_prices)
                or any(not lot.initial_stop < price < lot.entry_price for price in dca_prices)
                or any(later <= earlier for earlier, later in zip(target_prices, target_prices[1:]))
                or any(later >= earlier for earlier, later in zip(dca_prices, dca_prices[1:]))
                or any(later <= earlier for earlier, later in zip(pyramid_prices,
                                                                  pyramid_prices[1:]))):
            return False
    else:
        if (any(price >= lot.entry_price for price in target_prices + pyramid_prices)
                or any(not lot.entry_price < price < lot.initial_stop for price in dca_prices)
                or any(later >= earlier for earlier, later in zip(target_prices, target_prices[1:]))
                or any(later <= earlier for earlier, later in zip(dca_prices, dca_prices[1:]))
                or any(later >= earlier for earlier, later in zip(pyramid_prices,
                                                                  pyramid_prices[1:]))):
            return False
    target_ids = {row.level_id for row in policy.targets}
    risk_ids = {row.level_id for row in policy.risk_increases}
    if (not set(lot.completed_target_levels) <= target_ids
            or not set(lot.completed_risk_levels) <= risk_ids
            or not set(protection.confirmed_target_levels) <= target_ids
            or set(protection.confirmed_target_levels) & set(lot.completed_target_levels)
            or lot.add_count != len(lot.completed_risk_levels)
            or lot.add_count > policy.max_add_count
            or lot.cumulative_add_risk > policy.max_cumulative_add_risk
            or (lot.add_count == 0) != (lot.cumulative_add_risk == 0)):
        return False
    if protection.confirmed_stop is not None:
        if protection.confirmed_stop % policy.tick_size != 0:
            return False
        if lot.side is PositionSide.LONG and protection.confirmed_stop < lot.initial_stop:
            return False
        if lot.side is PositionSide.SHORT and protection.confirmed_stop > lot.initial_stop:
            return False
    if (protection.status is ProtectionStatus.UNDERSIZED
            and protection.confirmed_quantity >= lot.quantity and lot.quantity > 0):
        return False
    if (protection.status is ProtectionStatus.WORKING and lot.quantity > 0
            and protection.confirmed_quantity > lot.quantity):
        return False
    return True


def _generation_mismatch(evaluation: ManagementEvaluation) -> bool:
    return (evaluation.lot_generation != evaluation.lot.generation
            or evaluation.policy_generation != evaluation.policy.generation
            or evaluation.market_generation != evaluation.market.generation
            or evaluation.protection_generation != evaluation.protection.generation
            or evaluation.strategy_exit_generation != evaluation.strategy_exit.generation
            or evaluation.pending_generation != evaluation.pending.generation)


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _ceil(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def _stop_tick(value: Decimal, side: PositionSide, tick: Decimal) -> Decimal:
    return _floor(value, tick) if side is PositionSide.LONG else _ceil(value, tick)


def _stop_touched(lot: LotState, market: MarketObservation, stop: Decimal) -> bool:
    return market.low <= stop if lot.side is PositionSide.LONG else market.high >= stop


def _target_touched(lot: LotState, market: MarketObservation, target: Decimal) -> bool:
    return market.high >= target if lot.side is PositionSide.LONG else market.low <= target


def _improves(side: PositionSide, candidate: Decimal, current: Decimal) -> bool:
    return candidate > current if side is PositionSide.LONG else candidate < current


def _safe_before_mark(side: PositionSide, stop: Decimal, mark: Decimal) -> bool:
    return stop < mark if side is PositionSide.LONG else stop > mark


def _repair_action(lot: LotState, policy: ManagementPolicy,
                   protection: ProtectionState) -> ProtectionRepair:
    raw_stop = (protection.confirmed_stop if protection.confirmed_stop is not None
                else lot.initial_stop)
    return _construct(ProtectionRepair,
                      stop_price=_stop_tick(raw_stop, lot.side, policy.tick_size),
                      quantity=lot.quantity, place_before_cancel=True)


def _due_targets(evaluation: ManagementEvaluation) -> tuple[TargetRule, ...]:
    lot, policy, market = evaluation.lot, evaluation.policy, evaluation.market
    if not market.completed:
        return ()
    return tuple(row for row in policy.targets
                 if row.level_id not in lot.completed_target_levels
                 and _target_touched(lot, market, row.trigger_price))


def _target_actions(evaluation: ManagementEvaluation,
                    targets: tuple[TargetRule, ...]) -> tuple[ReduceAction, ...]:
    lot, policy = evaluation.lot, evaluation.policy
    runner = policy.runner_quantity if policy.preserve_runner else Decimal(0)
    remaining = lot.quantity
    actions: list[ReduceAction] = []
    for row in targets:
        available = max(Decimal(0), remaining - runner)
        desired = _floor(lot.initial_quantity * row.reduce_fraction, policy.quantity_step)
        quantity = min(desired, _floor(available, policy.quantity_step))
        if quantity <= 0:
            continue
        remaining -= quantity
        actions.append(_construct(ReduceAction, level_id=row.level_id,
                                  target_price=row.trigger_price, quantity=quantity,
                                  remaining_quantity=remaining))
    return tuple(actions)


def _stop_change(evaluation: ManagementEvaluation) -> tuple[StopChange | None, PlanReason | None]:
    lot, policy, market, protection = (evaluation.lot, evaluation.policy,
                                        evaluation.market, evaluation.protection)
    current = protection.confirmed_stop
    if current is None or not market.completed:
        return None, None
    favorable = (market.high - lot.entry_price if lot.side is PositionSide.LONG
                 else lot.entry_price - market.low)
    if (not lot.breakeven_applied and policy.breakeven_trigger_r is not None
            and favorable >= lot.initial_risk_per_unit * policy.breakeven_trigger_r):
        offset = policy.breakeven_offset_ticks * policy.tick_size
        raw = lot.entry_price + offset if lot.side is PositionSide.LONG else lot.entry_price - offset
        candidate = _stop_tick(raw, lot.side, policy.tick_size)
        if _improves(lot.side, candidate, current) and _safe_before_mark(lot.side, candidate, market.mark):
            return (_construct(StopChange, stop_price=candidate, replaces_stop=current,
                               place_before_cancel=True), PlanReason.BREAKEVEN)
    if (policy.trail_trigger_r is not None and policy.trail_distance_r is not None
            and favorable >= lot.initial_risk_per_unit * policy.trail_trigger_r):
        distance = policy.trail_distance_r * lot.initial_risk_per_unit
        raw = market.high - distance if lot.side is PositionSide.LONG else market.low + distance
        candidate = _stop_tick(raw, lot.side, policy.tick_size)
        if _improves(lot.side, candidate, current) and _safe_before_mark(lot.side, candidate, market.mark):
            return (_construct(StopChange, stop_price=candidate, replaces_stop=current,
                               place_before_cancel=True), PlanReason.TRAIL)
    return None, None


def _increase_triggered(lot: LotState, market: MarketObservation,
                        row: RiskIncreaseRule) -> bool:
    if row.kind is IncreaseKind.DCA:
        return (market.low <= row.trigger_price if lot.side is PositionSide.LONG
                else market.high >= row.trigger_price)
    return (market.high >= row.trigger_price if lot.side is PositionSide.LONG
            else market.low <= row.trigger_price)


def _risk_proposal(evaluation: ManagementEvaluation) -> RiskIncreaseProposal | None:
    lot, policy, market = evaluation.lot, evaluation.policy, evaluation.market
    if not market.completed or lot.add_count >= policy.max_add_count:
        return None
    for row in policy.risk_increases:
        if row.level_id in lot.completed_risk_levels or not _increase_triggered(lot, market, row):
            continue
        if lot.cumulative_add_risk + row.requested_risk > policy.max_cumulative_add_risk:
            continue
        return _construct(RiskIncreaseProposal, kind=row.kind, level_id=row.level_id,
                          requested_risk=row.requested_risk,
                          policy_generation=policy.generation,
                          requires_nc06_authorization=True)
    return None


def _evaluate(evaluation: object) -> ManagementPlan:
    if not _valid_shape(evaluation):
        return _plan(evaluation, PlanStatus.HOLD, PlanReason.INPUT_INVALID)
    assert type(evaluation) is ManagementEvaluation
    lot, policy, market, protection, strategy, pending = (
        evaluation.lot, evaluation.policy, evaluation.market, evaluation.protection,
        evaluation.strategy_exit, evaluation.pending)
    if _generation_mismatch(evaluation):
        return _plan(evaluation, PlanStatus.HOLD, PlanReason.GENERATION_MISMATCH)
    if not all((lot.fresh, policy.fresh, market.fresh, protection.fresh,
                strategy.fresh, pending.fresh)):
        return _plan(evaluation, PlanStatus.HOLD, PlanReason.STALE_INPUT)

    if lot.quantity == 0:
        if protection.status in (ProtectionStatus.WORKING, ProtectionStatus.UNDERSIZED):
            action = _construct(CleanupAction, cancel_protection=True)
            return _plan(evaluation, PlanStatus.ACTIONS, PlanReason.CLEANUP_REQUIRED,
                         bindings=(Binding.PROTECTION,), actions=(action,))
        if protection.status is ProtectionStatus.UNKNOWN or pending.kind is not PendingKind.NONE:
            return _plan(evaluation, PlanStatus.RECONCILE, PlanReason.LOT_FINISHED,
                         bindings=(Binding.PROTECTION,))
        return _plan(evaluation, PlanStatus.HOLD, PlanReason.LOT_FINISHED)

    effectively_undersized = (protection.status is ProtectionStatus.UNDERSIZED
                               or (protection.status is ProtectionStatus.WORKING
                                   and protection.confirmed_quantity < lot.quantity))
    if protection.status is ProtectionStatus.MISSING or effectively_undersized:
        reason = (PlanReason.PROTECTION_MISSING if protection.status is ProtectionStatus.MISSING
                  else PlanReason.PROTECTION_UNDERSIZED)
        if pending.kind is PendingKind.PROTECTION:
            return _plan(evaluation, PlanStatus.HOLD, PlanReason.PENDING_INTENT,
                         bindings=(Binding.PROTECTION, Binding.PENDING_INTENT))
        return _plan(evaluation, PlanStatus.ACTIONS, reason,
                     bindings=(Binding.PROTECTION,),
                     actions=(_repair_action(lot, policy, protection),))

    due_targets = _due_targets(evaluation)
    confirmed_stop = protection.confirmed_stop
    mark_beyond_confirmed = (confirmed_stop is not None
                             and (market.mark <= confirmed_stop
                                  if lot.side is PositionSide.LONG
                                  else market.mark >= confirmed_stop))
    stop_touched = (confirmed_stop is not None
                    and (mark_beyond_confirmed
                         or (market.completed and _stop_touched(lot, market, confirmed_stop))))
    target_touched = bool(due_targets)
    close_reason = (PlanReason.FORCED_EXIT if market.forced_exit_confirmed
                    else PlanReason.STRATEGY_EXIT if strategy.exit_requested else None)
    if close_reason is not None:
        if pending.kind is PendingKind.CLOSE:
            return _plan(evaluation, PlanStatus.HOLD, PlanReason.PENDING_INTENT,
                         bindings=(Binding.PENDING_INTENT,))
        bindings = [Binding.FORCED_EXIT if close_reason is PlanReason.FORCED_EXIT
                    else Binding.STRATEGY_EXIT]
        if pending.kind is not PendingKind.NONE:
            bindings.append(Binding.PENDING_INTENT)
        return _plan(evaluation, PlanStatus.ACTIONS, close_reason,
                     bindings=tuple(bindings),
                     actions=(_construct(CloseAction, quantity=lot.quantity),))

    if stop_touched and target_touched and market.path_order in (PathOrder.NONE, PathOrder.UNORDERED):
        return _plan(evaluation, PlanStatus.AMBIGUOUS, PlanReason.AMBIGUOUS_TOUCH,
                     bindings=(Binding.PROTECTION, Binding.TARGET))

    if any(row.level_id in protection.confirmed_target_levels for row in due_targets):
        return _plan(evaluation, PlanStatus.RECONCILE,
                     PlanReason.TARGET_CONFIRMATION_REQUIRED,
                     bindings=(Binding.TARGET,))

    mark_beyond_initial = (market.mark <= lot.initial_stop
                           if lot.side is PositionSide.LONG
                           else market.mark >= lot.initial_stop)
    beyond_initial_stop = (mark_beyond_initial
                           or (market.completed
                               and _stop_touched(lot, market, lot.initial_stop)))
    if protection.status is ProtectionStatus.UNKNOWN:
        if beyond_initial_stop:
            action = _construct(FlattenRequired, quantity=lot.quantity)
            return _plan(evaluation, PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED,
                         bindings=(Binding.PROTECTION,), actions=(action,))
        return _plan(evaluation, PlanStatus.RECONCILE, PlanReason.PROTECTION_UNKNOWN,
                     bindings=(Binding.PROTECTION,))

    if stop_touched and market.path_order is not PathOrder.TARGET_FIRST:
        if mark_beyond_confirmed:
            action = _construct(FlattenRequired, quantity=lot.quantity)
            return _plan(evaluation, PlanStatus.RECONCILE, PlanReason.FLATTEN_REQUIRED,
                         bindings=(Binding.PROTECTION,), actions=(action,))
        return _plan(evaluation, PlanStatus.RECONCILE, PlanReason.STOP_CONFIRMATION_REQUIRED,
                     bindings=(Binding.PROTECTION,))

    if pending.kind is PendingKind.UNKNOWN:
        return _plan(evaluation, PlanStatus.RECONCILE, PlanReason.PENDING_INTENT,
                     bindings=(Binding.PENDING_INTENT,))
    if pending.kind is not PendingKind.NONE:
        return _plan(evaluation, PlanStatus.HOLD, PlanReason.PENDING_INTENT,
                     bindings=(Binding.PENDING_INTENT,))

    actions = _target_actions(evaluation, due_targets)
    if actions:
        bindings = [Binding.TARGET]
        if policy.preserve_runner:
            bindings.append(Binding.RUNNER)
        return _plan(evaluation, PlanStatus.ACTIONS, PlanReason.TARGET_REDUCE,
                     bindings=tuple(bindings), actions=actions)

    stop_change, stop_reason = _stop_change(evaluation)
    if stop_change is not None and stop_reason is not None:
        binding = Binding.BREAKEVEN if stop_reason is PlanReason.BREAKEVEN else Binding.TRAIL
        return _plan(evaluation, PlanStatus.ACTIONS, stop_reason,
                     bindings=(binding,), actions=(stop_change,))

    if (policy.max_holding_bars is not None
            and lot.completed_bars_held >= policy.max_holding_bars):
        return _plan(evaluation, PlanStatus.ACTIONS, PlanReason.TIME_EXIT,
                     bindings=(Binding.TIME,),
                     actions=(_construct(CloseAction, quantity=lot.quantity),))

    proposal = _risk_proposal(evaluation)
    if proposal is not None:
        return _plan(evaluation, PlanStatus.ACTIONS, PlanReason.RISK_INCREASE_PROPOSAL,
                     bindings=(Binding.ADD_COUNT, Binding.CUMULATIVE_ADD_RISK,
                               Binding.NC06_AUTHORIZATION),
                     actions=(proposal,))
    return _plan(evaluation, PlanStatus.HOLD, PlanReason.NO_CHANGE)


def evaluate(evaluation: object) -> ManagementPlan:
    """Return one deterministic closed plan for one lot and one fact set."""
    with localcontext() as context:
        context.prec = 64
        try:
            return _evaluate(evaluation)
        except DecimalException:
            return _plan(evaluation, PlanStatus.HOLD, PlanReason.INPUT_INVALID)


__all__ = [
    'Binding', 'CleanupAction', 'CloseAction', 'FlattenRequired', 'IncreaseKind',
    'LotState', 'ManagementAction', 'ManagementEvaluation', 'ManagementPlan',
    'ManagementPolicy', 'MarketObservation', 'PathOrder', 'PendingKind',
    'PendingSummary', 'PlanReason', 'PlanStatus', 'PositionSide', 'ProtectionRepair',
    'ProtectionState', 'ProtectionStatus', 'ReduceAction', 'RiskIncreaseProposal',
    'RiskIncreaseRule', 'StopChange', 'StrategyExitFact', 'TargetRule', 'evaluate',
]
