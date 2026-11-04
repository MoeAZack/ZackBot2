"""RT-01 injected runtime ports.

Each account supplies its own ports.  None of them receives a venue, budget, credential or raw payload from the
runtime: venue access happens only through the account's NC-04 ``AccountContext`` queue, which the runtime drives.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from newcore.account_context import AccountContext, VenueWork, WorkResult
from newcore.management import ManagementEvaluation, ManagementPlan
from newcore.reconciliation import DurableState, VenueSnapshot
from newcore.risk import ApprovedDecision, RiskEvaluation


@runtime_checkable
class DurableStateSource(Protocol):
    """Project the recovered NC-02 journal state into REC-02 durable local truth."""

    def load(self, account_id: str, journal_state: object) -> DurableState: ...


@runtime_checkable
class EvidencePort(Protocol):
    """Name the read-only venue work for one evidence generation, then assemble its completed results."""

    def read_work(self, account_id: str, generation: int, now_mono: int,
                  deadline_mono: int) -> tuple[VenueWork, ...]: ...

    def assemble(self, account_id: str, generation: int,
                 results: tuple[WorkResult, ...]) -> VenueSnapshot: ...


@runtime_checkable
class ManagementSource(Protocol):
    """Fresh NC-07 inputs for every managed lot of one account."""

    def evaluations(self, account_id: str, now_mono: int) -> tuple[ManagementEvaluation, ...]: ...


@runtime_checkable
class EntrySource(Protocol):
    """Opening requests for NC-06; consulted only while entries are enabled and every account is ready."""

    def candidates(self, account_id: str, now_mono: int) -> tuple[RiskEvaluation, ...]: ...


@runtime_checkable
class ActionSink(Protocol):
    """The single future route from a decided action to journaled venue work.

    RT-01 ships no implementation that sends anything.  ``accept_*`` returns True only when the action became durable
    intent work; any other answer leaves the account in HOLD (management) or skips the entry (cycle).
    """

    def accept_management(self, account_id: str, plan: ManagementPlan) -> bool: ...

    def accept_entry(self, account_id: str, decision: ApprovedDecision, evaluation: RiskEvaluation) -> bool: ...


class RefusingActionSink:
    """The RT-01 default: nothing is routed, so any required management action holds the account."""

    def accept_management(self, account_id: str, plan: ManagementPlan) -> bool:
        return False

    def accept_entry(self, account_id: str, decision: ApprovedDecision, evaluation: RiskEvaluation) -> bool:
        return False


class NoEntries:
    def candidates(self, account_id: str, now_mono: int) -> tuple[RiskEvaluation, ...]:
        return ()


class NoManagedLots:
    def evaluations(self, account_id: str, now_mono: int) -> tuple[ManagementEvaluation, ...]:
        return ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountPorts:
    context: AccountContext
    state_source: DurableStateSource
    evidence: EvidencePort
    management: ManagementSource = NoManagedLots()
    entries: EntrySource = NoEntries()
    actions: ActionSink = RefusingActionSink()

    def __post_init__(self):
        if type(self.context) is not AccountContext:
            raise ValueError('account_context_required')
        checks = ((self.state_source, ('load',)), (self.evidence, ('read_work', 'assemble')),
                  (self.management, ('evaluations',)), (self.entries, ('candidates',)),
                  (self.actions, ('accept_management', 'accept_entry')))
        for port, names in checks:
            if not all(callable(getattr(port, name, None)) for name in names):
                raise ValueError('runtime_port_invalid')


__all__ = ['AccountPorts', 'ActionSink', 'DurableStateSource', 'EntrySource', 'EvidencePort', 'ManagementSource',
           'NoEntries', 'NoManagedLots', 'RefusingActionSink']
