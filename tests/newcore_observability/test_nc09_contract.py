"""NC-09 frozen observability contract matrix and pure-kernel evidence."""
from __future__ import annotations

import ast
import dataclasses
import inspect
from dataclasses import dataclass
from decimal import Decimal

import pytest

import newcore.observability as observability_module

from newcore.domain.decision import Action, Authority, Decision
from newcore.domain.events import IncidentRecorded
from newcore.domain.incident import Incident
from newcore.domain.orders import Side
from newcore.domain.portfolio import Fill
from newcore.domain.reasons import ReasonCode
from newcore.management import (
    Binding,
    CloseAction,
    FlattenRequired,
    ManagementPlan,
    PlanReason,
    PlanStatus,
    ProtectionRepair,
    ReduceAction,
)
from newcore.observability import (
    DEFAULT_CAPACITY,
    MAX_CAPACITY,
    AdmissionStatus,
    BindingKind,
    Coverage,
    ExecutionPhase,
    ExcursionSample,
    FillMetric,
    GapReason,
    LifecycleSample,
    ObservationError,
    ObservationKind,
    QueueState,
    ReasonAlternative,
    SegmentTransition,
    SegmentTransitionKind,
    SequenceGap,
    Severity,
    TypedBinding,
    WriteStatus,
    acknowledge,
    admit,
    canonical_bytes,
    canonical_sha256,
    close_queue,
    declare_queue_gap,
    make_observation,
    measure_fill,
    mirror_durable_incident,
    new_queue,
    observe_excursion,
    observe_excursion_gap,
    observe_lifecycle,
    observe_lifecycle_gap,
    trace_decision,
    trace_plan,
)
from newcore.simulator import (
    FillEvidence,
    FillSummary,
    Liquidity,
    PathResolution,
    ReplayStatus,
    summarize_fills,
)


@dataclass(frozen=True, slots=True)
class ContractCase:
    case_id: str
    category: str
    requirement: str


CASES = (
    ContractCase('INC-01', 'incident', 'reuse exact NC-01 structured incident/event families'),
    ContractCase('INC-02', 'incident', 'incident detail is bounded sanitized display-only text'),
    ContractCase('DEC-01', 'decision', 'every decision records stable reason and provenance'),
    ContractCase('DEC-02', 'decision', 'management plans retain reason bindings and generations'),
    ContractCase('WHY-01', 'explanation', 'why-held is causal and distinct from close'),
    ContractCase('WHY-02', 'explanation', 'why-closed binds selected typed source reason'),
    ContractCase('FILL-01', 'fill', 'partial unknown and final fill evidence stay distinct'),
    ContractCase('SLIP-01', 'fill', 'slippage is exact directional Decimal with provenance'),
    ContractCase('MFE-01', 'excursion', 'MFE uses only ordered causally available evidence'),
    ContractCase('MAE-01', 'excursion', 'MAE is side-symmetric and coverage-labelled'),
    ContractCase('PEAK-01', 'excursion', 'lifecycle net peak is separate from price excursion'),
    ContractCase('GIVEBACK-01', 'excursion', 'peak give-back uses frozen positive denominator'),
    ContractCase('COVERAGE-01', 'excursion', 'gaps remain explicit and cannot become acceptance'),
    ContractCase('QUEUE-01', 'queue', 'admission is bounded non-waiting and performs no I/O'),
    ContractCase('QUEUE-02', 'queue', 'full or closed admission drops with typed counters'),
    ContractCase('QUEUE-03', 'queue', 'accepted evidence is never evicted or reordered'),
    ContractCase('WRITE-01', 'writer', 'failed write never fabricates persistence or metrics'),
    ContractCase('WRITE-02', 'writer', 'writer failure is sanitized and producer-independent'),
    ContractCase('PROTECT-01', 'safety', 'audit state cannot delay or alter protection actions'),
    ContractCase('ORDER-01', 'causality', 'explicit sequence and never timestamp breaks ties'),
    ContractCase('RESULT-01', 'result', 'snapshots and records are immutable and deterministic'),
    ContractCase('PRIVACY-01', 'privacy', 'no raw payload exception credential or secret retained'),
    ContractCase('BOUNDARY-01', 'boundary', 'pure core owns no clock file network thread or runtime'),
    ContractCase('IMPORT-01', 'boundary', 'pure core imports no legacy runtime or UI module'),
)

REQUIRED_CATEGORIES = frozenset({
    'incident', 'decision', 'explanation', 'fill', 'excursion', 'queue',
    'writer', 'safety', 'causality', 'result', 'privacy', 'boundary',
})

UPSTREAM_TYPES = (
    Decision,
    Incident,
    IncidentRecorded,
    Fill,
    ReasonCode,
    ManagementPlan,
    PlanReason,
    FillSummary,
    PathResolution,
)


def test_contract_case_ids_are_unique_and_stable():
    ids = tuple(case.case_id for case in CASES)
    assert len(ids) == len(set(ids)) == 24
    assert ids == (
        'INC-01', 'INC-02', 'DEC-01', 'DEC-02', 'WHY-01', 'WHY-02',
        'FILL-01', 'SLIP-01', 'MFE-01', 'MAE-01', 'PEAK-01',
        'GIVEBACK-01', 'COVERAGE-01', 'QUEUE-01', 'QUEUE-02', 'QUEUE-03',
        'WRITE-01', 'WRITE-02', 'PROTECT-01', 'ORDER-01', 'RESULT-01',
        'PRIVACY-01', 'BOUNDARY-01', 'IMPORT-01',
    )


def test_contract_matrix_covers_every_required_boundary():
    assert {case.category for case in CASES} == REQUIRED_CATEGORIES
    assert all(type(case.requirement) is str and case.requirement for case in CASES)


def test_contract_reuses_exact_accepted_upstream_object_families():
    assert tuple(row.__module__ for row in UPSTREAM_TYPES) == (
        'newcore.domain.decision',
        'newcore.domain.incident',
        'newcore.domain.events',
        'newcore.domain.portfolio',
        'newcore.domain.reasons',
        'newcore.management',
        'newcore.management',
        'newcore.simulator',
        'newcore.simulator',
    )
    assert all(row.__name__ not in {'TelemetryDecision', 'TelemetryIncident', 'AuditFill'}
               for row in UPSTREAM_TYPES)


def test_contract_requirements_are_closed_nonempty_statements():
    assert len(CASES) == 24
    assert all(type(case.requirement) is str and case.requirement.strip()
               for case in CASES)
    assert not any('TODO' in case.requirement or 'TBD' in case.requirement
                   for case in CASES)


D = Decimal
T0 = 1_791_500_000_000
ACCOUNT_ID = 'acct_' + '1' * 32
LOT_ID = 'lot_' + '2' * 32


def _closed(cls, **values):
    row = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(row, name, value)
    return row


def _decision():
    return Decision(
        decision_id='dec_' + '3' * 32,
        account_id=ACCOUNT_ID,
        at_ms=T0,
        action=Action.WAIT,
        reason=ReasonCode.EXIT_TIME,
        authority=Authority.STRATEGY,
        key=None,
        evidence=('rec_' + '4' * 32,),
        symbol='BTCUSDT',
        side=Side.LONG,
        subject_id=LOT_ID,
        detail='time exit not reached',
        intents=(),
        policy_version='policy-v1',
    )


def _plan(*, reason=PlanReason.NO_CHANGE, actions=(), bindings=(Binding.TIME,)):
    return _closed(
        ManagementPlan,
        plan_id='mgt_' + '5' * 32,
        status=PlanStatus.HOLD if not actions else PlanStatus.ACTIONS,
        reason=reason,
        lot_id=LOT_ID,
        policy_id='policy-1',
        policy_version='policy-v1',
        lot_generation=1,
        policy_generation=2,
        market_generation=3,
        protection_generation=4,
        strategy_exit_generation=5,
        pending_generation=6,
        bindings=bindings,
        actions=actions,
    )


def _incident_event():
    incident = Incident(
        incident_id='inc_' + '6' * 32,
        account_id=ACCOUNT_ID,
        kind=ReasonCode.PROTECT_UNDERSIZED,
        at_ms=T0,
        symbol='BTCUSDT',
        side=Side.LONG,
        intent_refs=('int_' + '7' * 32,),
        lot_refs=(LOT_ID,),
        position_refs=('pos_' + '8' * 32,),
        evidence=('rec_' + '9' * 32,),
        detail='stop quantity below exposure',
    )
    return IncidentRecorded(
        event_id='evt_' + 'a' * 32,
        account_id=ACCOUNT_ID,
        aggregate_id='pf_' + 'b' * 32,
        sequence=1,
        at_ms=T0 + 1,
        reason=incident.kind,
        incident=incident,
    )


def _known_fills():
    return (
        FillEvidence(sequence=1, at_ms=T0 + 1, known=True, quantity=D('4'),
                     price=D('101'), liquidity=Liquidity.TAKER),
        FillEvidence(sequence=2, at_ms=T0 + 2, known=True, quantity=D('6'),
                     price=D('102'), liquidity=Liquidity.TAKER),
    )


def _partial_unknown():
    return summarize_fills(D('10'), (
        FillEvidence(sequence=1, at_ms=T0 + 1, known=True, quantity=D('4'),
                     price=D('101'), liquidity=Liquidity.TAKER),
        FillEvidence(sequence=2, at_ms=T0 + 2, known=False, quantity=D('0'),
                     price=None, liquidity=None),
    ))


def _measure(summary, *, side=Side.LONG, phase=ExecutionPhase.ENTRY):
    return measure_fill(
        summary, reference_price=D('100'), side=side, phase=phase,
        source_id='res_' + 'c' * 32, cost_model_id='binance-usdm',
        cost_model_version='cost-v1', settlement_asset='USDT')


def _decision_record(sequence=1, *, severity=Severity.INFO):
    trace = trace_decision(_decision())
    return make_observation(
        account_id=ACCOUNT_ID, sequence=sequence, at_ms=T0 + sequence,
        kind=ObservationKind.DECISION, severity=severity, reason=trace.reason,
        source_sha256=trace.source_sha256, payload=trace)


def _sample(*, sequence, mark, side=Side.LONG, generation=1,
            segment='seg-1', coverage=Coverage.COMPLETE, quantity=D('2'),
            average=D('100'), risk=D('10')):
    return ExcursionSample(
        lot_id=LOT_ID, lot_generation=generation, segment_id=segment,
        sequence=sequence, at_ms=T0 + sequence, side=side, mark=mark,
        average_entry=average, quantity=quantity,
        risk_basis_id=f'risk-{generation}', accepted_risk=risk,
        coverage=coverage)


def _source_binding():
    return TypedBinding(kind=BindingKind.RECORD_ID,
                        record_id='dec_' + 'd' * 32)


def _gap(stream_id, *, after, through, at_ms=None):
    return SequenceGap(
        stream_id=stream_id, after_sequence=after, missing_from=after + 1,
        missing_through=through, resume_sequence=through + 1,
        observed_at_ms=T0 + through if at_ms is None else at_ms,
        reason=GapReason.OUTAGE, source=_source_binding())


def _transition(*, sequence, from_segment='seg-1', to_segment='seg-2',
                from_generation=1, to_generation=1, kind=SegmentTransitionKind.ADD):
    return SegmentTransition(
        lot_id=LOT_ID, kind=kind, from_segment_id=from_segment,
        to_segment_id=to_segment, from_generation=from_generation,
        to_generation=to_generation, sequence=sequence, source=_source_binding())


def _lifecycle(sequence, *, net, closed=False):
    sample = LifecycleSample(
        stream_id=LOT_ID, sequence=sequence, at_ms=T0 + sequence,
        realized_pnl=net, open_pnl=D('0'), fees=D('0'), slippage=D('0'),
        funding=D('0'), modeled_exit_cost=D('0'), coverage=Coverage.COMPLETE)
    return sample, closed


def _inc_01():
    event = _incident_event()
    mirror = mirror_durable_incident(event)
    assert mirror.incident_id == event.incident.incident_id
    assert mirror.reason is ReasonCode.PROTECT_UNDERSIZED
    assert mirror.source_sha256


def _inc_02():
    with pytest.raises(Exception):
        Incident(
            incident_id='inc_' + '6' * 32, account_id=ACCOUNT_ID,
            kind=ReasonCode.PROTECT_UNDERSIZED, at_ms=T0, symbol=None, side=None,
            intent_refs=(), lot_refs=(), position_refs=(), evidence=(),
            detail='secret-' + 'x' * 30)
    mirror = mirror_durable_incident(_incident_event())
    assert 'secret' not in (mirror.detail or '') and len(mirror.detail) <= 160


def _dec_01():
    trace = trace_decision(_decision())
    assert trace.reason is ReasonCode.EXIT_TIME
    assert trace.bindings == ('rec_' + '4' * 32,)
    assert trace.policy_version == 'policy-v1' and trace.source_sha256
    with pytest.raises(ObservationError, match='record.trace_binding'):
        make_observation(
            account_id=ACCOUNT_ID, sequence=1, at_ms=T0,
            kind=ObservationKind.DECISION, severity=Severity.INFO,
            reason=ReasonCode.EXIT_MANUAL, source_sha256=trace.source_sha256,
            payload=trace)


def _dec_02():
    trace = trace_plan(_plan(), ObservationKind.WHY_HELD)
    assert trace.reason is PlanReason.NO_CHANGE
    assert trace.bindings == ('time',)
    assert trace.generations == (1, 2, 3, 4, 5, 6)


def _why_01():
    trace = trace_plan(_plan(), ObservationKind.WHY_HELD)
    assert trace.kind is ObservationKind.WHY_HELD
    assert trace.reason is PlanReason.NO_CHANGE


def _why_02():
    close = _closed(CloseAction, quantity=D('2'))
    alternative = ReasonAlternative(
        reason=PlanReason.TRAIL, source_sha256='1' * 64,
        bindings=(
            TypedBinding(kind=BindingKind.MANAGEMENT, management=Binding.TRAIL),
            TypedBinding(kind=BindingKind.GENERATION, generation=3),
        ))
    trace = trace_plan(
        _plan(reason=PlanReason.TIME_EXIT, actions=(close,)),
        ObservationKind.WHY_CLOSED, alternatives=(alternative,))
    assert trace.kind is ObservationKind.WHY_CLOSED
    assert trace.reason is PlanReason.TIME_EXIT
    assert trace.alternatives == (alternative,)


def _fill_01():
    metric = _measure(_partial_unknown())
    assert isinstance(metric, FillMetric)
    assert metric.coverage is Coverage.INCOMPLETE
    assert (metric.filled_quantity, metric.remaining_quantity) == (D('4'), D('6'))
    assert metric.average_price == D('101')
    assert metric.evidence[-1].known is False
    assert (metric.cost_model_id, metric.cost_model_version, metric.settlement_asset) == (
        'binance-usdm', 'cost-v1', 'USDT')
    forged = dataclasses.replace(_partial_unknown(), status=ReplayStatus.COMPLETE)
    with pytest.raises(ObservationError, match='fill.summary_mismatch'):
        _measure(forged)


def _slip_01():
    summary = summarize_fills(D('10'), _known_fills())
    long_entry = _measure(summary)
    long_exit = _measure(summary, phase=ExecutionPhase.EXIT)
    short_entry = _measure(summary, side=Side.SHORT)
    assert long_entry.slippage_price == D('1.6')
    assert long_entry.slippage_amount == D('16.0')
    assert long_entry.slippage_bps == D('160.000')
    assert long_exit.slippage_price == short_entry.slippage_price == D('-1.6')


def _mfe_01():
    long = observe_excursion(None, _sample(sequence=1, mark=D('110')))
    short = observe_excursion(None, _sample(sequence=1, mark=D('90'), side=Side.SHORT))
    for series in (long, short):
        point = series.segments[0].mfe
        assert (point.price, point.amount, point.percent, point.r_multiple) == (
            D('10'), D('20'), D('10.0'), D('2'))


def _mae_01():
    long = observe_excursion(None, _sample(sequence=1, mark=D('90')))
    short = observe_excursion(None, _sample(sequence=1, mark=D('110'), side=Side.SHORT))
    assert long.segments[0].mae == short.segments[0].mae


def _peak_01():
    excursion = observe_excursion(None, _sample(sequence=1, mark=D('110')))
    sample, closed = _lifecycle(1, net=D('7'))
    lifecycle = observe_lifecycle(None, sample, closed=closed)
    assert excursion.segments[0].mfe.amount == D('20')
    assert lifecycle.peak_net == D('7')
    assert type(excursion).__name__ != type(lifecycle).__name__


def _giveback_01():
    first, _ = _lifecycle(1, net=D('10'))
    series = observe_lifecycle(None, first)
    final, _ = _lifecycle(2, net=D('4'))
    closed = observe_lifecycle(series, final, closed=True)
    assert (closed.giveback, closed.giveback_percent) == (D('6'), D('60'))
    loss, _ = _lifecycle(1, net=D('-2'))
    loss_series = observe_lifecycle(None, loss)
    loss_final, _ = _lifecycle(2, net=D('-3'))
    loss_closed = observe_lifecycle(loss_series, loss_final, closed=True)
    assert loss_closed.giveback == D('1') and loss_closed.giveback_percent is None


def _coverage_01():
    series = observe_excursion(None, _sample(sequence=1, mark=D('100')))
    with_gap = observe_excursion_gap(series, _gap(LOT_ID, after=1, through=2))
    assert with_gap.segments[0].gap_count == 1
    assert with_gap.segments[0].gaps[0].missing_from == 2
    next_generation = observe_excursion(
        with_gap,
        _sample(sequence=3, mark=D('110'), generation=2, segment='seg-2',
                quantity=D('3'), risk=D('12')),
        transition=_transition(sequence=3, from_generation=1, to_generation=2))
    assert len(next_generation.segments) == 2
    assert next_generation.segments[0] == with_gap.segments[0]


def _queue_01():
    state = new_queue(ACCOUNT_ID)
    assert state.capacity == DEFAULT_CAPACITY and MAX_CAPACITY == 65536
    assert new_queue(ACCOUNT_ID, MAX_CAPACITY).capacity == MAX_CAPACITY
    for bad in (0, 65537, True):
        with pytest.raises(ObservationError, match='queue.capacity'):
            new_queue(ACCOUNT_ID, bad)
    state, outcome = admit(state, _decision_record())
    assert outcome.status is AdmissionStatus.ACCEPTED
    assert len(state.queued) == 1 and state.counters.accepted == 1


def _queue_02():
    state, first = admit(new_queue(ACCOUNT_ID, 1), _decision_record(1))
    state, second = admit(state, _decision_record(2, severity=Severity.CRITICAL))
    assert first.status is AdmissionStatus.ACCEPTED
    assert second.status is AdmissionStatus.DROPPED_FULL
    assert state.queued[0].sequence == 1 and state.counters.dropped_full == 1
    assert state.counters.drops[0].severity is Severity.CRITICAL
    state = close_queue(state)
    state, third = admit(state, _decision_record(3))
    assert third.status is AdmissionStatus.DROPPED_CLOSED
    assert state.counters.dropped_closed == 1


def _queue_03():
    first = _decision_record(1)
    state, _ = admit(new_queue(ACCOUNT_ID, 1), first)
    state, _ = admit(state, _decision_record(2))
    assert state.queued == (first,)
    state, outcome = acknowledge(state, first.observation_id, persisted=True)
    assert outcome.status is WriteStatus.PERSISTED and state.queued == ()
    state, accepted = admit(state, _decision_record(3))
    assert accepted.status is AdmissionStatus.ACCEPTED


def _write_01():
    record = _decision_record(1)
    state, _ = admit(new_queue(ACCOUNT_ID), record)
    state, outcome = acknowledge(state, record.observation_id, persisted=False)
    assert outcome.status is WriteStatus.WRITE_FAILED
    assert state.counters.write_errors == 1 and state.counters.persisted == 0


def _write_02():
    record = _decision_record(1)
    state, _ = admit(new_queue(ACCOUNT_ID), record)
    same, outcome = acknowledge(state, 'not-the-head', persisted=False)
    assert same == state and outcome.status is WriteStatus.INVALID
    assert not hasattr(outcome, 'exception') and record in state.queued


def _protect_01():
    action = _closed(ProtectionRepair, stop_price=D('95'), quantity=D('2'),
                     place_before_cancel=True)
    before = canonical_bytes(action)
    state = new_queue(ACCOUNT_ID, 1)
    state, _ = admit(state, _decision_record(1))
    state, _ = admit(state, _decision_record(2))
    assert canonical_bytes(action) == before
    assert action.place_before_cancel is True


def _order_01():
    first = _decision_record(1)
    trace = trace_decision(_decision())
    second = make_observation(
        account_id=ACCOUNT_ID, sequence=2, at_ms=first.at_ms,
        kind=ObservationKind.DECISION, severity=Severity.INFO,
        reason=trace.reason, source_sha256=trace.source_sha256, payload=trace)
    state, _ = admit(new_queue(ACCOUNT_ID), first)
    state, accepted = admit(state, second)
    assert accepted.status is AdmissionStatus.ACCEPTED
    state, refused = admit(state, _decision_record(1))
    assert refused.status is AdmissionStatus.INVALID
    assert tuple(row.sequence for row in state.queued) == (1, 2)


def _result_01():
    left, right = _decision_record(), _decision_record()
    assert canonical_bytes(left) == canonical_bytes(right)
    assert canonical_sha256(left) == canonical_sha256(right)
    state = new_queue(ACCOUNT_ID)
    with pytest.raises(TypeError, match='factory-only'):
        QueueState()
    with pytest.raises(dataclasses.FrozenInstanceError):
        left.sequence = 9
    state, accepted = admit(state, left)
    assert accepted.status is AdmissionStatus.ACCEPTED
    retained = canonical_bytes(state.queued[0])
    assert state.queued[0] is not left
    object.__setattr__(left, 'payload_bytes', b'forged-after-admission')
    assert canonical_bytes(state.queued[0]) == retained


def _privacy_01():
    trace = trace_decision(_decision())
    with pytest.raises(ObservationError, match='record.payload'):
        make_observation(
            account_id=ACCOUNT_ID, sequence=1, at_ms=T0,
            kind=ObservationKind.DECISION, severity=Severity.ERROR,
            reason=trace.reason, source_sha256=trace.source_sha256,
            payload=RuntimeError('api-key-secret'))


def _source_tree():
    return ast.parse(inspect.getsource(observability_module))


def _boundary_01():
    tree = _source_tree()
    forbidden = {'os', 'pathlib', 'queue', 'random', 'socket', 'threading', 'time',
                 'urllib', 'requests'}
    imports = {alias.name.split('.')[0] for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names}
    imports |= {node.module.split('.')[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module}
    assert imports.isdisjoint(forbidden)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == 'open' for node in ast.walk(tree))


def _import_01():
    tree = _source_tree()
    internal = {node.module for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
                and node.module.startswith('newcore')}
    assert internal <= {
        'newcore.domain.codec', 'newcore.domain.decision', 'newcore.domain.events',
        'newcore.domain.incident', 'newcore.domain.orders',
        'newcore.domain.reasons', 'newcore.management', 'newcore.simulator'}
    assert all(not any(word in name for word in ('runtime', 'legacy', 'ui'))
               for name in internal)


def test_sequence_jumps_require_exact_explicit_gap_transitions():
    state, _ = admit(new_queue(ACCOUNT_ID), _decision_record(1))
    state_after_refusal, refused = admit(state, _decision_record(3))
    assert refused.status is AdmissionStatus.INVALID
    assert state_after_refusal.last_sequence == 1
    state = declare_queue_gap(state, _gap(ACCOUNT_ID, after=1, through=2))
    state, accepted = admit(state, _decision_record(3))
    assert accepted.status is AdmissionStatus.ACCEPTED
    assert state.gaps[-1].missing_from == state.gaps[-1].missing_through == 2

    excursion = observe_excursion(None, _sample(sequence=1, mark=D('100')))
    with pytest.raises(ObservationError, match='excursion.order'):
        observe_excursion(excursion, _sample(sequence=3, mark=D('101')))
    excursion = observe_excursion_gap(excursion, _gap(LOT_ID, after=1, through=2))
    assert observe_excursion(
        excursion, _sample(sequence=3, mark=D('101'))).segments[-1].last_sequence == 3

    first, _ = _lifecycle(1, net=D('1'))
    lifecycle = observe_lifecycle(None, first)
    third, _ = _lifecycle(3, net=D('2'))
    with pytest.raises(ObservationError, match='lifecycle.order'):
        observe_lifecycle(lifecycle, third)
    lifecycle = observe_lifecycle_gap(lifecycle, _gap(LOT_ID, after=1, through=2))
    assert observe_lifecycle(lifecycle, third).latest_sequence == 3


def test_same_generation_add_or_partial_requires_explicit_segment_transition():
    series = observe_excursion(None, _sample(sequence=1, mark=D('101')))
    changed = _sample(sequence=2, mark=D('102'), segment='seg-2', quantity=D('3'))
    with pytest.raises(ObservationError, match='segment.transition'):
        observe_excursion(series, changed)
    advanced = observe_excursion(
        series, changed, transition=_transition(sequence=2,
                                                kind=SegmentTransitionKind.ADD))
    assert len(advanced.segments) == 2
    assert advanced.segments[0] == series.segments[0]
    assert advanced.segments[1].transition.kind is SegmentTransitionKind.ADD
    partial_sample = _sample(
        sequence=3, mark=D('103'), segment='seg-3', quantity=D('1'))
    partial = observe_excursion(
        advanced, partial_sample,
        transition=_transition(
            sequence=3, from_segment='seg-2', to_segment='seg-3',
            kind=SegmentTransitionKind.PARTIAL))
    assert partial.segments[-1].transition.kind is SegmentTransitionKind.PARTIAL
    assert partial.segments[-2] == advanced.segments[-1]


def test_excursion_updates_deep_seal_all_prior_history():
    original = observe_excursion(None, _sample(sequence=1, mark=D('101')))
    updated = observe_excursion(original, _sample(sequence=2, mark=D('102')))
    frozen_bytes = canonical_bytes(updated)
    assert updated.segments[0] is not original.segments[0]
    assert updated.segments[0].mfe is not original.segments[0].mfe
    object.__setattr__(original.segments[0].mfe, 'price', D('999'))
    object.__setattr__(original.segments[0], 'segment_id', 'mutated-source')
    assert canonical_bytes(updated) == frozen_bytes

    transition = _transition(sequence=3)
    segmented = observe_excursion(
        updated, _sample(sequence=3, mark=D('103'), segment='seg-2', quantity=D('3')),
        transition=transition)
    segmented_bytes = canonical_bytes(segmented)
    object.__setattr__(transition, 'to_segment_id', 'caller-mutated')
    object.__setattr__(updated.segments[0].mae, 'amount', D('777'))
    assert canonical_bytes(segmented) == segmented_bytes


def test_why_held_and_closed_refuse_semantic_mismatch():
    close = _closed(CloseAction, quantity=D('2'))
    exiting = _plan(reason=PlanReason.TIME_EXIT, actions=(close,))
    with pytest.raises(ObservationError, match='trace.held_semantics'):
        trace_plan(exiting, ObservationKind.WHY_HELD)
    with pytest.raises(ObservationError, match='trace.closed_semantics'):
        trace_plan(_plan(), ObservationKind.WHY_CLOSED)
    reduce = _closed(
        ReduceAction, level_id='tp1', target_price=D('105'), quantity=D('1'),
        remaining_quantity=D('1'))
    with pytest.raises(ObservationError, match='trace.closed_semantics'):
        trace_plan(_plan(reason=PlanReason.TARGET_REDUCE, actions=(reduce,)),
                   ObservationKind.WHY_CLOSED)
    flatten = _closed(FlattenRequired, quantity=D('2'))
    with pytest.raises(ObservationError, match='trace.closed_semantics'):
        trace_plan(_plan(reason=PlanReason.FLATTEN_REQUIRED, actions=(flatten,)),
                   ObservationKind.WHY_CLOSED)


def test_alternative_bindings_are_closed_typed_and_privacy_safe():
    for raw in ('https://example.invalid/a', 'api-key-secret-value', 'free form payload'):
        with pytest.raises(ObservationError, match='binding.record_id'):
            TypedBinding(kind=BindingKind.RECORD_ID, record_id=raw)
    with pytest.raises(ObservationError, match='alternative.bindings'):
        ReasonAlternative(reason=PlanReason.TRAIL, source_sha256='1' * 64,
                          bindings=('https://example.invalid/a',))


CASE_CHECKS = {
    'INC-01': _inc_01, 'INC-02': _inc_02, 'DEC-01': _dec_01,
    'DEC-02': _dec_02, 'WHY-01': _why_01, 'WHY-02': _why_02,
    'FILL-01': _fill_01, 'SLIP-01': _slip_01, 'MFE-01': _mfe_01,
    'MAE-01': _mae_01, 'PEAK-01': _peak_01, 'GIVEBACK-01': _giveback_01,
    'COVERAGE-01': _coverage_01, 'QUEUE-01': _queue_01,
    'QUEUE-02': _queue_02, 'QUEUE-03': _queue_03, 'WRITE-01': _write_01,
    'WRITE-02': _write_02, 'PROTECT-01': _protect_01, 'ORDER-01': _order_01,
    'RESULT-01': _result_01, 'PRIVACY-01': _privacy_01,
    'BOUNDARY-01': _boundary_01, 'IMPORT-01': _import_01,
}


@pytest.mark.parametrize('case', CASES, ids=lambda case: case.case_id)
def test_nc09_implementation_contract(case):
    CASE_CHECKS[case.case_id]()
