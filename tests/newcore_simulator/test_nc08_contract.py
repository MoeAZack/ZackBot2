"""NC-08 contract matrix only; simulator implementation is intentionally deferred."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from newcore.domain.orders import OrderIntent
from newcore.management import (
    CleanupAction,
    CloseAction,
    FlattenRequired,
    ManagementPlan,
    ProtectionRepair,
    ReduceAction,
    RiskIncreaseProposal,
    StopChange,
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


@pytest.mark.skip(reason='NC-08 implementation is intentionally deferred on the contract-only branch')
@pytest.mark.parametrize('case', CASES, ids=lambda case: case.case_id)
def test_nc08_implementation_contract(case):
    """Activate each frozen case without renaming it when implementation is authorized."""
    raise AssertionError(f'unimplemented NC-08 contract case: {case.case_id}')
