"""NC-09 frozen observability contract matrix; implementation is deferred."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from newcore.domain.decision import Decision
from newcore.domain.events import IncidentRecorded
from newcore.domain.incident import Incident
from newcore.domain.portfolio import Fill
from newcore.domain.reasons import ReasonCode
from newcore.management import ManagementPlan, PlanReason
from newcore.simulator import FillSummary, PathResolution


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


def test_document_tracks_every_case_and_frozen_boundary():
    contract = (Path(__file__).parents[2] / 'docs' / 'newcore' / 'NC09_CONTRACT.md').read_text(
        encoding='utf-8')
    assert all(case.case_id in contract for case in CASES)
    for phrase in (
        'stable typed reason', 'MFE', 'MAE', 'peak give-back', 'why-held',
        'why-closed', 'bounded, non-waiting', 'dropped-full', 'write-errors',
        'must never delay or alter', 'REC-02', 'TNET-01',
    ):
        assert phrase in contract


@pytest.mark.skip(reason='NC-09 implementation awaits the four frozen Codex rulings')
@pytest.mark.parametrize('case', CASES, ids=lambda case: case.case_id)
def test_nc09_implementation_contract(case):
    """Activate every frozen case without deleting or renaming it."""
    raise AssertionError(f'unimplemented NC-09 contract case: {case.case_id}')
