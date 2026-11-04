"""NC-08 frozen contract matrix and bounded pure-kernel evidence."""
from __future__ import annotations

import ast
import dataclasses
import hashlib
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext
from pathlib import Path

import pytest

from newcore.domain.orders import (
    IntentState,
    OrderIntent,
    OrderType,
    OwnerKind,
    Purpose,
    Side,
)
from newcore.domain.reasons import ReasonCode
from newcore.management import (
    CleanupAction,
    CloseAction,
    FlattenRequired,
    IncreaseKind,
    ManagementPlan,
    PathOrder,
    PositionSide,
    ProtectionRepair,
    ReduceAction,
    RiskIncreaseProposal,
    StopChange,
)
from newcore.simulator import (
    CostModel,
    DatasetManifest,
    EventKind,
    FillEvidence,
    FundingEvent,
    FundingModel,
    Liquidity,
    PathObservation,
    ReplayEvent,
    ReplayReason,
    ReplayResult,
    ReplayStatus,
    SimulationInputError,
    SimulationMode,
    TranslationContext,
    TranslationStatus,
    build_result,
    calculate_costs,
    calculate_funding,
    canonical_bytes,
    causal_prefix,
    dataset_matches,
    resolve_paths,
    summarize_fills,
    translate_action,
)


@dataclass(frozen=True, slots=True)
class ContractCase:
    case_id: str
    category: str
    requirement: str


CASES = (
    ContractCase('PARITY-01', 'parity', 'NC-01 intent translation bytes match in all modes'),
    ContractCase('PARITY-02', 'parity', 'exact NC-07 action objects have no backtest DTO'),
    ContractCase('AUTH-01', 'authority', 'risk increase needs fresh NC-06 authorization'),
    ContractCase('DATA-01', 'dataset', 'manifest versions and SHA-256 pin every input/model'),
    ContractCase('DATA-02', 'dataset', 'mutation, gaps, duplicates and forming bars fail closed'),
    ContractCase('CAUSAL-01', 'causality', 'future evidence is invisible at the decision cursor'),
    ContractCase('PATH-01', 'path', 'unordered stop/target paths remain ambiguous'),
    ContractCase('PATH-02', 'path', 'explicit ordered path replays deterministically'),
    ContractCase('FILL-01', 'fill', 'partial fills apply exact ordered quantity evidence'),
    ContractCase('FILL-02', 'fill', 'crossed price and not-found never imply a fill'),
    ContractCase('COST-01', 'cost', 'maker/taker fees and slippage accrue per partial fill'),
    ContractCase('COST-02', 'cost', 'missing cost coverage makes result incomplete'),
    ContractCase('FUND-01', 'funding', 'timestamped funding applies only while held'),
    ContractCase('FUND-02', 'funding', 'missing funding coverage makes result incomplete'),
    ContractCase('DECIMAL-01', 'numeric', 'all trading values are exact finite Decimal'),
    ContractCase('REPLAY-01', 'determinism', 'same canonical inputs produce same result bytes'),
    ContractCase('REPLAY-02', 'determinism', 'explicit sequence, never timestamp, breaks ties'),
    ContractCase('RESULT-01', 'result', 'closed result retains complete provenance'),
    ContractCase('BOUNDARY-01', 'boundary', 'core owns no network, clock, files or runtime'),
    ContractCase('IMPORT-01', 'boundary', 'core has no legacy/runtime dependency'),
)

NC07_ACTION_TYPES = (
    ProtectionRepair,
    CloseAction,
    ReduceAction,
    StopChange,
    CleanupAction,
    FlattenRequired,
    RiskIncreaseProposal,
)

REQUIRED_CATEGORIES = frozenset({
    'parity', 'authority', 'dataset', 'causality', 'path', 'fill', 'cost',
    'funding', 'numeric', 'determinism', 'result', 'boundary',
})


def test_contract_case_ids_are_unique_and_stable():
    ids = tuple(case.case_id for case in CASES)
    assert len(ids) == len(set(ids)) == 20
    assert ids == (
        'PARITY-01', 'PARITY-02', 'AUTH-01', 'DATA-01', 'DATA-02', 'CAUSAL-01',
        'PATH-01', 'PATH-02', 'FILL-01', 'FILL-02', 'COST-01', 'COST-02',
        'FUND-01', 'FUND-02', 'DECIMAL-01', 'REPLAY-01', 'REPLAY-02',
        'RESULT-01', 'BOUNDARY-01', 'IMPORT-01',
    )


def test_contract_matrix_covers_every_required_boundary():
    assert {case.category for case in CASES} == REQUIRED_CATEGORIES
    assert all(case.requirement and type(case.requirement) is str for case in CASES)


def test_contract_uses_real_nc01_and_nc07_object_families():
    assert OrderIntent.__module__ == 'newcore.domain.orders'
    assert ManagementPlan.__module__ == 'newcore.management'
    assert all(action.__module__ == 'newcore.management' for action in NC07_ACTION_TYPES)
    assert RiskIncreaseProposal in NC07_ACTION_TYPES
    assert all(action.__name__ != 'BacktestAction' for action in NC07_ACTION_TYPES)


def test_document_tracks_every_executable_case_id():
    contract = (Path(__file__).parents[2] / 'docs' / 'newcore' / 'NC08_CONTRACT.md').read_text(
        encoding='utf-8')
    assert all(case.case_id in contract for case in CASES)
    for phrase in ('LIVE', 'REPLAY', 'BACKTEST', 'SHA-256', 'AMBIGUOUS',
                   'INCOMPLETE', 'RiskIncreaseProposal', 'NC-06', 'funding'):
        assert phrase in contract


D = Decimal
T0 = 1_791_400_000_000
PAYLOAD = b'nc08-closed-dataset-v1'


def _closed(cls, **values):
    row = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(row, name, value)
    return row


def _intent() -> OrderIntent:
    return OrderIntent(
        intent_id='int_' + '1' * 32,
        account_id='acct_' + '2' * 32,
        decision_id='dec_' + '3' * 32,
        client_order_id='nc08-intent-0001',
        purpose=Purpose.CLOSE,
        order_type=OrderType.MARKET,
        state=IntentState.PLANNED,
        symbol='BTCUSDT',
        side=Side.LONG,
        qty=D('1'),
        reason=ReasonCode.EXIT_TIME,
        created_at_ms=T0,
        owner_id='lot_' + '4' * 32,
        owner_kind=OwnerKind.LOT,
        slot_id=None,
        price=None,
        stop_price=None,
        arm=None,
        alt_client_order_id=None,
        seen_qty=None,
        authorized_by=None,
        replaces_intent_id=None,
    )


def _context(reason=ReasonCode.EXIT_TIME, *, cancel_intent_id=None) -> TranslationContext:
    return TranslationContext(
        account_id='acct_' + '2' * 32,
        symbol='BTCUSDT',
        side=Side.LONG,
        owner_id='lot_' + '4' * 32,
        owner_kind=OwnerKind.LOT,
        intent_id='int_' + '5' * 32,
        decision_id='dec_' + '6' * 32,
        client_order_id='nc08-translated-0001',
        created_at_ms=T0,
        reason=reason,
        position_quantity=D('10'),
        cancel_intent_id=cancel_intent_id,
        instrument_rules_version='rules-v1',
        input_generations=(1, 2, 3),
        nc06_authorization_id=None,
    )


def _manifest(payload=PAYLOAD) -> DatasetManifest:
    return DatasetManifest(
        schema_version=1,
        dataset_id='dataset-btc-1m',
        dataset_version='v1',
        content_sha256=hashlib.sha256(payload).hexdigest(),
        source_class='closed-bars',
        symbol='BTCUSDT',
        timeframe_ms=60_000,
        start_ms=T0,
        end_ms=T0 + 120_000,
        record_count=3,
        ordering_version='sequence-v1',
        fill_model_version='fill-v1',
        path_model_version='path-v1',
        cost_model_version='cost-v1',
        funding_model_version='funding-v1',
        instrument_rules_version='rules-v1',
        translator_version='translator-v1',
    )


def _close_action():
    return _closed(CloseAction, quantity=D('10'))


def _risk_proposal():
    return _closed(
        RiskIncreaseProposal,
        kind=IncreaseKind.DCA,
        level_id='dca-1',
        requested_risk=D('0.25'),
        policy_generation=7,
        requires_nc06_authorization=True,
    )


def _ambiguous_path():
    return resolve_paths(PathObservation(
        side=PositionSide.LONG,
        high=D('110'),
        low=D('90'),
        close=D('100'),
        stop_price=D('95'),
        target_price=D('105'),
        order=PathOrder.UNORDERED,
    ))


def _single_path():
    return resolve_paths(PathObservation(
        side=PositionSide.LONG,
        high=D('103'),
        low=D('97'),
        close=D('100'),
        stop_price=D('95'),
        target_price=D('110'),
        order=PathOrder.NONE,
    ))


def _fills():
    return (
        FillEvidence(sequence=1, at_ms=T0 + 1, known=True, quantity=D('4'),
                     price=D('100'), liquidity=Liquidity.MAKER),
        FillEvidence(sequence=2, at_ms=T0 + 2, known=True, quantity=D('6'),
                     price=D('101'), liquidity=Liquidity.TAKER),
    )


def _costs():
    return calculate_costs(_fills(), CostModel(
        model_id='binance-usdm', version='cost-v1', settlement_asset='USDT',
        maker_rate=D('0.001'),
        taker_rate=D('0.002'), slippage_bps=D('10')))


def _funding(side=Side.LONG):
    events = (
        FundingEvent(sequence=1, at_ms=T0 - 1, rate=D('0.001'), mark_price=D('100')),
        FundingEvent(sequence=2, at_ms=T0 + 10, rate=D('0.001'), mark_price=D('100')),
        FundingEvent(sequence=3, at_ms=T0 + 30, rate=D('0.001'), mark_price=D('100')),
    )
    model = FundingModel(model_id='binance-usdm', version='funding-v1',
                         settlement_asset='USDT', convention='long-pays-positive')
    return calculate_funding(side, D('10'), events, opened_at_ms=T0,
                             closed_at_ms=T0 + 20, model=model)


def _complete_result():
    return build_result(
        manifest=_manifest(), action=_intent(), path=_single_path(),
        fills=summarize_fills(D('10'), _fills()), costs=_costs(), funding=_funding(),
        gross_pnl=D('10'))


def _parity_01():
    intent = _intent()
    outcomes = tuple(translate_action(intent, None, mode) for mode in SimulationMode)
    assert all(row.status is TranslationStatus.READY and row.intent is intent for row in outcomes)
    assert len({canonical_bytes(row) for row in outcomes}) == 1
    assert canonical_bytes(outcomes[0].intent) == canonical_bytes(intent)


def _parity_02():
    action_contexts = (
        (_closed(ProtectionRepair, stop_price=D('95'), quantity=D('10'),
                 place_before_cancel=True), _context(ReasonCode.PROTECT_PLACE)),
        (_close_action(), _context(ReasonCode.EXIT_TIME)),
        (_closed(ReduceAction, level_id='tp1', target_price=D('105'), quantity=D('4'),
                 remaining_quantity=D('6')), _context(ReasonCode.EXIT_TP1)),
        (_closed(StopChange, stop_price=D('99'), replaces_stop=D('95'),
                 place_before_cancel=True), _context(ReasonCode.PROTECT_REPLACE)),
        (_closed(CleanupAction, cancel_protection=True), _context()),
        (_closed(FlattenRequired, quantity=D('10')), _context(ReasonCode.EXIT_TIME)),
        (_risk_proposal(), _context()),
    )
    for action, context in action_contexts:
        outcomes = tuple(translate_action(action, context, mode) for mode in SimulationMode)
        assert len({canonical_bytes(row) for row in outcomes}) == 1
        assert all(row.action_sha256 and row.context_sha256 for row in outcomes)
        assert type(action) in NC07_ACTION_TYPES
    assert not hasattr(__import__('newcore.simulator', fromlist=['x']), 'BacktestAction')


def _auth_01():
    outcomes = tuple(translate_action(_risk_proposal(), _context(), mode)
                     for mode in SimulationMode)
    assert all(row.status is TranslationStatus.AUTHORIZATION_REQUIRED for row in outcomes)
    assert all(row.intent is None and row.reason is ReplayReason.AUTHORIZATION_REQUIRED
               for row in outcomes)


def _data_01():
    manifest = _manifest()
    assert dataset_matches(manifest, PAYLOAD)
    assert not dataset_matches(manifest, PAYLOAD + b'-mutated')
    assert canonical_bytes(manifest) == canonical_bytes(_manifest())
    assert all(getattr(manifest, name) for name in (
        'ordering_version', 'fill_model_version', 'path_model_version',
        'cost_model_version', 'funding_model_version', 'instrument_rules_version',
        'translator_version'))


def _data_02():
    with pytest.raises(SimulationInputError, match='events.sequence'):
        causal_prefix((ReplayEvent(sequence=2, evidence_at_ms=T0, available_at_ms=T0,
                                   kind=EventKind.MARKET, price=D('100')),), T0)
    with pytest.raises(SimulationInputError, match='events.sequence'):
        causal_prefix((
            ReplayEvent(sequence=1, evidence_at_ms=T0, available_at_ms=T0,
                        kind=EventKind.MARKET, price=D('100')),
            ReplayEvent(sequence=1, evidence_at_ms=T0, available_at_ms=T0,
                        kind=EventKind.MARKET, price=D('100')),
        ), T0)
    assert not dataset_matches(_manifest(), b'forming-or-mutated-input')


def _causal_01():
    events = (
        ReplayEvent(sequence=1, evidence_at_ms=T0, available_at_ms=T0,
                    kind=EventKind.MARKET, price=D('100')),
        ReplayEvent(sequence=2, evidence_at_ms=T0 + 1, available_at_ms=T0 + 10,
                    kind=EventKind.MARKET, price=D('101')),
    )
    assert tuple(row.sequence for row in causal_prefix(events, T0 + 5)) == (1,)


def _path_01():
    resolution = _ambiguous_path()
    assert resolution.status is ReplayStatus.AMBIGUOUS
    assert tuple(row.order for row in resolution.branches) == (
        PathOrder.STOP_FIRST, PathOrder.TARGET_FIRST)
    result = build_result(manifest=_manifest(), action=_intent(), path=resolution,
                          fills=summarize_fills(D('10'), _fills()), costs=_costs(),
                          funding=_funding(), gross_pnl=D('999'))
    assert result.status is ReplayStatus.AMBIGUOUS
    assert result.gross_pnl is result.net_pnl is None
    assert not hasattr(result, 'acceptance_metric')
    short = resolve_paths(PathObservation(
        side=PositionSide.SHORT, high=D('110'), low=D('90'), close=D('100'),
        stop_price=D('105'), target_price=D('95'), order=PathOrder.UNORDERED))
    assert short.status is ReplayStatus.AMBIGUOUS
    assert short.branches == resolution.branches


def _path_02():
    observation = PathObservation(
        side=PositionSide.LONG, high=D('110'), low=D('90'), close=D('100'),
        stop_price=D('95'), target_price=D('105'), order=PathOrder.TARGET_FIRST)
    assert resolve_paths(observation).branches == resolve_paths(observation).branches
    assert resolve_paths(observation).branches[0].events == ('target', 'stop')


def _fill_01():
    summary = summarize_fills(D('10'), _fills())
    assert summary.status is ReplayStatus.COMPLETE
    assert summary.filled_quantity == D('10') and summary.remaining_quantity == D('0')
    assert summary.average_price == D('100.6')
    assert tuple(row.sequence for row in summary.fills) == (1, 2)


def _fill_02():
    assert summarize_fills(D('10'), ()).status is ReplayStatus.INCOMPLETE
    unknown = FillEvidence(sequence=1, at_ms=T0, known=False, quantity=D('0'),
                           price=None, liquidity=None)
    assert summarize_fills(D('10'), (unknown,)).status is ReplayStatus.INCOMPLETE
    assert _ambiguous_path().branches and not hasattr(_ambiguous_path(), 'fills')


def _cost_01():
    costs = _costs()
    assert costs.fee == D('1.612')
    assert costs.slippage == D('1.006')


def _cost_02():
    result = build_result(manifest=_manifest(), action=_intent(), path=_single_path(),
                          fills=summarize_fills(D('10'), _fills()), costs=None,
                          funding=_funding(), gross_pnl=D('10'))
    assert result.status is ReplayStatus.INCOMPLETE and result.net_pnl is None
    assert ReplayReason.COST_COVERAGE_MISSING in result.reasons


def _fund_01():
    assert _funding(Side.LONG).payments == (D('-1.000'),)
    assert _funding(Side.LONG).total == D('-1.000')
    assert _funding(Side.SHORT).total == D('1.000')


def _fund_02():
    result = build_result(manifest=_manifest(), action=_intent(), path=_single_path(),
                          fills=summarize_fills(D('10'), _fills()), costs=_costs(),
                          funding=None, gross_pnl=D('10'))
    assert result.status is ReplayStatus.INCOMPLETE and result.net_pnl is None
    assert ReplayReason.FUNDING_COVERAGE_MISSING in result.reasons


def _decimal_01():
    for bad in (1, 1.0, D('NaN'), D('Infinity')):
        with pytest.raises(SimulationInputError):
            FillEvidence(sequence=1, at_ms=T0, known=True, quantity=bad,
                         price=D('100'), liquidity=Liquidity.MAKER)
    assert canonical_bytes(CostModel(model_id='m', version='v', settlement_asset='USDT',
                                     maker_rate=D('0.010'), taker_rate=D('0'),
                                     slippage_bps=D('0'))) == canonical_bytes(
        CostModel(model_id='m', version='v', settlement_asset='USDT', maker_rate=D('0.01'),
                  taker_rate=D('0.0'), slippage_bps=D('0.00')))


def _replay_01():
    left, right = _complete_result(), _complete_result()
    assert canonical_bytes(left) == canonical_bytes(right)
    assert left.run_id == right.run_id
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_DOWN
        altered_process_context = _complete_result()
    assert canonical_bytes(left) == canonical_bytes(altered_process_context)


def _replay_02():
    events = tuple(ReplayEvent(sequence=i, evidence_at_ms=T0, available_at_ms=T0,
                               kind=EventKind.MARKET, price=D(str(99 + i)))
                   for i in (1, 2))
    assert tuple(row.sequence for row in causal_prefix(events, T0)) == (1, 2)
    assert tuple(row.price for row in causal_prefix(events, T0)) == (D('100'), D('101'))


def _result_01():
    result = _complete_result()
    assert result.status is ReplayStatus.COMPLETE
    assert result.net_pnl == D('6.382')
    assert result.manifest == _manifest()
    assert result.manifest_sha256 and result.action_sha256 and result.run_id.startswith('sim_')
    assert dataclasses.is_dataclass(result) and result.reasons == (ReplayReason.OK,)
    with pytest.raises(TypeError, match='factory-only'):
        ReplayResult()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.net_pnl = D('0')


def _source_tree():
    source = Path(__file__).parents[2] / 'newcore' / 'simulator.py'
    return ast.parse(source.read_text(encoding='utf-8'))


def _boundary_01():
    tree = _source_tree()
    forbidden = {'os', 'pathlib', 'random', 'socket', 'time', 'urllib', 'requests'}
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
        'newcore.domain.codec', 'newcore.domain.orders', 'newcore.domain.reasons',
        'newcore.management'}
    assert all('runtime' not in name and 'legacy' not in name for name in internal)


CASE_CHECKS = {
    'PARITY-01': _parity_01, 'PARITY-02': _parity_02, 'AUTH-01': _auth_01,
    'DATA-01': _data_01, 'DATA-02': _data_02, 'CAUSAL-01': _causal_01,
    'PATH-01': _path_01, 'PATH-02': _path_02, 'FILL-01': _fill_01,
    'FILL-02': _fill_02, 'COST-01': _cost_01, 'COST-02': _cost_02,
    'FUND-01': _fund_01, 'FUND-02': _fund_02, 'DECIMAL-01': _decimal_01,
    'REPLAY-01': _replay_01, 'REPLAY-02': _replay_02, 'RESULT-01': _result_01,
    'BOUNDARY-01': _boundary_01, 'IMPORT-01': _import_01,
}


@pytest.mark.parametrize('case', CASES, ids=lambda case: case.case_id)
def test_nc08_implementation_contract(case):
    CASE_CHECKS[case.case_id]()
