"""NC-05 deterministic multi-account scheduling kernel.

The caller of :meth:`Scheduler.step` is the single scheduler owner.  Each
account has one dedicated runner thread which is the only thread that invokes
that account's hooks or ``AccountContext`` worker methods.  Published snapshots
are immutable reference values; readers never acquire a scheduler lock.

This module deliberately owns no runtime loop, sleep, persistence, venue,
credentials, UI, or PAPER wiring.
"""
from __future__ import annotations

import enum
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from newcore.account_context import AccountSnapshot, VenueWork, WorkResult, WorkState
from newcore.domain import Action


MAX_ACCOUNTS = 4
GUARD_PERIOD_MS = 1_000
MANAGE_PERIOD_MS = 2_000
CYCLE_PERIOD_MS = 60_000
TRANSITION_PUBLICATION_MS = 250
PUBLICATION_TARGET_MS = 50
STALE_GUARDS_MS = 3_000
STALE_MANAGE_MS = 6_000
STALE_CYCLE_MS = 120_000
STALE_PUBLICATION_MS = 1_000


class Lane(enum.StrEnum):
    GUARDS = 'guards'
    MANAGE = 'manage'
    CYCLE = 'cycle'


LANE_ORDER = (Lane.GUARDS, Lane.MANAGE, Lane.CYCLE)
PERIOD_MS = {
    Lane.GUARDS: GUARD_PERIOD_MS,
    Lane.MANAGE: MANAGE_PERIOD_MS,
    Lane.CYCLE: CYCLE_PERIOD_MS,
}
STALE_MS = {
    Lane.GUARDS: STALE_GUARDS_MS,
    Lane.MANAGE: STALE_MANAGE_MS,
    Lane.CYCLE: STALE_CYCLE_MS,
}


class SchedulerState(enum.StrEnum):
    NOT_READY = 'not_ready'
    RUNNING = 'running'
    STOPPING = 'stopping'
    STOPPED = 'stopped'


class HookStatus(enum.StrEnum):
    SUCCESS = 'success'
    REFUSED = 'refused'
    FAILED = 'failed'


class FailureCode(enum.StrEnum):
    NONE = 'none'
    RESTORE_UNPROVEN = 'restore_unproven'
    FIRST_PASS_PENDING = 'first_pass_pending'
    HOOK_REFUSED = 'hook_refused'
    HOOK_FAILED = 'hook_failed'
    HOOK_EXCEPTION = 'hook_exception'
    INVALID_HOOK_OUTCOME = 'invalid_hook_outcome'
    ACCOUNT_EXCEPTION = 'account_exception'
    ACCOUNT_UNKNOWN = 'account_unknown'
    SNAPSHOT_EXCEPTION = 'snapshot_exception'
    STOP_DEADLINE = 'stop_deadline'


class SchedulerAdmissionRefused(Exception):
    """A stable, non-secret scheduler admission failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _exact_ms(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 2 ** 63 - 1:
        raise ValueError(f'{name}_invalid')
    return value


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or not value or len(value) > 64:
        raise ValueError(f'{name}_invalid')
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class HookOutcome:
    status: HookStatus
    failure: FailureCode = FailureCode.NONE

    def __post_init__(self):
        object.__setattr__(self, 'status', HookStatus(self.status))
        object.__setattr__(self, 'failure', FailureCode(self.failure))
        if self.status is HookStatus.SUCCESS and self.failure is not FailureCode.NONE:
            raise ValueError('successful_hook_has_failure')
        if self.status is not HookStatus.SUCCESS and self.failure is FailureCode.NONE:
            raise ValueError('failed_hook_missing_failure')

    @classmethod
    def success(cls) -> 'HookOutcome':
        return cls(status=HookStatus.SUCCESS)

    @classmethod
    def refused(cls) -> 'HookOutcome':
        return cls(status=HookStatus.REFUSED, failure=FailureCode.HOOK_REFUSED)

    @classmethod
    def failed(cls) -> 'HookOutcome':
        return cls(status=HookStatus.FAILED, failure=FailureCode.HOOK_FAILED)


@runtime_checkable
class SchedulerHooks(Protocol):
    """The only downstream hook surface accepted by NC-05."""

    def guards(self, account_id: str, now_ms: int) -> HookOutcome: ...
    def manage(self, account_id: str, now_ms: int) -> HookOutcome: ...
    def cycle(self, account_id: str, now_ms: int) -> HookOutcome: ...


@runtime_checkable
class AccountWorker(Protocol):
    """Narrow structural view of NC-04 used by the account runner."""

    def submit(self, work: VenueWork) -> None: ...
    def run_next(self) -> WorkResult | None: ...
    def stop(self, deadline_ms: int) -> AccountSnapshot: ...
    def snapshot(self) -> AccountSnapshot: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountBinding:
    account_id: str
    worker: AccountWorker
    hooks: SchedulerHooks
    restore_proven: bool = False

    def __post_init__(self):
        _identifier(self.account_id, 'account_id')
        if type(self.restore_proven) is not bool:
            raise ValueError('restore_proven_invalid')
        for name in ('submit', 'run_next', 'stop', 'snapshot'):
            if not callable(getattr(self.worker, name, None)):
                raise ValueError('account_worker_invalid')
        for name in ('guards', 'manage', 'cycle'):
            if not callable(getattr(self.hooks, name, None)):
                raise ValueError('scheduler_hooks_invalid')


@dataclass(frozen=True, slots=True, kw_only=True)
class QueueSummary:
    queued: tuple[str, ...]
    in_flight: str | None
    result_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class LaneSnapshot:
    lane: Lane
    last_success_mono: int | None
    stale: bool
    pending: bool
    in_flight: bool
    failure: FailureCode


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountScheduleSnapshot:
    account_id: str
    ready: bool
    not_ready: bool
    stale: bool
    restore_proven: bool
    reconcile_before_restart: bool
    lanes: tuple[LaneSnapshot, ...]
    queue: QueueSummary


@dataclass(frozen=True, slots=True, kw_only=True)
class SchedulerSnapshot:
    boot_id: str
    generation: int
    published_mono: int
    state: SchedulerState
    ready: bool
    stale: bool
    failure: FailureCode
    accounts: tuple[AccountScheduleSnapshot, ...]


@dataclass(frozen=True, slots=True)
class _HookTask:
    lane: Lane
    scheduled_mono: int


@dataclass(frozen=True, slots=True)
class _RunnerEvent:
    account_id: str
    kind: str
    completed_mono: int
    lane: Lane | None = None
    outcome: HookOutcome | None = None
    work_id: str | None = None
    reconcile: bool = False
    failure: FailureCode | None = None
    view: QueueSummary | None = None


class _AccountRunner:
    """One serialized downstream execution lane for one account."""

    def __init__(self, binding: AccountBinding, clock: Callable[[], int], events: queue.SimpleQueue):
        self.account_id = binding.account_id
        self._worker = binding.worker
        self._hooks = binding.hooks
        self._clock = clock
        self._events = events
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._hooks_pending: dict[Lane, _HookTask] = {}
        self._hook_active: Lane | None = None
        self._pending_work: dict[str, tuple[VenueWork, bool]] = {}
        self._work: dict[str, bool] = {}  # accepted by NC-04; value is protection/flatten priority
        self._submission_active: tuple[VenueWork, bool] | None = None
        self._snapshot_requested = True
        self._stopping = False
        self._deadline: int | None = None
        self._stop_called = False
        self._terminal = False
        self._thread = threading.Thread(
            target=self._run, name=f'nc05-{binding.account_id[-12:]}', daemon=True)
        self._thread.start()

    def _now(self) -> int:
        return _exact_ms(self._clock(), 'monotonic_clock')

    def offer_hook(self, lane: Lane, scheduled_mono: int) -> bool:
        with self._lock:
            if self._stopping or self._terminal or lane in self._hooks_pending or self._hook_active is lane:
                return False
            self._hooks_pending[lane] = _HookTask(lane, scheduled_mono)
            self._wake.set()
            return True

    def hook_state(self, lane: Lane) -> tuple[bool, bool]:
        with self._lock:
            return lane in self._hooks_pending, self._hook_active is lane

    def submit(self, work: VenueWork) -> None:
        if type(work) is not VenueWork:
            raise SchedulerAdmissionRefused('venue_work_required')
        with self._lock:
            if self._stopping or self._terminal:
                raise SchedulerAdmissionRefused('scheduler_stopping')
            active_id = None if self._submission_active is None else self._submission_active[0].work_id
            if work.work_id in self._pending_work or work.work_id in self._work or work.work_id == active_id:
                raise SchedulerAdmissionRefused('duplicate_work_id')
            urgent = work.action in (Action.PROTECT, Action.FLATTEN)
            self._pending_work[work.work_id] = (work, urgent)
            self._events.put(_RunnerEvent(
                self.account_id, 'queued', self._now(), work_id=work.work_id))
            self._wake.set()

    def request_snapshot(self) -> None:
        with self._lock:
            if not self._terminal:
                self._snapshot_requested = True
                self._wake.set()

    def begin_stop(self, deadline_ms: int) -> None:
        with self._lock:
            if self._terminal:
                return
            self._stopping = True
            self._deadline = deadline_ms if self._deadline is None else min(self._deadline, deadline_ms)
            self._hooks_pending.clear()
            dropped = tuple(work_id for work_id, (_work, urgent) in self._pending_work.items()
                            if not urgent)
            self._pending_work = {
                work_id: item for work_id, item in self._pending_work.items() if item[1]
            }
            for work_id in dropped:
                self._events.put(_RunnerEvent(
                    self.account_id, 'dropped', self._now(), work_id=work_id))
            self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def _choose(self) -> tuple[str, object] | None:
        with self._lock:
            now = self._now()
            if self._stopping:
                if now >= self._deadline:
                    if not self._stop_called:
                        self._stop_called = True
                        return 'stop', self._deadline
                    self._terminal = True
                    return 'terminal', bool(self._pending_work or self._work or self._submission_active)
                pending = next((work_id for work_id, (_work, urgent) in self._pending_work.items()
                                if urgent), None)
                if pending is not None:
                    item = self._pending_work.pop(pending)
                    self._submission_active = item
                    return 'submit', item
                if any(self._work.values()):
                    return 'work', None
                if not self._stop_called:
                    self._stop_called = True
                    return 'stop', self._deadline
                self._terminal = True
                return 'terminal', False
            if any(self._work.values()):
                return 'work', None
            pending = next((work_id for work_id, (_work, urgent) in self._pending_work.items()
                            if urgent), None)
            if pending is not None:
                item = self._pending_work.pop(pending)
                self._submission_active = item
                return 'submit', item
            for lane in LANE_ORDER:
                task = self._hooks_pending.pop(lane, None)
                if task is not None:
                    self._hook_active = lane
                    return 'hook', task
            if self._pending_work:
                work_id = next(iter(self._pending_work))
                item = self._pending_work.pop(work_id)
                self._submission_active = item
                return 'submit', item
            if self._work:
                return 'work', None
            if self._snapshot_requested:
                self._snapshot_requested = False
                return 'snapshot', None
            self._wake.clear()
            return None

    def _run_hook(self, task: _HookTask) -> None:
        try:
            method = {
                Lane.GUARDS: self._hooks.guards,
                Lane.MANAGE: self._hooks.manage,
                Lane.CYCLE: self._hooks.cycle,
            }[task.lane]
            outcome = method(self.account_id, task.scheduled_mono)
            if type(outcome) is not HookOutcome:
                outcome = HookOutcome(status=HookStatus.FAILED,
                                      failure=FailureCode.INVALID_HOOK_OUTCOME)
        except Exception:
            outcome = HookOutcome(status=HookStatus.FAILED,
                                  failure=FailureCode.HOOK_EXCEPTION)
        completed = self._now()
        with self._lock:
            self._hook_active = None
            late = self._stopping and self._deadline is not None and completed >= self._deadline
        if late:
            outcome = HookOutcome(status=HookStatus.FAILED, failure=FailureCode.STOP_DEADLINE)
        self._events.put(_RunnerEvent(self.account_id, 'hook', completed, lane=task.lane,
                                      outcome=outcome, reconcile=late))

    def _run_submit(self, item: tuple[VenueWork, bool]) -> None:
        work, urgent = item
        failure = None
        try:
            self._worker.submit(work)
            completed = self._now()
            accepted = True
        except Exception:
            completed = self._now()
            accepted = False
            failure = FailureCode.ACCOUNT_EXCEPTION
        with self._lock:
            self._submission_active = None
            late = self._stopping and self._deadline is not None and completed >= self._deadline
            if accepted:
                self._work[work.work_id] = urgent
            self._snapshot_requested = True
        if late:
            failure = FailureCode.STOP_DEADLINE
        self._events.put(_RunnerEvent(
            self.account_id, 'submit', completed, work_id=work.work_id,
            reconcile=not accepted or late, failure=failure))

    def _run_work(self) -> None:
        failure = None
        try:
            result = self._worker.run_next()
            completed = self._now()
            if result is not None and type(result) is not WorkResult:
                raise TypeError('invalid_account_result')
            work_id = None if result is None else result.work_id
            reconcile = False if result is None else result.state is WorkState.UNKNOWN
        except Exception:
            completed, work_id, reconcile = self._now(), None, True
            failure = FailureCode.ACCOUNT_EXCEPTION
        with self._lock:
            late = self._stopping and self._deadline is not None and completed >= self._deadline
            if work_id is not None:
                self._work.pop(work_id, None)
            elif self._work:
                # A conforming NC-04 worker returns a result for each tracked item.
                # Refuse to spin forever if a substitute violates that contract.
                self._work.clear()
            self._snapshot_requested = True
        if late:
            reconcile = True
            failure = FailureCode.STOP_DEADLINE
        self._events.put(_RunnerEvent(self.account_id, 'work', completed,
                                      work_id=work_id, reconcile=reconcile, failure=failure))

    def _sanitize_view(self, view: object) -> tuple[QueueSummary, bool]:
        if (getattr(view, 'account_id', None) != self.account_id
                or type(getattr(view, 'queued', None)) is not tuple
                or any(type(work_id) is not str for work_id in view.queued)
                or getattr(view, 'in_flight', None) is not None
                and type(view.in_flight) is not str
                or type(getattr(view, 'results', None)) is not tuple
                or type(getattr(view, 'reconcile_before_restart', None)) is not bool):
            raise ValueError('account_snapshot_invalid')
        return (QueueSummary(queued=tuple(view.queued), in_flight=view.in_flight,
                             result_count=len(view.results)),
                view.reconcile_before_restart)

    def _run_snapshot(self) -> None:
        try:
            summary, reconcile = self._sanitize_view(self._worker.snapshot())
            completed = self._now()
            failure = None
        except Exception:
            completed, summary, reconcile = self._now(), None, True
            failure = FailureCode.SNAPSHOT_EXCEPTION
        self._events.put(_RunnerEvent(
            self.account_id, 'snapshot', completed, reconcile=reconcile,
            failure=failure, view=summary))

    def _call_stop(self, deadline: int) -> None:
        failure = None
        try:
            view, reconcile = self._sanitize_view(self._worker.stop(deadline))
            completed = self._now()
            queued = set(view.queued)
            with self._lock:
                self._work = {work_id: urgent for work_id, urgent in self._work.items()
                              if urgent and work_id in queued}
        except Exception:
            completed, view, reconcile = self._now(), None, True
            failure = FailureCode.ACCOUNT_EXCEPTION
            with self._lock:
                self._work.clear()
        if completed >= deadline:
            reconcile = True
            failure = FailureCode.STOP_DEADLINE
        self._events.put(_RunnerEvent(
            self.account_id, 'stop', completed, reconcile=reconcile,
            failure=failure, view=view))

    def _run(self) -> None:
        while True:
            selected = self._choose()
            if selected is None:
                self._wake.wait()
                continue
            kind, item = selected
            if kind == 'hook':
                self._run_hook(item)
            elif kind == 'submit':
                self._run_submit(item)
            elif kind == 'work':
                self._run_work()
            elif kind == 'snapshot':
                self._run_snapshot()
            elif kind == 'stop':
                self._call_stop(item)
            else:
                self._events.put(_RunnerEvent(self.account_id, 'terminal', self._now(),
                                              reconcile=bool(item)))
                return


@dataclass(slots=True)
class _AccountState:
    restore_proven: bool
    last_success: dict[Lane, int | None]
    failure: dict[Lane, FailureCode]
    pending_work: set[str]
    reconcile: bool = False
    terminal: bool = False


class Scheduler:
    """Explicitly stepped NC-05 scheduler with lock-free immutable reads."""

    def __init__(self, *, boot_id: str, accounts: tuple[AccountBinding, ...],
                 monotonic_clock: Callable[[], int]):
        self._boot_id = _identifier(boot_id, 'boot_id')
        if type(accounts) is not tuple or not 1 <= len(accounts) <= MAX_ACCOUNTS:
            raise ValueError('accounts_invalid')
        if len({binding.account_id for binding in accounts}) != len(accounts):
            raise ValueError('duplicate_account_id')
        if not callable(monotonic_clock):
            raise ValueError('monotonic_clock_invalid')
        self._clock = monotonic_clock
        now = self._now()
        self._owner_thread = threading.get_ident()
        self._events: queue.SimpleQueue[_RunnerEvent] = queue.SimpleQueue()
        self._bindings = {binding.account_id: binding for binding in accounts}
        self._states = {
            binding.account_id: _AccountState(
                restore_proven=binding.restore_proven,
                last_success={lane: None for lane in LANE_ORDER},
                failure={lane: FailureCode.NONE for lane in LANE_ORDER},
                pending_work=set())
            for binding in accounts
        }
        self._runners = {
            binding.account_id: _AccountRunner(binding, monotonic_clock, self._events)
            for binding in accounts
        }
        self._next_due = {
            account_id: {
                Lane.GUARDS: now,
                Lane.MANAGE: now,
                Lane.CYCLE: now + CYCLE_PERIOD_MS,
            }
            for account_id in self._bindings
        }
        self._generation = 0
        self._stopping = False
        self._stop_deadline: int | None = None
        self._views = {
            account_id: QueueSummary(queued=(), in_flight=None, result_count=0)
            for account_id in self._bindings
        }
        self._published = self._build_snapshot(now)

    def _now(self) -> int:
        return _exact_ms(self._clock(), 'monotonic_clock')

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError('scheduler_owner_thread_required')

    def snapshot(self) -> SchedulerSnapshot:
        """Return the current immutable reference without taking a lock."""
        return self._published

    def mark_restore_proven(self, account_id: str) -> None:
        self._assert_owner()
        state = self._states.get(account_id)
        if state is None:
            raise SchedulerAdmissionRefused('unknown_account')
        state.restore_proven = True

    def submit(self, account_id: str, work: VenueWork) -> None:
        runner = self._runners.get(account_id)
        if runner is None:
            raise SchedulerAdmissionRefused('unknown_account')
        if self._stopping:
            raise SchedulerAdmissionRefused('scheduler_stopping')
        runner.submit(work)

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            state = self._states[event.account_id]
            state.reconcile = state.reconcile or event.reconcile
            if event.view is not None:
                self._views[event.account_id] = event.view
            if event.failure is FailureCode.SNAPSHOT_EXCEPTION:
                for lane in LANE_ORDER:
                    state.failure[lane] = FailureCode.SNAPSHOT_EXCEPTION
            if event.kind == 'queued' and event.work_id is not None:
                state.pending_work.add(event.work_id)
            elif event.kind == 'dropped' and event.work_id is not None:
                state.pending_work.discard(event.work_id)
            elif event.kind == 'submit' and event.failure is not None and event.work_id is not None:
                state.pending_work.discard(event.work_id)
            elif event.kind == 'work' and event.work_id is not None:
                state.pending_work.discard(event.work_id)
            elif event.kind == 'stop' and event.view is not None:
                state.pending_work.intersection_update(event.view.queued)
            if event.kind == 'hook' and event.lane is not None and event.outcome is not None:
                state.failure[event.lane] = event.outcome.failure
                if event.outcome.status is HookStatus.SUCCESS:
                    state.last_success[event.lane] = event.completed_mono
            elif event.kind == 'terminal':
                state.terminal = True

    def _account_ready(self, state: _AccountState) -> bool:
        return (state.restore_proven
                and state.last_success[Lane.GUARDS] is not None
                and state.last_success[Lane.MANAGE] is not None
                and state.failure[Lane.GUARDS] is FailureCode.NONE
                and state.failure[Lane.MANAGE] is FailureCode.NONE
                and not state.reconcile)

    def _all_ready(self) -> bool:
        return all(self._account_ready(state) for state in self._states.values())

    def step(self) -> SchedulerSnapshot:
        """Run one owner step; time jumps dispatch at most once per lane/account."""
        self._assert_owner()
        now = self._now()
        self._drain_events()
        ready_before = self._all_ready()
        if not self._stopping:
            for lane in LANE_ORDER:
                for account_id in self._bindings:
                    due = self._next_due[account_id][lane]
                    if now < due:
                        continue
                    # Always advance from observed time. Missed periods are skipped.
                    self._next_due[account_id][lane] = now + PERIOD_MS[lane]
                    if lane is Lane.CYCLE and not ready_before:
                        continue
                    self._runners[account_id].offer_hook(lane, now)
        else:
            for runner in self._runners.values():
                runner.wake()
        for runner in self._runners.values():
            runner.request_snapshot()
        self._drain_events()
        self._generation += 1
        self._published = self._build_snapshot(now)
        return self._published

    def stop(self, deadline_ms: int) -> SchedulerSnapshot:
        """Reject new work and ask each account runner to drain queued protection."""
        self._assert_owner()
        deadline = _exact_ms(deadline_ms, 'deadline_ms')
        now = self._now()
        if deadline < now:
            deadline = now
        if not self._stopping:
            self._stopping = True
            self._stop_deadline = deadline
            for runner in self._runners.values():
                runner.begin_stop(deadline)
        elif deadline < self._stop_deadline:
            self._stop_deadline = deadline
            for runner in self._runners.values():
                runner.begin_stop(deadline)
        return self.step()

    def _build_snapshot(self, now: int) -> SchedulerSnapshot:
        accounts: list[AccountScheduleSnapshot] = []
        for account_id in self._bindings:
            state = self._states[account_id]
            lane_rows: list[LaneSnapshot] = []
            for lane in LANE_ORDER:
                last = state.last_success[lane]
                pending, active = self._runners[account_id].hook_state(lane)
                lane_rows.append(LaneSnapshot(
                    lane=lane, last_success_mono=last,
                    stale=last is None or now - last >= STALE_MS[lane],
                    pending=pending, in_flight=active, failure=state.failure[lane]))
            ready = self._account_ready(state)
            view = self._views[account_id]
            queued = tuple(dict.fromkeys(view.queued + tuple(sorted(state.pending_work))))
            accounts.append(AccountScheduleSnapshot(
                account_id=account_id, ready=ready, not_ready=not ready,
                stale=any(row.stale for row in lane_rows),
                restore_proven=state.restore_proven,
                reconcile_before_restart=state.reconcile,
                lanes=tuple(lane_rows),
                queue=QueueSummary(queued=queued, in_flight=view.in_flight,
                                   result_count=view.result_count)))
        all_ready = all(account.ready for account in accounts)
        # STOPPED is published only after the owner consumes each runner's
        # ordered terminal event.  Looking at runner state directly could race
        # ahead of a preceding late-stop/reconcile event in the same queue.
        all_terminal = self._stopping and all(
            self._states[account_id].terminal for account_id in self._bindings)
        if all_terminal:
            scheduler_state = SchedulerState.STOPPED
        elif self._stopping:
            scheduler_state = SchedulerState.STOPPING
        elif all_ready:
            scheduler_state = SchedulerState.RUNNING
        else:
            scheduler_state = SchedulerState.NOT_READY
        if any(account.reconcile_before_restart for account in accounts):
            failure = FailureCode.ACCOUNT_UNKNOWN
        elif not all(account.restore_proven for account in accounts):
            failure = FailureCode.RESTORE_UNPROVEN
        elif not all_ready:
            failure = FailureCode.FIRST_PASS_PENDING
        else:
            failure = FailureCode.NONE
        return SchedulerSnapshot(
            boot_id=self._boot_id, generation=self._generation, published_mono=now,
            state=scheduler_state, ready=all_ready and not self._stopping,
            stale=(now - self._published.published_mono > STALE_PUBLICATION_MS
                   if hasattr(self, '_published') else True)
                  or any(account.stale for account in accounts),
            failure=failure, accounts=tuple(accounts))


__all__ = [
    'AccountBinding', 'AccountScheduleSnapshot', 'AccountWorker', 'CYCLE_PERIOD_MS',
    'FailureCode', 'GUARD_PERIOD_MS', 'HookOutcome', 'HookStatus', 'Lane', 'LaneSnapshot',
    'MANAGE_PERIOD_MS', 'MAX_ACCOUNTS', 'PUBLICATION_TARGET_MS', 'QueueSummary',
    'STALE_CYCLE_MS', 'STALE_GUARDS_MS', 'STALE_MANAGE_MS', 'STALE_PUBLICATION_MS',
    'Scheduler', 'SchedulerAdmissionRefused', 'SchedulerHooks', 'SchedulerSnapshot',
    'SchedulerState', 'TRANSITION_PUBLICATION_MS',
]
