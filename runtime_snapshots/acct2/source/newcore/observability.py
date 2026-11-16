"""NC-09 pure structured-observability value and transition kernel.

No function in this module owns a clock, thread, file, network client, sink,
runtime hook, or trading action.  Callers inject accepted NEWCORE facts and
integer timestamps/sequences; admission only returns new immutable state.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from dataclasses import dataclass
from decimal import (MAX_EMAX, MIN_EMIN, Context, Decimal, DecimalException,
                     ROUND_HALF_EVEN, localcontext)

from newcore.domain.codec import contract_sha256 as domain_sha256
from newcore.domain.decision import Decision
from newcore.domain.events import IncidentRecorded
from newcore.domain.incident import Incident
from newcore.domain.orders import Purpose, Side
from newcore.domain.reasons import ReasonCode
from newcore.management import (Binding, CloseAction, FlattenRequired,
                                ManagementPlan, PlanReason, ReduceAction)
from newcore.simulator import FillSummary, ReplayStatus, summarize_fills


DEFAULT_CAPACITY = 4096
MAX_CAPACITY = 65536
MAX_PAYLOAD_BYTES = 65536
_ARITHMETIC = Context(prec=64, rounding=ROUND_HALF_EVEN,
                      Emin=MIN_EMIN, Emax=MAX_EMAX)


class ObservationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ObservationKind(enum.StrEnum):
    INCIDENT = 'incident'
    DECISION = 'decision'
    WHY_HELD = 'why_held'
    WHY_CLOSED = 'why_closed'
    FILL = 'fill'
    EXCURSION = 'excursion'
    LIFECYCLE = 'lifecycle'


class Severity(enum.StrEnum):
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'


class Coverage(enum.StrEnum):
    COMPLETE = 'complete'
    INCOMPLETE = 'incomplete'
    GAP = 'gap'


class ExecutionPhase(enum.StrEnum):
    ENTRY = 'entry'
    EXIT = 'exit'


class AdmissionStatus(enum.StrEnum):
    ACCEPTED = 'accepted'
    DROPPED_FULL = 'dropped_full'
    DROPPED_CLOSED = 'dropped_closed'
    INVALID = 'invalid'


class WriteStatus(enum.StrEnum):
    PERSISTED = 'persisted'
    WRITE_FAILED = 'write_failed'
    INVALID = 'invalid'


class BindingKind(enum.StrEnum):
    RECORD_ID = 'record_id'
    MANAGEMENT = 'management'
    GENERATION = 'generation'


class GapReason(enum.StrEnum):
    SOURCE_DROPPED = 'source_dropped'
    OUTAGE = 'outage'
    UNAVAILABLE = 'unavailable'


class SegmentTransitionKind(enum.StrEnum):
    ADD = 'add'
    PARTIAL = 'partial'


def _fail(code: str):
    raise ObservationError(code)


def _integer(value: object, code: str, *, minimum=0, maximum=2 ** 63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code)


def _text(value: object, code: str, *, maximum=96):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or not value.isprintable() or value.strip() == ''):
        _fail(code)


def _family_id(value: object, code: str, prefix: str):
    stem = prefix + '_'
    if (type(value) is not str or not value.startswith(stem)
            or len(value) != len(stem) + 32
            or any(char not in '0123456789abcdef' for char in value[len(stem):])):
        _fail(code)


def _stable_record_id(value: object, code: str):
    if type(value) is not str or '_' not in value:
        _fail(code)
    prefix = value.split('_', 1)[0]
    if prefix not in {'acct', 'pf', 'pos', 'lot', 'int', 'res', 'rec', 'dec',
                      'evt', 'inc', 'mgt', 'obs'}:
        _fail(code)
    _family_id(value, code, prefix)


def _digest(value: object, code: str):
    if (type(value) is not str or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value)):
        _fail(code)


def _decimal(value: object, code: str, *, positive=False, nonnegative=False):
    if type(value) is not Decimal or not value.is_finite():
        _fail(code)
    if positive and value <= 0:
        _fail(code)
    if nonnegative and value < 0:
        _fail(code)


def _reason(value: object, code: str):
    if type(value) not in (ReasonCode, PlanReason):
        _fail(code)


def _canonical_decimal(value: Decimal) -> str:
    _decimal(value, 'canonical.decimal')
    if value == 0:
        return '0'
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return ('-' if sign else '') + (''.join(map(str, digits)) or '0') + 'e' + str(exponent)


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
        return [_json_value(row) for row in value]
    if type(value) is bytes:
        return {'$bytes': value.hex()}
    if value is None or type(value) in (str, int, bool):
        return value
    _fail('canonical.unsupported_type')


def canonical_bytes(value: object) -> bytes:
    return json.dumps(_json_value(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class TypedBinding:
    kind: BindingKind
    record_id: str | None = None
    management: Binding | None = None
    generation: int | None = None

    def __post_init__(self):
        if type(self.kind) is not BindingKind:
            _fail('validation.binding.kind')
        present = sum(value is not None for value in (
            self.record_id, self.management, self.generation))
        if present != 1:
            _fail('validation.binding.shape')
        if self.kind is BindingKind.RECORD_ID:
            _stable_record_id(self.record_id, 'validation.binding.record_id')
        elif self.kind is BindingKind.MANAGEMENT:
            if type(self.management) is not Binding:
                _fail('validation.binding.management')
        elif type(self.generation) is not int or self.generation <= 0:
            _fail('validation.binding.generation')


@dataclass(frozen=True, slots=True, kw_only=True)
class ReasonAlternative:
    reason: ReasonCode | PlanReason
    source_sha256: str
    bindings: tuple[TypedBinding, ...]

    def __post_init__(self):
        _reason(self.reason, 'alternative.reason')
        _digest(self.source_sha256, 'alternative.source_sha256')
        if (type(self.bindings) is not tuple
                or not all(type(row) is TypedBinding for row in self.bindings)):
            _fail('alternative.bindings')


@dataclass(frozen=True, slots=True)
class DecisionTrace:
    kind: ObservationKind
    source_type: str
    source_id: str
    reason: ReasonCode | PlanReason
    source_sha256: str
    bindings: tuple[str, ...]
    generations: tuple[int, ...]
    policy_version: str
    alternatives: tuple[ReasonAlternative, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class SequenceGap:
    stream_id: str
    after_sequence: int
    missing_from: int
    missing_through: int
    resume_sequence: int
    observed_at_ms: int
    reason: GapReason
    source: TypedBinding

    def __post_init__(self):
        _stable_record_id(self.stream_id, 'gap.stream_id')
        _integer(self.after_sequence, 'gap.after_sequence')
        _integer(self.missing_from, 'gap.missing_from', minimum=1)
        _integer(self.missing_through, 'gap.missing_through', minimum=1)
        _integer(self.resume_sequence, 'gap.resume_sequence', minimum=1)
        _integer(self.observed_at_ms, 'gap.observed_at_ms', minimum=1)
        if type(self.reason) is not GapReason or type(self.source) is not TypedBinding:
            _fail('gap.type')
        if (self.missing_from != self.after_sequence + 1
                or self.missing_through < self.missing_from
                or self.resume_sequence != self.missing_through + 1):
            _fail('gap.range')


def _seal_gap(gap: object) -> SequenceGap:
    if type(gap) is not SequenceGap:
        _fail('gap.type')
    source = TypedBinding(kind=gap.source.kind, record_id=gap.source.record_id,
                          management=gap.source.management,
                          generation=gap.source.generation)
    return SequenceGap(
        stream_id=gap.stream_id, after_sequence=gap.after_sequence,
        missing_from=gap.missing_from, missing_through=gap.missing_through,
        resume_sequence=gap.resume_sequence, observed_at_ms=gap.observed_at_ms,
        reason=gap.reason, source=source)


def _alternatives(values: object) -> tuple[ReasonAlternative, ...]:
    if (type(values) is not tuple
            or not all(type(row) is ReasonAlternative for row in values)):
        _fail('trace.alternatives')
    return tuple(ReasonAlternative(reason=row.reason,
                                   source_sha256=row.source_sha256,
                                   bindings=tuple(TypedBinding(
                                       kind=item.kind, record_id=item.record_id,
                                       management=item.management,
                                       generation=item.generation)
                                                  for item in row.bindings))
                 for row in values)


def trace_decision(decision: object, *, alternatives=()) -> DecisionTrace:
    if type(decision) is not Decision:
        _fail('trace.decision')
    return DecisionTrace(
        ObservationKind.DECISION, 'Decision', decision.decision_id, decision.reason,
        domain_sha256(decision), tuple(decision.evidence), (), decision.policy_version,
        _alternatives(alternatives))


def trace_plan(plan: object, kind: object, *, alternatives=()) -> DecisionTrace:
    if (type(plan) is not ManagementPlan or type(kind) is not ObservationKind
            or kind not in (ObservationKind.WHY_HELD, ObservationKind.WHY_CLOSED)):
        _fail('trace.plan')
    if (type(plan.reason) is not PlanReason or type(plan.bindings) is not tuple
            or not all(type(row) is Binding for row in plan.bindings)):
        _fail('trace.plan_shape')
    if type(plan.actions) is not tuple:
        _fail('trace.plan_actions')
    terminal_closes = tuple(row for row in plan.actions if type(row) is CloseAction)
    nonterminal_exits = tuple(row for row in plan.actions
                              if type(row) in (FlattenRequired, ReduceAction))
    exit_reasons = frozenset({
        PlanReason.FLATTEN_REQUIRED, PlanReason.FORCED_EXIT,
        PlanReason.STRATEGY_EXIT, PlanReason.TARGET_REDUCE, PlanReason.TIME_EXIT,
    })
    terminal_close_reasons = frozenset({
        PlanReason.FORCED_EXIT, PlanReason.STRATEGY_EXIT, PlanReason.TIME_EXIT,
    })
    if kind is ObservationKind.WHY_HELD:
        if terminal_closes or nonterminal_exits or plan.reason in exit_reasons:
            _fail('trace.held_semantics')
    elif (len(terminal_closes) != 1 or nonterminal_exits
          or plan.reason not in terminal_close_reasons
          or plan.reason is PlanReason.NO_CHANGE):
        _fail('trace.closed_semantics')
    generations = (plan.lot_generation, plan.policy_generation, plan.market_generation,
                   plan.protection_generation, plan.strategy_exit_generation,
                   plan.pending_generation)
    if not all(type(row) is int and row > 0 for row in generations):
        _fail('trace.generations')
    return DecisionTrace(
        kind, 'ManagementPlan', plan.plan_id, plan.reason, canonical_sha256(plan),
        tuple(row.value for row in plan.bindings), generations, plan.policy_version,
        _alternatives(alternatives))


@dataclass(frozen=True, slots=True)
class IncidentMirror:
    event_id: str
    incident_id: str
    account_id: str
    reason: ReasonCode
    at_ms: int
    source_sha256: str
    symbol: str | None
    side: Side | None
    intent_refs: tuple[str, ...]
    lot_refs: tuple[str, ...]
    position_refs: tuple[str, ...]
    evidence: tuple[str, ...]
    detail: str | None


def mirror_durable_incident(event: object) -> IncidentMirror:
    if type(event) is not IncidentRecorded or type(event.incident) is not Incident:
        _fail('incident.durable_event')
    incident = event.incident
    if event.reason is not incident.kind or event.account_id != incident.account_id:
        _fail('incident.binding')
    return IncidentMirror(event.event_id, incident.incident_id, incident.account_id,
                          incident.kind, incident.at_ms, domain_sha256(event),
                          incident.symbol, incident.side, tuple(incident.intent_refs),
                          tuple(incident.lot_refs), tuple(incident.position_refs),
                          tuple(incident.evidence), incident.detail)


@dataclass(frozen=True, slots=True)
class FillEvidenceMetric:
    sequence: int
    at_ms: int
    known: bool
    quantity: Decimal
    price: Decimal | None
    liquidity: str | None


@dataclass(frozen=True, slots=True)
class FillMetric:
    source_id: str
    cost_model_id: str
    cost_model_version: str
    settlement_asset: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    average_price: Decimal | None
    reference_price: Decimal | None
    slippage_price: Decimal | None
    slippage_amount: Decimal | None
    slippage_bps: Decimal | None
    side: Side
    phase: ExecutionPhase
    coverage: Coverage
    source_sha256: str
    evidence: tuple[FillEvidenceMetric, ...]


def _valid_fill_summary(summary: FillSummary):
    for name in ('requested_quantity', 'filled_quantity', 'remaining_quantity'):
        _decimal(getattr(summary, name), 'fill.' + name, nonnegative=True)
    if summary.requested_quantity <= 0:
        _fail('fill.requested_quantity')
    with localcontext(_ARITHMETIC):
        if summary.filled_quantity + summary.remaining_quantity != summary.requested_quantity:
            _fail('fill.quantities')
    if summary.filled_quantity == 0:
        if summary.average_price is not None:
            _fail('fill.average_price')
    else:
        _decimal(summary.average_price, 'fill.average_price', positive=True)
    try:
        rebuilt = summarize_fills(summary.requested_quantity, summary.fills)
    except Exception:
        _fail('fill.evidence')
    if rebuilt != summary:
        _fail('fill.summary_mismatch')


def measure_fill(summary: object, *, reference_price: object, side: object,
                 phase: object, source_id: object, cost_model_id: object,
                 cost_model_version: object, settlement_asset: object) -> FillMetric:
    if type(summary) is not FillSummary or type(side) is not Side or type(phase) is not ExecutionPhase:
        _fail('fill.input')
    _valid_fill_summary(summary)
    if (type(source_id) is not str or not any(
            source_id.startswith(prefix + '_') for prefix in ('res', 'dec'))):
        _fail('fill.source_id')
    _family_id(source_id, 'fill.source_id', source_id.split('_', 1)[0])
    _text(cost_model_id, 'fill.cost_model_id')
    _text(cost_model_version, 'fill.cost_model_version')
    _text(settlement_asset, 'fill.settlement_asset', maximum=16)
    if reference_price is not None:
        _decimal(reference_price, 'fill.reference_price', positive=True)
    complete = summary.status is ReplayStatus.COMPLETE
    coverage = Coverage.COMPLETE if complete else Coverage.INCOMPLETE
    slip_price = slip_amount = slip_bps = None
    if summary.average_price is not None and reference_price is not None:
        direction = Decimal(1) if side is Side.LONG else Decimal(-1)
        if phase is ExecutionPhase.EXIT:
            direction = -direction
        with localcontext(_ARITHMETIC):
            try:
                slip_price = direction * (summary.average_price - reference_price)
                slip_amount = slip_price * summary.filled_quantity
                slip_bps = slip_price / abs(reference_price) * Decimal(10_000)
            except DecimalException:
                _fail('fill.arithmetic')
    evidence = tuple(FillEvidenceMetric(
        row.sequence, row.at_ms, row.known, row.quantity, row.price,
        None if row.liquidity is None else row.liquidity.value)
        for row in summary.fills)
    return FillMetric(source_id, cost_model_id, cost_model_version,
                      settlement_asset, summary.requested_quantity, summary.filled_quantity,
                      summary.remaining_quantity, summary.average_price,
                      reference_price, slip_price, slip_amount, slip_bps, side,
                      phase, coverage, canonical_sha256(summary), evidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentTransition:
    lot_id: str
    kind: SegmentTransitionKind
    from_segment_id: str
    to_segment_id: str
    from_generation: int
    to_generation: int
    sequence: int
    source: TypedBinding

    def __post_init__(self):
        _family_id(self.lot_id, 'segment.lot_id', 'lot')
        if type(self.kind) is not SegmentTransitionKind:
            _fail('segment.kind')
        _text(self.from_segment_id, 'segment.from_segment_id', maximum=64)
        _text(self.to_segment_id, 'segment.to_segment_id', maximum=64)
        if self.from_segment_id == self.to_segment_id:
            _fail('segment.identity')
        _integer(self.from_generation, 'segment.from_generation', minimum=1)
        _integer(self.to_generation, 'segment.to_generation', minimum=1)
        if self.to_generation < self.from_generation:
            _fail('segment.generation')
        _integer(self.sequence, 'segment.sequence', minimum=1)
        if type(self.source) is not TypedBinding:
            _fail('segment.source')


def _seal_segment_transition(value: object) -> SegmentTransition:
    if type(value) is not SegmentTransition:
        _fail('segment.transition')
    source = TypedBinding(kind=value.source.kind, record_id=value.source.record_id,
                          management=value.source.management,
                          generation=value.source.generation)
    return SegmentTransition(
        lot_id=value.lot_id, kind=value.kind,
        from_segment_id=value.from_segment_id, to_segment_id=value.to_segment_id,
        from_generation=value.from_generation, to_generation=value.to_generation,
        sequence=value.sequence, source=source)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExcursionSample:
    lot_id: str
    lot_generation: int
    segment_id: str
    sequence: int
    at_ms: int
    side: Side
    mark: Decimal | None
    average_entry: Decimal
    quantity: Decimal
    risk_basis_id: str
    accepted_risk: Decimal
    coverage: Coverage

    def __post_init__(self):
        _family_id(self.lot_id, 'excursion.lot_id', 'lot')
        _integer(self.lot_generation, 'excursion.lot_generation', minimum=1)
        _text(self.segment_id, 'excursion.segment_id', maximum=64)
        _integer(self.sequence, 'excursion.sequence', minimum=1)
        _integer(self.at_ms, 'excursion.at_ms', minimum=1)
        if type(self.side) is not Side or type(self.coverage) is not Coverage:
            _fail('excursion.enum')
        _decimal(self.average_entry, 'excursion.average_entry', positive=True)
        _decimal(self.quantity, 'excursion.quantity', positive=True)
        _text(self.risk_basis_id, 'excursion.risk_basis_id', maximum=64)
        _decimal(self.accepted_risk, 'excursion.accepted_risk', positive=True)
        if self.coverage is not Coverage.COMPLETE:
            _fail('excursion.explicit_gap_required')
        _decimal(self.mark, 'excursion.mark', positive=True)


@dataclass(frozen=True, slots=True)
class ExcursionPoint:
    price: Decimal
    amount: Decimal
    percent: Decimal
    r_multiple: Decimal
    sequence: int
    at_ms: int


@dataclass(frozen=True, slots=True)
class ExcursionSegment:
    lot_generation: int
    segment_id: str
    side: Side
    average_entry: Decimal
    quantity: Decimal
    risk_basis_id: str
    accepted_risk: Decimal
    mfe: ExcursionPoint | None
    mae: ExcursionPoint | None
    transition: SegmentTransition | None
    gaps: tuple[SequenceGap, ...]
    last_sequence: int
    last_at_ms: int
    gap_count: int


@dataclass(frozen=True, slots=True)
class ExcursionSeries:
    lot_id: str
    segments: tuple[ExcursionSegment, ...]


def _point(distance: Decimal, sample: ExcursionSample) -> ExcursionPoint:
    distance = max(distance, Decimal(0))
    with localcontext(_ARITHMETIC):
        try:
            amount = distance * sample.quantity
            percent = distance / abs(sample.average_entry) * Decimal(100)
            r_multiple = amount / sample.accepted_risk
        except DecimalException:
            _fail('excursion.arithmetic')
    return ExcursionPoint(distance, amount, percent, r_multiple,
                          sample.sequence, sample.at_ms)


def _new_segment(sample: ExcursionSample,
                 transition: SegmentTransition | None = None) -> ExcursionSegment:
    direction = Decimal(1) if sample.side is Side.LONG else Decimal(-1)
    favorable = direction * (sample.mark - sample.average_entry)
    adverse = -favorable
    return ExcursionSegment(sample.lot_generation, sample.segment_id, sample.side,
                            sample.average_entry, sample.quantity,
                            sample.risk_basis_id, sample.accepted_risk,
                            _point(favorable, sample), _point(adverse, sample),
                            transition, (), sample.sequence, sample.at_ms, 0)


def _seal_excursion_point(value: object) -> ExcursionPoint:
    if type(value) is not ExcursionPoint:
        _fail('excursion.point')
    for name in ('price', 'amount', 'percent', 'r_multiple'):
        _decimal(getattr(value, name), 'excursion.point_' + name, nonnegative=True)
    _integer(value.sequence, 'excursion.point_sequence', minimum=1)
    _integer(value.at_ms, 'excursion.point_at_ms', minimum=1)
    return ExcursionPoint(value.price, value.amount, value.percent,
                          value.r_multiple, value.sequence, value.at_ms)


def _seal_excursion_segment(value: object, lot_id: str) -> ExcursionSegment:
    if type(value) is not ExcursionSegment:
        _fail('excursion.segment')
    _integer(value.lot_generation, 'excursion.segment_generation', minimum=1)
    _text(value.segment_id, 'excursion.segment_id', maximum=64)
    if type(value.side) is not Side:
        _fail('excursion.segment_side')
    _decimal(value.average_entry, 'excursion.segment_average', positive=True)
    _decimal(value.quantity, 'excursion.segment_quantity', positive=True)
    _text(value.risk_basis_id, 'excursion.segment_risk_basis', maximum=64)
    _decimal(value.accepted_risk, 'excursion.segment_risk', positive=True)
    mfe = _seal_excursion_point(value.mfe)
    mae = _seal_excursion_point(value.mae)
    with localcontext(_ARITHMETIC):
        try:
            for point in (mfe, mae):
                if (point.amount != point.price * value.quantity
                        or point.percent != point.price / abs(value.average_entry) * Decimal(100)
                        or point.r_multiple != point.amount / value.accepted_risk):
                    _fail('excursion.point_basis')
        except DecimalException:
            _fail('excursion.point_arithmetic')
    transition = (None if value.transition is None
                  else _seal_segment_transition(value.transition))
    if transition is not None and transition.lot_id != lot_id:
        _fail('excursion.segment_transition_lot')
    if type(value.gaps) is not tuple:
        _fail('excursion.segment_gaps')
    gaps = tuple(_seal_gap(row) for row in value.gaps)
    if any(row.stream_id != lot_id for row in gaps):
        _fail('excursion.segment_gap_lot')
    _integer(value.last_sequence, 'excursion.segment_last_sequence', minimum=1)
    _integer(value.last_at_ms, 'excursion.segment_last_at_ms', minimum=1)
    _integer(value.gap_count, 'excursion.segment_gap_count')
    if value.gap_count != len(gaps):
        _fail('excursion.segment_gap_count')
    if (mfe.sequence > value.last_sequence or mae.sequence > value.last_sequence
            or mfe.at_ms > value.last_at_ms or mae.at_ms > value.last_at_ms):
        _fail('excursion.segment_point_order')
    if gaps and (gaps[-1].missing_through > value.last_sequence
                 or gaps[-1].observed_at_ms > value.last_at_ms):
        _fail('excursion.segment_gap_order')
    return ExcursionSegment(
        value.lot_generation, value.segment_id, value.side, value.average_entry,
        value.quantity, value.risk_basis_id, value.accepted_risk, mfe, mae,
        transition, gaps, value.last_sequence, value.last_at_ms, value.gap_count)


def _seal_excursion_series(value: object) -> ExcursionSeries:
    if type(value) is not ExcursionSeries:
        _fail('excursion.series')
    _family_id(value.lot_id, 'excursion.series_lot_id', 'lot')
    if (type(value.segments) is not tuple or not value.segments
            or not all(type(row) is ExcursionSegment for row in value.segments)):
        _fail('excursion.series_segments')
    segments = tuple(_seal_excursion_segment(row, value.lot_id)
                     for row in value.segments)
    if segments[0].transition is not None:
        _fail('excursion.initial_transition')
    if len({row.segment_id for row in segments}) != len(segments):
        _fail('excursion.segment_duplicate')
    for previous, current in zip(segments, segments[1:]):
        transition = current.transition
        if (transition is None
                or transition.from_segment_id != previous.segment_id
                or transition.to_segment_id != current.segment_id
                or transition.from_generation != previous.lot_generation
                or transition.to_generation != current.lot_generation
                or transition.sequence != previous.last_sequence + 1
                or transition.sequence > current.last_sequence
                or current.mfe.sequence < transition.sequence
                or current.mae.sequence < transition.sequence
                or current.side is not previous.side):
            _fail('excursion.segment_chain')
        if (transition.kind is SegmentTransitionKind.ADD
                and current.quantity <= previous.quantity):
            _fail('excursion.segment_add_quantity')
        if (transition.kind is SegmentTransitionKind.PARTIAL
                and current.quantity >= previous.quantity):
            _fail('excursion.segment_partial_quantity')
    return ExcursionSeries(value.lot_id, segments)


def observe_excursion(series: object, sample: object, *, transition=None) -> ExcursionSeries:
    if type(sample) is not ExcursionSample:
        _fail('excursion.sample')
    if series is None:
        if sample.sequence != 1 or transition is not None:
            _fail('excursion.initial_sequence')
        return ExcursionSeries(sample.lot_id, (_new_segment(sample),))
    series = _seal_excursion_series(series)
    if series.lot_id != sample.lot_id:
        _fail('excursion.series')
    current = series.segments[-1]
    if sample.sequence != current.last_sequence + 1 or sample.at_ms < current.last_at_ms:
        _fail('excursion.order')
    frozen = (sample.segment_id, sample.side, sample.average_entry, sample.quantity,
              sample.risk_basis_id, sample.accepted_risk)
    current_basis = (current.segment_id, current.side, current.average_entry,
                     current.quantity, current.risk_basis_id, current.accepted_risk)
    if frozen != current_basis or sample.lot_generation != current.lot_generation:
        transition = _seal_segment_transition(transition)
        if (transition.lot_id != sample.lot_id
                or transition.from_segment_id != current.segment_id
                or transition.to_segment_id != sample.segment_id
                or transition.from_generation != current.lot_generation
                or transition.to_generation != sample.lot_generation
                or transition.sequence != sample.sequence):
            _fail('excursion.segment_transition')
        if sample.side is not current.side:
            _fail('excursion.side_transition')
        if (transition.kind is SegmentTransitionKind.ADD
                and sample.quantity <= current.quantity):
            _fail('excursion.add_quantity')
        if (transition.kind is SegmentTransitionKind.PARTIAL
                and sample.quantity >= current.quantity):
            _fail('excursion.partial_quantity')
        return ExcursionSeries(
            series.lot_id, series.segments + (_new_segment(sample, transition),))
    if transition is not None:
        _fail('excursion.unneeded_transition')
    direction = Decimal(1) if sample.side is Side.LONG else Decimal(-1)
    favorable = _point(direction * (sample.mark - sample.average_entry), sample)
    adverse = _point(-direction * (sample.mark - sample.average_entry), sample)
    updated = dataclasses.replace(
        current,
        mfe=(favorable if current.mfe is None or favorable.price > current.mfe.price
             else current.mfe),
        mae=(adverse if current.mae is None or adverse.price > current.mae.price
             else current.mae),
        last_sequence=sample.sequence,
        last_at_ms=sample.at_ms)
    return ExcursionSeries(series.lot_id, series.segments[:-1] + (updated,))


def observe_excursion_gap(series: object, gap: object) -> ExcursionSeries:
    series = _seal_excursion_series(series)
    gap = _seal_gap(gap)
    current = series.segments[-1]
    if (gap.stream_id != series.lot_id or gap.after_sequence != current.last_sequence
            or gap.observed_at_ms < current.last_at_ms):
        _fail('excursion.gap_binding')
    updated = dataclasses.replace(
        current, gaps=current.gaps + (gap,), last_sequence=gap.missing_through,
        last_at_ms=gap.observed_at_ms, gap_count=current.gap_count + 1)
    return ExcursionSeries(series.lot_id, series.segments[:-1] + (updated,))


@dataclass(frozen=True, slots=True, kw_only=True)
class LifecycleSample:
    stream_id: str
    sequence: int
    at_ms: int
    realized_pnl: Decimal | None
    open_pnl: Decimal | None
    fees: Decimal | None
    slippage: Decimal | None
    funding: Decimal | None
    modeled_exit_cost: Decimal | None
    coverage: Coverage

    def __post_init__(self):
        _stable_record_id(self.stream_id, 'validation.lifecycle.stream_id')
        _integer(self.sequence, 'validation.lifecycle.sequence', minimum=1)
        _integer(self.at_ms, 'validation.lifecycle.at_ms', minimum=1)
        if type(self.coverage) is not Coverage:
            _fail('validation.lifecycle.coverage')
        values = (self.realized_pnl, self.open_pnl, self.fees, self.slippage,
                  self.funding, self.modeled_exit_cost)
        if self.coverage is not Coverage.COMPLETE:
            _fail('validation.lifecycle.explicit_gap_required')
        for value in values:
            _decimal(value, 'validation.lifecycle.value')
        for value in (self.fees, self.slippage, self.modeled_exit_cost):
            if value < 0:
                _fail('validation.lifecycle.cost')


@dataclass(frozen=True, slots=True)
class LifecycleSeries:
    stream_id: str
    peak_net: Decimal | None
    peak_sequence: int | None
    peak_at_ms: int | None
    latest_net: Decimal | None
    latest_sequence: int
    latest_at_ms: int
    gap_count: int
    gaps: tuple[SequenceGap, ...]
    closed: bool
    giveback: Decimal | None
    giveback_percent: Decimal | None


def observe_lifecycle(series: object, sample: object, *, closed=False) -> LifecycleSeries:
    if type(sample) is not LifecycleSample or type(closed) is not bool:
        _fail('validation.lifecycle.input')
    if series is not None:
        if (type(series) is not LifecycleSeries or series.closed
                or series.stream_id != sample.stream_id):
            _fail('validation.lifecycle.series')
        if (sample.sequence != series.latest_sequence + 1
                or sample.at_ms < series.latest_at_ms):
            _fail('validation.lifecycle.order')
    elif sample.sequence != 1:
        _fail('validation.lifecycle.initial_sequence')
    with localcontext(_ARITHMETIC):
        try:
            net = (sample.realized_pnl + sample.open_pnl - sample.fees
                   - sample.slippage + sample.funding - sample.modeled_exit_cost)
        except DecimalException:
            _fail('validation.lifecycle.arithmetic')
    if series is None or series.peak_net is None or net > series.peak_net:
        peak, peak_sequence, peak_at = net, sample.sequence, sample.at_ms
    else:
        peak, peak_sequence, peak_at = (series.peak_net, series.peak_sequence,
                                        series.peak_at_ms)
    giveback = giveback_percent = None
    if closed:
        with localcontext(_ARITHMETIC):
            giveback = peak - net
            if peak > 0:
                giveback_percent = giveback / peak * Decimal(100)
    return LifecycleSeries(
        sample.stream_id, peak, peak_sequence, peak_at, net, sample.sequence, sample.at_ms,
        0 if series is None else series.gap_count,
        () if series is None else series.gaps, closed, giveback,
        giveback_percent)


def observe_lifecycle_gap(series: object, gap: object) -> LifecycleSeries:
    if type(series) is not LifecycleSeries or series.closed:
        _fail('validation.lifecycle.series')
    gap = _seal_gap(gap)
    if (gap.stream_id != series.stream_id
            or gap.after_sequence != series.latest_sequence
            or gap.observed_at_ms < series.latest_at_ms):
        _fail('validation.lifecycle.gap_binding')
    return dataclasses.replace(
        series, latest_sequence=gap.missing_through,
        latest_at_ms=gap.observed_at_ms, gap_count=series.gap_count + 1,
        gaps=series.gaps + (gap,))


_PAYLOAD_TYPES = (DecisionTrace, IncidentMirror, FillMetric, ExcursionSeries,
                  LifecycleSeries)


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    observation_id: str
    account_id: str
    sequence: int
    at_ms: int
    kind: ObservationKind
    severity: Severity
    reason: ReasonCode | PlanReason
    source_sha256: str
    payload_bytes: bytes

    def __post_init__(self):
        _family_id(self.observation_id, 'record.observation_id', 'obs')
        _family_id(self.account_id, 'record.account_id', 'acct')
        _integer(self.sequence, 'record.sequence', minimum=1)
        _integer(self.at_ms, 'record.at_ms', minimum=1)
        if type(self.kind) is not ObservationKind or type(self.severity) is not Severity:
            _fail('record.enum')
        _reason(self.reason, 'record.reason')
        _digest(self.source_sha256, 'record.source_sha256')
        if (type(self.payload_bytes) is not bytes
                or not 1 <= len(self.payload_bytes) <= MAX_PAYLOAD_BYTES):
            _fail('record.payload_bytes')
        material = (self.account_id, self.sequence, self.at_ms, self.kind,
                    self.severity, self.reason, self.source_sha256,
                    self.payload_bytes)
        expected = 'obs_' + canonical_sha256(material)[:32]
        if self.observation_id != expected:
            _fail('record.identity')


def make_observation(*, account_id: object, sequence: object, at_ms: object,
                     kind: object, severity: object, reason: object,
                     source_sha256: object, payload: object) -> ObservationRecord:
    _family_id(account_id, 'record.account_id', 'acct')
    _integer(sequence, 'record.sequence', minimum=1)
    _integer(at_ms, 'record.at_ms', minimum=1)
    if type(kind) is not ObservationKind or type(severity) is not Severity:
        _fail('record.enum')
    _reason(reason, 'record.reason')
    _digest(source_sha256, 'record.source_sha256')
    if type(payload) not in _PAYLOAD_TYPES:
        _fail('record.payload')
    if type(payload) is DecisionTrace:
        if (kind is not payload.kind or reason is not payload.reason
                or source_sha256 != payload.source_sha256):
            _fail('record.trace_binding')
    elif type(payload) is IncidentMirror:
        if (kind is not ObservationKind.INCIDENT or reason is not payload.reason
                or source_sha256 != payload.source_sha256):
            _fail('record.incident_binding')
    elif type(payload) is FillMetric:
        if (kind is not ObservationKind.FILL or type(reason) is not ReasonCode
                or reason.namespace not in ('entry', 'exit')
                or source_sha256 != payload.source_sha256):
            _fail('record.fill_binding')
    elif type(payload) is ExcursionSeries:
        if (kind is not ObservationKind.EXCURSION
                or source_sha256 != canonical_sha256(payload)):
            _fail('record.excursion_binding')
    elif (kind is not ObservationKind.LIFECYCLE
          or source_sha256 != canonical_sha256(payload)):
        _fail('record.lifecycle_binding')
    payload_bytes = canonical_bytes(payload)
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        _fail('record.payload_size')
    material = (account_id, sequence, at_ms, kind, severity, reason,
                source_sha256, payload_bytes)
    observation_id = 'obs_' + canonical_sha256(material)[:32]
    return ObservationRecord(observation_id, account_id, sequence, at_ms, kind,
                             severity, reason, source_sha256, payload_bytes)


@dataclass(frozen=True, slots=True)
class DropCount:
    kind: ObservationKind
    severity: Severity
    full: int
    closed: int


@dataclass(frozen=True, slots=True)
class QueueCounters:
    attempted: int
    accepted: int
    persisted: int
    dropped_full: int
    dropped_closed: int
    invalid: int
    write_errors: int
    drops: tuple[DropCount, ...]


@dataclass(frozen=True, slots=True, init=False)
class QueueState:
    account_id: str
    capacity: int
    closed: bool
    queued: tuple[ObservationRecord, ...]
    gaps: tuple[SequenceGap, ...]
    last_sequence: int
    generation: int
    counters: QueueCounters

    def __init__(self, *args, **kwargs):
        raise TypeError('QueueState is factory-only')

    def __init_subclass__(cls, **kwargs):
        raise TypeError('QueueState is final')


@dataclass(frozen=True, slots=True)
class AdmissionOutcome:
    status: AdmissionStatus
    observation_id: str | None
    queued: int
    generation: int


@dataclass(frozen=True, slots=True)
class WriterOutcome:
    status: WriteStatus
    observation_id: str | None
    queued: int
    generation: int


def _construct_queue(**values) -> QueueState:
    row = object.__new__(QueueState)
    for name, value in values.items():
        object.__setattr__(row, name, value)
    return row


def _zero_counters() -> QueueCounters:
    return QueueCounters(0, 0, 0, 0, 0, 0, 0, ())


def _seal_queue(state: object) -> QueueState:
    if type(state) is not QueueState:
        _fail('queue.state')
    _family_id(state.account_id, 'queue.account_id', 'acct')
    _integer(state.capacity, 'queue.capacity', minimum=1, maximum=MAX_CAPACITY)
    if type(state.closed) is not bool or type(state.queued) is not tuple:
        _fail('queue.shape')
    if type(state.gaps) is not tuple:
        _fail('queue.gaps')
    _integer(state.last_sequence, 'queue.last_sequence')
    _integer(state.generation, 'queue.generation', minimum=1)
    if len(state.queued) > state.capacity:
        _fail('queue.capacity_exceeded')
    sealed_records = []
    for row in state.queued:
        if type(row) is not ObservationRecord:
            _fail('queue.record')
        sealed = ObservationRecord(
            row.observation_id, row.account_id, row.sequence, row.at_ms, row.kind,
            row.severity, row.reason, row.source_sha256, bytes(row.payload_bytes))
        if sealed.account_id != state.account_id:
            _fail('queue.record_account')
        sealed_records.append(sealed)
    if any(right.sequence <= left.sequence
           for left, right in zip(sealed_records, sealed_records[1:])):
        _fail('queue.record_order')
    if sealed_records and sealed_records[-1].sequence > state.last_sequence:
        _fail('queue.last_sequence')
    sealed_gaps = tuple(_seal_gap(row) for row in state.gaps)
    if any(row.stream_id != state.account_id for row in sealed_gaps):
        _fail('queue.gap_account')
    if any(right.missing_from <= left.missing_through
           for left, right in zip(sealed_gaps, sealed_gaps[1:])):
        _fail('queue.gap_order')
    if sealed_gaps and sealed_gaps[-1].missing_through > state.last_sequence:
        _fail('queue.gap_sequence')
    counters = state.counters
    if type(counters) is not QueueCounters or type(counters.drops) is not tuple:
        _fail('queue.counters')
    numbers = (counters.attempted, counters.accepted, counters.persisted,
               counters.dropped_full, counters.dropped_closed, counters.invalid,
               counters.write_errors)
    if not all(type(row) is int and row >= 0 for row in numbers):
        _fail('queue.counter_value')
    if (counters.attempted != counters.accepted + counters.dropped_full
            + counters.dropped_closed + counters.invalid):
        _fail('queue.attempted_count')
    if counters.accepted != counters.persisted + counters.write_errors + len(sealed_records):
        _fail('queue.accepted_count')
    seen = set()
    full = closed = 0
    sealed_drops = []
    for row in counters.drops:
        if (type(row) is not DropCount or type(row.kind) is not ObservationKind
                or type(row.severity) is not Severity or type(row.full) is not int
                or type(row.closed) is not int or row.full < 0 or row.closed < 0):
            _fail('queue.drop_count')
        key = (row.kind, row.severity)
        if key in seen:
            _fail('queue.drop_duplicate')
        seen.add(key)
        full += row.full
        closed += row.closed
        sealed_drops.append(DropCount(row.kind, row.severity, row.full, row.closed))
    if full != counters.dropped_full or closed != counters.dropped_closed:
        _fail('queue.drop_total')
    sealed_counters = QueueCounters(*numbers, tuple(sealed_drops))
    return _construct_queue(
        account_id=state.account_id, capacity=state.capacity, closed=state.closed,
        queued=tuple(sealed_records), gaps=sealed_gaps,
        last_sequence=state.last_sequence,
        generation=state.generation, counters=sealed_counters)


def new_queue(account_id: object, capacity: object = DEFAULT_CAPACITY) -> QueueState:
    _family_id(account_id, 'queue.account_id', 'acct')
    _integer(capacity, 'queue.capacity', minimum=1, maximum=MAX_CAPACITY)
    return _construct_queue(account_id=account_id, capacity=capacity, closed=False,
                            queued=(), gaps=(), last_sequence=0, generation=1,
                            counters=_zero_counters())


def _counter(counters: QueueCounters, **changes) -> QueueCounters:
    return dataclasses.replace(counters, **changes)


def _record_drop(counters: QueueCounters, record: ObservationRecord, *, closed: bool):
    rows = list(counters.drops)
    for index, row in enumerate(rows):
        if (row.kind, row.severity) == (record.kind, record.severity):
            rows[index] = dataclasses.replace(
                row, closed=row.closed + int(closed), full=row.full + int(not closed))
            break
    else:
        rows.append(DropCount(record.kind, record.severity,
                              int(not closed), int(closed)))
    rows.sort(key=lambda row: (row.kind.value, row.severity.value))
    return tuple(rows)


def _next_state(state: QueueState, **changes) -> QueueState:
    values = {field.name: getattr(state, field.name) for field in dataclasses.fields(state)}
    values.update(changes)
    values['generation'] = state.generation + 1
    return _construct_queue(**values)


def admit(state: object, record: object) -> tuple[QueueState, AdmissionOutcome]:
    state = _seal_queue(state)
    counters = state.counters
    try:
        if type(record) is not ObservationRecord:
            raise ObservationError('record.type')
        record = ObservationRecord(
            record.observation_id, record.account_id, record.sequence, record.at_ms,
            record.kind, record.severity, record.reason, record.source_sha256,
            bytes(record.payload_bytes))
    except Exception:
        counters = _counter(counters, attempted=counters.attempted + 1,
                            invalid=counters.invalid + 1)
        next_state = _next_state(state, counters=counters)
        return next_state, AdmissionOutcome(AdmissionStatus.INVALID, None,
                                            len(next_state.queued), next_state.generation)
    counters = _counter(counters, attempted=counters.attempted + 1)
    if (record.account_id != state.account_id
            or record.sequence != state.last_sequence + 1):
        counters = _counter(counters, invalid=counters.invalid + 1)
        next_state = _next_state(state, counters=counters)
        return next_state, AdmissionOutcome(AdmissionStatus.INVALID,
                                            record.observation_id,
                                            len(next_state.queued), next_state.generation)
    if state.closed:
        counters = _counter(
            counters, dropped_closed=counters.dropped_closed + 1,
            drops=_record_drop(counters, record, closed=True))
        gap = SequenceGap(
            stream_id=state.account_id, after_sequence=state.last_sequence,
            missing_from=record.sequence, missing_through=record.sequence,
            resume_sequence=record.sequence + 1, observed_at_ms=record.at_ms,
            reason=GapReason.SOURCE_DROPPED,
            source=TypedBinding(kind=BindingKind.RECORD_ID,
                                record_id=record.observation_id))
        next_state = _next_state(state, counters=counters, gaps=state.gaps + (gap,),
                                 last_sequence=record.sequence)
        return next_state, AdmissionOutcome(AdmissionStatus.DROPPED_CLOSED,
                                            record.observation_id,
                                            len(next_state.queued), next_state.generation)
    if len(state.queued) >= state.capacity:
        counters = _counter(
            counters, dropped_full=counters.dropped_full + 1,
            drops=_record_drop(counters, record, closed=False))
        gap = SequenceGap(
            stream_id=state.account_id, after_sequence=state.last_sequence,
            missing_from=record.sequence, missing_through=record.sequence,
            resume_sequence=record.sequence + 1, observed_at_ms=record.at_ms,
            reason=GapReason.SOURCE_DROPPED,
            source=TypedBinding(kind=BindingKind.RECORD_ID,
                                record_id=record.observation_id))
        next_state = _next_state(state, counters=counters, gaps=state.gaps + (gap,),
                                 last_sequence=record.sequence)
        return next_state, AdmissionOutcome(AdmissionStatus.DROPPED_FULL,
                                            record.observation_id,
                                            len(next_state.queued), next_state.generation)
    counters = _counter(counters, accepted=counters.accepted + 1)
    next_state = _next_state(state, counters=counters,
                             queued=state.queued + (record,),
                             last_sequence=record.sequence)
    return next_state, AdmissionOutcome(AdmissionStatus.ACCEPTED,
                                        record.observation_id,
                                        len(next_state.queued), next_state.generation)


def close_queue(state: object) -> QueueState:
    state = _seal_queue(state)
    return state if state.closed else _next_state(state, closed=True)


def declare_queue_gap(state: object, gap: object) -> QueueState:
    state = _seal_queue(state)
    gap = _seal_gap(gap)
    if (gap.stream_id != state.account_id
            or gap.after_sequence != state.last_sequence):
        _fail('queue.gap_binding')
    return _next_state(state, gaps=state.gaps + (gap,),
                       last_sequence=gap.missing_through)


def acknowledge(state: object, observation_id: object, *, persisted: object):
    state = _seal_queue(state)
    if type(persisted) is not bool:
        _fail('writer.input')
    if (type(observation_id) is not str or not state.queued
            or state.queued[0].observation_id != observation_id):
        return state, WriterOutcome(WriteStatus.INVALID, None,
                                    len(state.queued), state.generation)
    counters = state.counters
    if persisted:
        counters = _counter(counters, persisted=counters.persisted + 1)
        status = WriteStatus.PERSISTED
    else:
        counters = _counter(counters, write_errors=counters.write_errors + 1)
        status = WriteStatus.WRITE_FAILED
    next_state = _next_state(state, queued=state.queued[1:], counters=counters)
    return next_state, WriterOutcome(status, observation_id,
                                     len(next_state.queued), next_state.generation)


__all__ = [
    'DEFAULT_CAPACITY', 'MAX_CAPACITY', 'MAX_PAYLOAD_BYTES', 'AdmissionOutcome', 'AdmissionStatus',
    'BindingKind', 'Coverage', 'DecisionTrace', 'DropCount', 'ExecutionPhase', 'ExcursionPoint',
    'ExcursionSample', 'ExcursionSegment', 'ExcursionSeries', 'FillMetric',
    'GapReason', 'IncidentMirror', 'LifecycleSample', 'LifecycleSeries', 'ObservationError',
    'ObservationKind', 'ObservationRecord', 'QueueCounters', 'QueueState',
    'ReasonAlternative', 'SegmentTransition', 'SegmentTransitionKind',
    'SequenceGap', 'Severity', 'TypedBinding', 'WriteStatus', 'WriterOutcome',
    'acknowledge', 'admit', 'canonical_bytes', 'canonical_sha256', 'close_queue',
    'declare_queue_gap', 'make_observation', 'measure_fill',
    'mirror_durable_incident', 'new_queue', 'observe_excursion',
    'observe_excursion_gap', 'observe_lifecycle', 'observe_lifecycle_gap',
    'trace_decision', 'trace_plan',
]
