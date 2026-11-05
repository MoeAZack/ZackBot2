"""RT-01 replacement entrypoint: the composition root that wires NEWCORE without the legacy engine.

Boot, per account (before any scheduler thread exists):

1. NC-02 ``recover_journal``.  Only CLEAN or REPAIRED gives a writable journal; every other verdict is a HOLD.
2. ``DurableStateSource`` projects the recovered journal state into REC-02 durable truth.  Its binding must be
   CONFIRMED and must name the configured environment (``testnet`` or ``sim``); mainnet is a HOLD.
3. Read-only evidence work (``Action.RECONCILE`` with a positions/open-orders/fills operation, nothing else) runs
   through the account's own NC-04 ``AccountContext``.  ``EvidencePort.assemble`` builds the REC-02 snapshot.
4. REC-02 ``plan_reconciliation``.  Only FLAT_CONFIRMED or PROTECTED_CONFIRMED proves restore; HOLD stays fail-closed.

Then one NC-05 ``Scheduler`` runs the per-account hooks:

* guards: refreshes exchange truth in two phases (queue read work, then fold the completed results into a new REC-02
  plan) and succeeds only while the proof is fresh and no hold is active;
* manage: NC-07 ``management.evaluate`` on every managed lot; an action plan must be accepted by the ``ActionSink``,
  otherwise the account holds.  Plans without actions are traced through NC-09 ``trace_plan`` best effort;
* cycle: NC-06 ``risk.evaluate`` only while entries are enabled, the account holds nothing and every account is ready.

The runtime never builds an order itself.  RT-01 ships only ``RefusingActionSink``.
"""
from __future__ import annotations

import enum
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Mapping

from newcore import management as nc07
from newcore import risk as nc06
from newcore.account_context import Operation, VenueWork, WorkResult, WorkState, validate_distinct_egress
from newcore.domain import Action
from newcore.domain.account import BindingState, Environment
from newcore.observability import ObservationKind, trace_plan
from newcore.reconciliation import (DurableState, PlannerConfig, PlanStatus, VenueSnapshot,
                                    plan_reconciliation)
from newcore.runtime.config import AccountSpec, RuntimeConfig, RuntimeEnvironment
from newcore.runtime.journaling import SinkBinding
from newcore.runtime.ports import AccountPorts, EntryCandidate
from newcore.scheduler import AccountBinding, HookOutcome, Scheduler, SchedulerSnapshot, SchedulerState
from newcore.store import JournalLocked, Verdict, recover_journal


EVENT_CAPACITY = 256
MAX_EVIDENCE_WORK = 32
READ_OPERATIONS = frozenset({Operation.POSITIONS, Operation.OPEN_ORDERS, Operation.FILLS})
ENVIRONMENT_BINDING = {
    RuntimeEnvironment.TESTNET: Environment.TESTNET,
    RuntimeEnvironment.PAPER_SIM: Environment.SIM,
}


class RuntimeBootError(ValueError):
    """A wiring error found before any account is touched."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class AccountPhase(enum.StrEnum):
    HOLD = 'hold'          # fail-closed: guards refuse, cycle never runs
    PROVEN = 'proven'      # last exchange-truth proof was FLAT/PROTECTED_CONFIRMED and is fresh
    STALE = 'stale'        # no hold, but the last proof is older than proof_max_age_ms or missing


class HoldCode(enum.StrEnum):
    JOURNAL_LOCKED = 'journal_locked'
    JOURNAL_ERROR = 'journal_error'
    JOURNAL_DAMAGED = 'journal_damaged'
    JOURNAL_UNREADABLE = 'journal_unreadable'
    JOURNAL_MISSING = 'journal_missing'
    JOURNAL_ABORT_RO = 'journal_abort_ro'
    JOURNAL_DURABILITY_UNAVAILABLE = 'journal_durability_unavailable'
    DURABLE_STATE_INVALID = 'durable_state_invalid'
    BINDING_UNCONFIRMED = 'binding_unconfirmed'
    ENVIRONMENT_MISMATCH = 'environment_mismatch'
    EVIDENCE_WORK_INVALID = 'evidence_work_invalid'
    EVIDENCE_UNAVAILABLE = 'evidence_unavailable'
    EVIDENCE_TIMEOUT = 'evidence_timeout'
    EVIDENCE_INVALID = 'evidence_invalid'
    RECONCILE_ERROR = 'reconcile_error'
    RECONCILE_HOLD = 'reconcile_hold'
    WORKER_RECONCILE = 'worker_reconcile_before_restart'
    MANAGEMENT_INPUT_INVALID = 'management_input_invalid'
    MANAGEMENT_UNRESOLVED = 'management_unresolved'
    MANAGEMENT_UNROUTED = 'management_unrouted'
    SINK_ERROR = 'action_sink_error'
    BOOT_ERROR = 'boot_error'
    # RT-02 journaling sink problems (all sticky: resolving them needs a restart and the reconcile path).
    ORDER_UNRESOLVED = 'order_unresolved'
    ORDER_JOURNAL_FAILED = 'order_journal_failed'
    PROTECTION_FAILED = 'protection_failed'
    SINK_UNBOUND = 'sink_unbound'
    RECOVERED_LIVE_INTENTS = 'recovered_live_intents'
    HOOK_ERROR = 'hook_error'


# A later confirmed REC-02 proof clears only these.  Every other hold lasts for the process and needs a restart.
CLEARABLE = frozenset({HoldCode.EVIDENCE_UNAVAILABLE, HoldCode.EVIDENCE_TIMEOUT, HoldCode.EVIDENCE_INVALID,
                       HoldCode.RECONCILE_ERROR, HoldCode.RECONCILE_HOLD})
JOURNAL_HOLD = {
    Verdict.DAMAGED: HoldCode.JOURNAL_DAMAGED,
    Verdict.UNREADABLE: HoldCode.JOURNAL_UNREADABLE,
    Verdict.MISSING: HoldCode.JOURNAL_MISSING,
    Verdict.ABORT_RO: HoldCode.JOURNAL_ABORT_RO,
    Verdict.DURABILITY_UNAVAILABLE: HoldCode.JOURNAL_DURABILITY_UNAVAILABLE,
}
# NC-07 HOLD plans: only these may pass as "nothing to do"; the unresolved ones hold the account; every other HOLD
# reason (stale input, generation mismatch, confirmation pending, ambiguous touch, ...) refuses this manage pass so the
# account is not ready until a later pass succeeds.
PASS_REASONS = frozenset({nc07.PlanReason.NO_CHANGE, nc07.PlanReason.PENDING_INTENT, nc07.PlanReason.LOT_FINISHED})
UNRESOLVED_REASONS = frozenset({nc07.PlanReason.INPUT_INVALID, nc07.PlanReason.PROTECTION_UNKNOWN})
BOOT_ID_MAX = 64


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    sequence: int
    account_id: str
    kind: str
    code: str
    at_mono: int


@dataclass(frozen=True, slots=True)
class AccountRuntimeSnapshot:
    account_id: str
    phase: AccountPhase
    holds: tuple[str, ...]
    journal_verdict: str
    restore_proven: bool
    proof_mono: int | None
    last_plan_status: str | None
    last_plan_id: str | None
    evidence_generation: int
    evidence_pending: bool


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    boot_id: str
    environment: RuntimeEnvironment
    entries_enabled: bool
    scheduler: SchedulerSnapshot
    accounts: tuple[AccountRuntimeSnapshot, ...]
    events: tuple[RuntimeEvent, ...]
    events_dropped: int


@dataclass(slots=True)
class _Pending:
    generation: int
    work_ids: tuple[str, ...]
    deadline_mono: int


class _Account:
    def __init__(self, spec: AccountSpec, ports: AccountPorts):
        self.spec, self.ports = spec, ports
        self.lock = threading.Lock()
        self.holds: set[HoldCode] = set()
        self.journal = None
        self.verdict = 'not_run'
        self.generation = 0
        self.pending: _Pending | None = None
        self.last_refresh_mono: int | None = None
        self.proof_mono: int | None = None
        self.plan_status: str | None = None
        self.plan_id: str | None = None
        self.restore_proven = False
        self.restore_mark_due = False
        self.used_work_ids: set[str] = set()


class _EventLog:
    def __init__(self, capacity: int = EVENT_CAPACITY):
        self._lock = threading.Lock()
        self._rows: deque[RuntimeEvent] = deque(maxlen=capacity)
        self._sequence = 0
        self.dropped = 0

    def record(self, account_id: str, kind: str, code: str, at_mono: int) -> None:
        with self._lock:
            self._sequence += 1
            if len(self._rows) == self._rows.maxlen:
                self.dropped += 1
            self._rows.append(RuntimeEvent(self._sequence, account_id, kind, str(code), at_mono))

    def rows(self) -> tuple[RuntimeEvent, ...]:
        with self._lock:
            return tuple(self._rows)


class Runtime:
    """The NEWCORE runtime.  ``step``/``stop``/``close`` are owner-thread calls; ``snapshot`` is lock-free."""

    def __init__(self, config: RuntimeConfig, accounts: tuple[_Account, ...], *, boot_id: str,
                 monotonic_clock: Callable[[], int], wall_clock: Callable[[], int], events: _EventLog):
        self.config, self.boot_id = config, boot_id
        self._mono, self._wall, self._events = monotonic_clock, wall_clock, events
        self._accounts = {row.spec.account_id: row for row in accounts}
        for row in accounts:
            self._bind_sink(row)
        bindings = tuple(AccountBinding(account_id=row.spec.account_id, worker=row.ports.context,
                                        hooks=_Hooks(self, row), restore_proven=row.restore_proven)
                         for row in accounts)
        self._scheduler = Scheduler(boot_id=boot_id, accounts=bindings, monotonic_clock=monotonic_clock)
        self._closed = False
        self._published = self._build(self._scheduler.snapshot())

    # ---------------------------------------------------------------------------------------------- owner thread
    def step(self) -> RuntimeSnapshot:
        for row in self._accounts.values():
            # Check and mark under the account lock so a hold added by the runner cannot interleave.
            # mark_restore_proven never takes row.lock, so this cannot deadlock.
            with row.lock:
                due = row.restore_mark_due and not row.holds
                if due:
                    self._scheduler.mark_restore_proven(row.spec.account_id)
                    row.restore_mark_due = False
                    row.restore_proven = True
            if due:
                self._events.record(row.spec.account_id, 'restore', 'proven', self._mono())
        self._published = self._build(self._scheduler.step())
        return self._published

    def stop(self, deadline_ms: int | None = None) -> RuntimeSnapshot:
        deadline = self._mono() + self.config.stop_grace_ms if deadline_ms is None else deadline_ms
        self._published = self._build(self._scheduler.stop(deadline))
        return self._published

    def close(self) -> None:
        """Release every journal writer lock.  Allowed only once the scheduler published STOPPED."""
        if self._scheduler.snapshot().state is not SchedulerState.STOPPED:
            raise RuntimeError('runtime_not_stopped')
        if self._closed:
            return
        self._closed = True
        for row in self._accounts.values():
            if row.journal is not None:
                row.journal.close()

    def snapshot(self) -> RuntimeSnapshot:
        return self._published

    # ---------------------------------------------------------------------------------------------- helpers
    def _phase(self, row: _Account, now: int) -> AccountPhase:
        if row.holds:
            return AccountPhase.HOLD
        if row.proof_mono is None or now - row.proof_mono >= self.config.proof_max_age_ms:
            return AccountPhase.STALE
        return AccountPhase.PROVEN

    def _build(self, scheduler: SchedulerSnapshot) -> RuntimeSnapshot:
        now = self._mono()
        rows = []
        for row in self._accounts.values():
            with row.lock:
                rows.append(AccountRuntimeSnapshot(
                    account_id=row.spec.account_id, phase=self._phase(row, now),
                    holds=tuple(sorted(code.value for code in row.holds)), journal_verdict=row.verdict,
                    restore_proven=row.restore_proven, proof_mono=row.proof_mono,
                    last_plan_status=row.plan_status, last_plan_id=row.plan_id,
                    evidence_generation=row.generation, evidence_pending=row.pending is not None))
        return RuntimeSnapshot(boot_id=self.boot_id, environment=self.config.environment,
                               entries_enabled=self.config.entries_enabled, scheduler=scheduler,
                               accounts=tuple(rows), events=self._events.rows(),
                               events_dropped=self._events.dropped)

    def _bind_sink(self, row: _Account) -> None:
        """Hand a journaling sink its account's journal and queue, before any runner thread exists."""
        bind = getattr(row.ports.actions, 'bind', None)
        if not callable(bind) or row.journal is None:
            return
        account_id = row.spec.account_id
        try:
            bind(SinkBinding(account_id=account_id, aggregate_id=row.spec.aggregate_id, journal=row.journal,
                             submit=lambda work: self._submit(account_id, work), result=row.ports.context.result,
                             cancel=row.ports.context.cancel, wall_clock=self._wall,
                             order_deadline_ms=self.config.evidence_deadline_ms), self._mono)
        except Exception:
            row.holds.add(HoldCode.SINK_UNBOUND)
            row.proof_mono = None
            self._events.record(account_id, 'boot', HoldCode.SINK_UNBOUND, 0)

    def _submit(self, account_id: str, work: VenueWork) -> None:
        self._scheduler.submit(account_id, work)

    def _all_ready(self) -> bool:
        return self._scheduler.snapshot().ready

    def _scheduler_reconcile(self, account_id: str) -> bool:
        return any(row.account_id == account_id and row.reconcile_before_restart
                   for row in self._scheduler.snapshot().accounts)

    def _close_journals(self) -> None:
        for row in self._accounts.values():
            if row.journal is not None:
                try:
                    row.journal.close()
                except Exception:
                    pass


# -------------------------------------------------------------------------------------------------- evidence
def _validate_work(works: object, deadline_mono: int, row: _Account) -> tuple[VenueWork, ...]:
    """Read-only reconcile work with ids never used before in this process (a reused id could fold old results)."""
    if type(works) is not tuple or not 1 <= len(works) <= MAX_EVIDENCE_WORK:
        raise ValueError('evidence_work_shape')
    ids = set()
    for work in works:
        if (type(work) is not VenueWork or work.action is not Action.RECONCILE
                or work.operation not in READ_OPERATIONS or work.deadline_ms > deadline_mono
                or work.work_id in ids or work.work_id in row.used_work_ids
                or row.ports.context.result(work.work_id) is not None):
            raise ValueError('evidence_work_forbidden')
        ids.add(work.work_id)
    with row.lock:
        row.used_work_ids |= ids
    return works


def _load_durable(config: RuntimeConfig, row: _Account) -> tuple[DurableState | None, HoldCode | None]:
    try:
        source = row.ports.state_source
        load_journal = getattr(source, 'load_journal', None)
        if callable(load_journal):     # a projection that needs the journal's own events (binding, results)
            durable = load_journal(row.spec.account_id, row.journal)
        else:
            durable = source.load(row.spec.account_id, row.journal.state())
    except Exception:
        return None, HoldCode.DURABLE_STATE_INVALID
    if type(durable) is not DurableState or durable.account.account_id != row.spec.account_id:
        return None, HoldCode.DURABLE_STATE_INVALID
    if durable.account.binding_state is not BindingState.CONFIRMED:
        return None, HoldCode.BINDING_UNCONFIRMED
    if durable.account.binding.environment is not ENVIRONMENT_BINDING[config.environment]:
        return None, HoldCode.ENVIRONMENT_MISMATCH
    return durable, None


def _fold(config: RuntimeConfig, row: _Account, results: tuple[WorkResult, ...], generation: int,
          now_mono: int, wall_clock: Callable[[], int]) -> HoldCode | None:
    """Turn completed evidence into a REC-02 proof.  Returns the hold it caused, or None when confirmed."""
    if any(type(r) is not WorkResult or r.state is not WorkState.COMPLETED for r in results):
        return HoldCode.EVIDENCE_UNAVAILABLE
    durable, hold = _load_durable(config, row)
    if hold is not None:
        return hold
    try:
        snapshot = row.ports.evidence.assemble(row.spec.account_id, generation, results)
    except Exception:
        return HoldCode.EVIDENCE_INVALID
    if type(snapshot) is not VenueSnapshot:
        return HoldCode.EVIDENCE_INVALID
    try:
        plan = plan_reconciliation(durable, snapshot,
                                   PlannerConfig(now_ms=wall_clock(), max_snapshot_age_s=config.max_snapshot_age_s))
    except Exception:
        return HoldCode.RECONCILE_ERROR
    confirmed = plan.status in (PlanStatus.FLAT_CONFIRMED, PlanStatus.PROTECTED_CONFIRMED)
    with row.lock:
        row.plan_status, row.plan_id = plan.status.value, plan.reconciliation_id
        row.proof_mono = now_mono if confirmed else None
    return None if confirmed else HoldCode.RECONCILE_HOLD


def _boot_account(config: RuntimeConfig, row: _Account, *, recover, monotonic_clock, wall_clock,
                  events: _EventLog) -> None:
    account_id = row.spec.account_id
    now = monotonic_clock()
    try:
        recovery = recover(row.spec.account_dir, account_id, row.spec.aggregate_id)
    except JournalLocked:
        row.holds.add(HoldCode.JOURNAL_LOCKED); row.verdict = 'locked'
        events.record(account_id, 'boot', HoldCode.JOURNAL_LOCKED, now)
        return
    except Exception:
        row.holds.add(HoldCode.JOURNAL_ERROR); row.verdict = 'error'
        events.record(account_id, 'boot', HoldCode.JOURNAL_ERROR, now)
        return
    verdict = getattr(recovery, 'verdict', None)
    row.verdict = verdict.value if isinstance(verdict, Verdict) else 'invalid'
    if verdict not in (Verdict.CLEAN, Verdict.REPAIRED) or getattr(recovery, 'journal', None) is None:
        code = JOURNAL_HOLD.get(verdict, HoldCode.JOURNAL_ERROR)
        row.holds.add(code)
        events.record(account_id, 'boot', code, now)
        return
    row.journal = recovery.journal
    bind_journal = getattr(row.ports.evidence, 'bind_journal', None)
    if callable(bind_journal):        # an evidence port that reads which orders to check from the journal
        bind_journal(row.journal)
    _durable, hold = _load_durable(config, row)
    if hold is not None:
        row.holds.add(hold)
        events.record(account_id, 'boot', hold, now)
        return
    row.generation += 1
    generation = row.generation
    deadline = now + config.evidence_deadline_ms
    try:
        works = _validate_work(row.ports.evidence.read_work(account_id, generation, now, deadline), deadline, row)
        for work in works:
            row.ports.context.submit(work)
    except Exception:
        row.holds.add(HoldCode.EVIDENCE_WORK_INVALID)
        events.record(account_id, 'boot', HoldCode.EVIDENCE_WORK_INVALID, now)
        return
    # Drain synchronously: the scheduler does not exist yet, so this thread is the account's only worker caller.
    for _ in range(len(works)):
        row.ports.context.run_next()
    results = tuple(row.ports.context.result(work.work_id) for work in works)  # a missing result folds as a hold
    row.last_refresh_mono = now
    hold = _fold(config, row, results, generation, monotonic_clock(), wall_clock)
    if hold is not None:
        row.holds.add(hold)
        events.record(account_id, 'boot', hold, monotonic_clock())
        return
    if row.ports.context.snapshot().reconcile_before_restart:
        row.holds.add(HoldCode.WORKER_RECONCILE)
        events.record(account_id, 'boot', HoldCode.WORKER_RECONCILE, monotonic_clock())
        return
    row.restore_proven = True
    events.record(account_id, 'boot', row.plan_status, monotonic_clock())


# -------------------------------------------------------------------------------------------------- hooks
class _Hooks:
    """NC-05 hooks for one account.  They run on that account's runner thread only."""

    def __init__(self, runtime: Runtime, row: _Account):
        self._rt, self._row = runtime, row

    def _check(self, account_id: str) -> None:
        if account_id != self._row.spec.account_id:
            raise ValueError('hook_account_mismatch')

    def _hold(self, code: HoldCode, now: int, lane: str) -> None:
        with self._row.lock:
            self._row.holds.add(code)
            if code not in CLEARABLE:
                self._row.proof_mono = None
        self._rt._events.record(self._row.spec.account_id, lane, code, now)

    def _guarded(self, lane: str, body, account_id: str, now_ms: int) -> HookOutcome:
        self._check(account_id)
        try:
            return body(account_id, now_ms)
        except Exception:
            # An unexpected failure is never left to the scheduler alone: the runtime holds the account too.
            self._hold(HoldCode.HOOK_ERROR, now_ms, lane)
            return HookOutcome.failed()

    def guards(self, account_id: str, now_ms: int) -> HookOutcome:
        return self._guarded('guards', self._guards, account_id, now_ms)

    def manage(self, account_id: str, now_ms: int) -> HookOutcome:
        return self._guarded('manage', self._manage, account_id, now_ms)

    def cycle(self, account_id: str, now_ms: int) -> HookOutcome:
        return self._guarded('cycle', self._cycle, account_id, now_ms)

    def _guards(self, account_id: str, now_ms: int) -> HookOutcome:
        row, cfg = self._row, self._rt.config
        poll = getattr(row.ports.actions, 'poll', None)
        if callable(poll) and row.journal is not None:
            # Results are journaled before anything else reads the account, even while it holds.
            for code in poll(now_ms):
                self._hold(HoldCode(code), now_ms, 'guards')
        if (row.ports.context.snapshot().reconcile_before_restart
                or self._rt._scheduler_reconcile(account_id)):
            self._hold(HoldCode.WORKER_RECONCILE, now_ms, 'guards')
        with row.lock:
            sticky = any(code not in CLEARABLE for code in row.holds)
            pending = row.pending
        if sticky:
            return HookOutcome.refused()
        if pending is not None:
            results = tuple(row.ports.context.result(work_id) for work_id in pending.work_ids)
            if all(result is not None for result in results):
                with row.lock:
                    row.pending = None
                hold = _fold(cfg, row, results, pending.generation, now_ms, self._rt._wall)
                if hold is None:
                    with row.lock:
                        row.holds -= CLEARABLE
                        if not row.restore_proven:
                            row.restore_mark_due = True
                    self._rt._events.record(account_id, 'guards', row.plan_status, now_ms)
                else:
                    self._hold(hold, now_ms, 'guards')
            elif now_ms >= pending.deadline_mono:
                for work_id in pending.work_ids:
                    row.ports.context.cancel(work_id, now_ms)
                with row.lock:
                    row.pending = None
                    row.proof_mono = None
                self._hold(HoldCode.EVIDENCE_TIMEOUT, now_ms, 'guards')
        with row.lock:
            start = (row.pending is None and not any(code not in CLEARABLE for code in row.holds)
                     and (row.last_refresh_mono is None or now_ms - row.last_refresh_mono >= cfg.refresh_period_ms))
        if start:
            self._start_refresh(now_ms)
        with row.lock:
            ok = (not row.holds and row.proof_mono is not None
                  and now_ms - row.proof_mono < cfg.proof_max_age_ms)
        return HookOutcome.success() if ok else HookOutcome.refused()

    def _start_refresh(self, now_ms: int) -> None:
        row, cfg = self._row, self._rt.config
        with row.lock:
            row.generation += 1
            generation = row.generation
            row.last_refresh_mono = now_ms
        deadline = now_ms + cfg.evidence_deadline_ms
        try:
            works = _validate_work(row.ports.evidence.read_work(row.spec.account_id, generation, now_ms, deadline),
                                   deadline, row)
        except Exception:
            self._hold(HoldCode.EVIDENCE_WORK_INVALID, now_ms, 'guards')
            return
        try:
            for work in works:
                self._rt._submit(row.spec.account_id, work)
        except Exception:
            # Reads already queued stay harmless; a partial generation is never folded.
            self._hold(HoldCode.EVIDENCE_UNAVAILABLE, now_ms, 'guards')
            return
        with row.lock:
            row.pending = _Pending(generation, tuple(work.work_id for work in works), deadline)

    def _manage(self, account_id: str, now_ms: int) -> HookOutcome:
        row = self._row
        try:
            evaluations = row.ports.management.evaluations(account_id, now_ms)
        except Exception:
            self._hold(HoldCode.MANAGEMENT_INPUT_INVALID, now_ms, 'manage')
            return HookOutcome.failed()
        if type(evaluations) is not tuple or any(type(e) is not nc07.ManagementEvaluation for e in evaluations):
            self._hold(HoldCode.MANAGEMENT_INPUT_INVALID, now_ms, 'manage')
            return HookOutcome.failed()
        held = waiting = False
        for evaluation in evaluations:
            plan = nc07.evaluate(evaluation)
            if (plan.status in (nc07.PlanStatus.RECONCILE, nc07.PlanStatus.AMBIGUOUS)
                    or plan.reason in UNRESOLVED_REASONS
                    or plan.status is nc07.PlanStatus.ACTIONS and not plan.actions):
                self._hold(HoldCode.MANAGEMENT_UNRESOLVED, now_ms, 'manage')
                held = True
                continue
            if not plan.actions and plan.reason not in PASS_REASONS:
                waiting = True
                self._rt._events.record(account_id, 'manage', f'not_ready:{plan.reason.value}', now_ms)
                continue
            if plan.actions:
                try:
                    accepted = row.ports.actions.accept_management(account_id, plan)
                except Exception:
                    self._hold(HoldCode.SINK_ERROR, now_ms, 'manage')
                    held = True
                    continue
                if accepted is not True:
                    self._hold(HoldCode.MANAGEMENT_UNROUTED, now_ms, 'manage')
                    held = True
                continue
            try:  # NC-09 mirror: best effort, never blocks management
                trace = trace_plan(plan, ObservationKind.WHY_HELD)
                self._rt._events.record(account_id, 'manage', f'why_held:{trace.reason}', now_ms)
            except Exception:
                self._rt._events.record(account_id, 'manage', 'trace_failed', now_ms)
        return HookOutcome.refused() if held or waiting else HookOutcome.success()

    def _cycle(self, account_id: str, now_ms: int) -> HookOutcome:
        row, cfg = self._row, self._rt.config
        if not cfg.entries_enabled:
            return HookOutcome.success()
        with row.lock:
            fresh = (not row.holds and row.proof_mono is not None
                     and now_ms - row.proof_mono < cfg.proof_max_age_ms)
        if not fresh or not self._rt._all_ready():
            self._rt._events.record(account_id, 'cycle', 'entries_held', now_ms)
            return HookOutcome.refused()
        try:
            candidates = row.ports.entries.candidates(account_id, now_ms)
        except Exception:
            self._rt._events.record(account_id, 'cycle', 'entry_source_error', now_ms)
            return HookOutcome.failed()
        if type(candidates) is not tuple or any(type(c) not in (nc06.RiskEvaluation, EntryCandidate)
                                                for c in candidates):
            self._rt._events.record(account_id, 'cycle', 'entry_source_invalid', now_ms)
            return HookOutcome.failed()
        for candidate in candidates:
            keyed = type(candidate) is EntryCandidate
            evaluation = candidate.evaluation if keyed else candidate
            decision = nc06.evaluate(evaluation)
            if type(decision) is not nc06.ApprovedDecision:
                self._rt._events.record(account_id, 'cycle', 'entry_refused', now_ms)
                continue
            try:
                if keyed:
                    accepted = row.ports.actions.accept_entry(account_id, decision, evaluation, key=candidate.key)
                else:
                    accepted = row.ports.actions.accept_entry(account_id, decision, evaluation)
            except Exception:
                accepted = False
            self._rt._events.record(account_id, 'cycle', 'entry_routed' if accepted is True else 'entry_unrouted',
                                    now_ms)
        return HookOutcome.success()


# -------------------------------------------------------------------------------------------------- boot
def boot(config: RuntimeConfig, ports: Mapping[str, AccountPorts], *, boot_id: str,
         monotonic_clock: Callable[[], int], wall_clock: Callable[[], int],
         recover: Callable = recover_journal) -> Runtime:
    """Recover, reconcile and wire every configured account, then return a not-yet-stepped Runtime."""
    if type(config) is not RuntimeConfig:
        raise RuntimeBootError('config_invalid')
    if not callable(monotonic_clock) or not callable(wall_clock) or not callable(recover):
        raise RuntimeBootError('clock_invalid')
    if type(boot_id) is not str or not 1 <= len(boot_id) <= BOOT_ID_MAX:
        raise RuntimeBootError('boot_id_invalid')
    if not isinstance(ports, Mapping) or set(ports) != {row.account_id for row in config.accounts}:
        raise RuntimeBootError('ports_accounts_mismatch')
    rows = []
    for spec in config.accounts:
        port = ports[spec.account_id]
        if type(port) is not AccountPorts:
            raise RuntimeBootError('ports_invalid')
        if (port.context.account_id, port.context.egress_id) != (spec.account_id, spec.egress_id):
            raise RuntimeBootError('context_account_mismatch')
        view = port.context.snapshot()
        if view.queued or view.in_flight is not None or view.results or view.stopping or view.reconcile_before_restart:
            # Leftover work on an injected context would be sent before any exchange-truth proof exists.
            raise RuntimeBootError('account_context_not_fresh')
        rows.append(_Account(spec, port))
    try:
        validate_distinct_egress(((row.spec.account_id, row.spec.egress_id) for row in rows),
                                 environment=config.environment.value)
    except Exception:
        raise RuntimeBootError('egress_invalid') from None
    events = _EventLog()
    try:
        for row in rows:
            try:
                _boot_account(config, row, recover=recover, monotonic_clock=monotonic_clock, wall_clock=wall_clock,
                              events=events)
            except Exception:
                row.holds.add(HoldCode.BOOT_ERROR)
                row.proof_mono, row.restore_proven = None, False
                events.record(row.spec.account_id, 'boot', HoldCode.BOOT_ERROR, 0)
        return Runtime(config, tuple(rows), boot_id=boot_id, monotonic_clock=monotonic_clock,
                       wall_clock=wall_clock, events=events)
    except BaseException:
        for row in rows:  # never leave a journal writer lock behind a failed boot
            if row.journal is not None:
                try:
                    row.journal.close()
                except Exception:
                    pass
        raise


__all__ = ['AccountPhase', 'AccountRuntimeSnapshot', 'CLEARABLE', 'HoldCode', 'READ_OPERATIONS', 'Runtime',
           'RuntimeBootError', 'RuntimeEvent', 'RuntimeSnapshot', 'boot']
