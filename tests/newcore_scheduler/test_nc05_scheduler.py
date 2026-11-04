from __future__ import annotations

import ast
import dataclasses
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from newcore.account_context import Operation, VenueWork, WorkResult, WorkState
from newcore.domain import Action
from newcore.scheduler import (
    CYCLE_PERIOD_MS,
    GUARD_PERIOD_MS,
    MANAGE_PERIOD_MS,
    MAX_ACCOUNTS,
    AccountBinding,
    FailureCode,
    HookOutcome,
    HookStatus,
    Lane,
    Scheduler,
    SchedulerAdmissionRefused,
    SchedulerState,
)


class Clock:
    def __init__(self, value=1_000_000):
        self.value = value
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.value

    def advance(self, milliseconds):
        with self.lock:
            self.value += milliseconds
            return self.value


class Hooks:
    def __init__(self, account_id, shared=None):
        self.account_id = account_id
        self.calls = []
        self.shared = shared
        self.block_lane = None
        self.started = threading.Event()
        self.release = threading.Event()
        self.raises = False
        self.invalid = False
        self.outcome = HookOutcome.success()

    def _call(self, lane, account_id, now_ms):
        assert account_id == self.account_id
        self.calls.append((lane, now_ms))
        if self.shared is not None:
            self.shared.append((self.account_id, lane))
        if self.block_lane is lane:
            self.started.set()
            self.release.wait(2)
        if self.raises:
            raise RuntimeError('SECRET must never reach snapshots')
        if self.invalid:
            return {'raw': 'payload'}
        return self.outcome

    def guards(self, account_id, now_ms):
        return self._call(Lane.GUARDS, account_id, now_ms)

    def manage(self, account_id, now_ms):
        return self._call(Lane.MANAGE, account_id, now_ms)

    def cycle(self, account_id, now_ms):
        return self._call(Lane.CYCLE, account_id, now_ms)


class Worker:
    def __init__(self, account_id, shared=None):
        self.account_id = account_id
        self.shared = shared
        self.work = []
        self.results = []
        self.in_flight = None
        self.stopping = False
        self.reconcile = False
        self.stop_calls = []
        self.snapshot_raises = False
        self.submit_raises = False
        self.work_started = threading.Event()
        self.submit_started = threading.Event()
        self.snapshot_started = threading.Event()
        self.stop_started = threading.Event()
        self.submit_release = threading.Event()
        self.snapshot_release = threading.Event()
        self.work_release = threading.Event()
        self.stop_release = threading.Event()
        self.block_submit = False
        self.block_snapshot = False
        self.block_work = False
        self.block_stop = False
        self.thread_calls = []
        self.lock = threading.Lock()

    def _called(self, name):
        self.thread_calls.append((name, threading.get_ident()))

    def submit(self, work):
        self._called('submit')
        self.submit_started.set()
        if self.block_submit:
            self.submit_release.wait(2)
        if self.submit_raises:
            raise RuntimeError('SECRET admission text')
        with self.lock:
            if self.stopping:
                raise RuntimeError('stopping')
            self.work.append(work)

    def run_next(self):
        self._called('run_next')
        with self.lock:
            if not self.work:
                return None
            # Model the NC-04 priority guarantee used by NC-05.
            index = next((i for i, item in enumerate(self.work)
                          if item.action in (Action.PROTECT, Action.FLATTEN)), 0)
            item = self.work.pop(index)
            self.in_flight = item.work_id
            self.work_started.set()
        if self.block_work:
            self.work_release.wait(2)
        if self.shared is not None:
            self.shared.append((self.account_id, item.work_id))
        result = WorkResult(work_id=item.work_id, state=WorkState.COMPLETED,
                            detail='fake_one_shot')
        with self.lock:
            self.in_flight = None
            self.results.append(result)
        return result

    def stop(self, deadline_ms):
        self._called('stop')
        self.stop_started.set()
        if self.block_stop:
            self.stop_release.wait(2)
        if self.shared is not None:
            self.shared.append((self.account_id, 'stop'))
        with self.lock:
            self.stop_calls.append(deadline_ms)
            self.stopping = True
            self.work = [item for item in self.work
                         if item.action in (Action.PROTECT, Action.FLATTEN)]
        return self.snapshot()

    def snapshot(self):
        self._called('snapshot')
        self.snapshot_started.set()
        if self.block_snapshot:
            self.snapshot_release.wait(2)
        if self.snapshot_raises:
            raise RuntimeError('SECRET snapshot error')
        with self.lock:
            return SimpleNamespace(
                account_id=self.account_id,
                queued=tuple(item.work_id for item in self.work),
                in_flight=self.in_flight,
                results=tuple(self.results),
                stopping=self.stopping,
                reconcile_before_restart=self.reconcile,
            )


def aid(suffix):
    return 'acct_' + suffix * 32


def binding(suffix='a', *, clock=None, restore=True, shared=None):
    account_id = aid(suffix)
    worker = Worker(account_id, shared)
    hooks = Hooks(account_id, shared)
    return AccountBinding(account_id=account_id, worker=worker, hooks=hooks,
                          restore_proven=restore), worker, hooks


def scheduler(*bindings, clock=None, boot='boot-test'):
    clock = clock or Clock()
    return Scheduler(boot_id=boot, accounts=tuple(bindings), monotonic_clock=clock), clock


def eventually(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.002)
    assert predicate()


def venue_work(work_id, action, now=1_000_000):
    return VenueWork(work_id=work_id, action=action, operation=Operation.QUERY,
                     payload=object(), enqueued_at_ms=now, deadline_ms=now + 100_000)


def lane(snapshot, name, account=0):
    return next(row for row in snapshot.accounts[account].lanes if row.lane is name)


def first_pass(sched, hooks):
    sched.step()
    eventually(lambda: len(hooks.calls) >= 2)
    return sched.step()


def test_frozen_cadences_and_account_cap():
    assert (GUARD_PERIOD_MS, MANAGE_PERIOD_MS, CYCLE_PERIOD_MS) == (1_000, 2_000, 60_000)
    rows = [binding(chr(97 + i))[0] for i in range(MAX_ACCOUNTS + 1)]
    with pytest.raises(ValueError, match='accounts_invalid'):
        scheduler(*rows)


def test_startup_is_fail_closed_until_restore_and_first_pass():
    row, _worker, hooks = binding(restore=False)
    sched, _clock = scheduler(row)
    assert sched.snapshot().state is SchedulerState.NOT_READY
    assert sched.snapshot().failure is FailureCode.RESTORE_UNPROVEN
    first_pass(sched, hooks)
    assert not sched.snapshot().ready
    sched.mark_restore_proven(row.account_id)
    snap = sched.step()
    assert snap.ready and snap.state is SchedulerState.RUNNING


def test_cycle_cannot_run_before_global_readiness():
    clock = Clock()
    a, _wa, ha = binding('a', restore=True)
    b, _wb, hb = binding('b', restore=False)
    sched, _ = scheduler(a, b, clock=clock)
    first_pass(sched, ha)
    eventually(lambda: len(hb.calls) >= 2)
    sched.step()
    clock.advance(CYCLE_PERIOD_MS)
    sched.step()
    time.sleep(0.02)
    assert all(call[0] is not Lane.CYCLE for call in ha.calls + hb.calls)


def test_large_clock_jump_skips_missed_periods_without_burst():
    row, _worker, hooks = binding()
    sched, clock = scheduler(row)
    first_pass(sched, hooks)
    before = {name: sum(x[0] is name for x in hooks.calls) for name in (Lane.GUARDS, Lane.MANAGE)}
    clock.advance(30_000)
    sched.step()
    eventually(lambda: len(hooks.calls) >= sum(before.values()) + 2)
    time.sleep(0.02)
    after = {name: sum(x[0] is name for x in hooks.calls) for name in before}
    assert after == {name: count + 1 for name, count in before.items()}


def test_exact_cadence_and_cycle_after_readiness():
    row, _worker, hooks = binding()
    sched, clock = scheduler(row)
    first_pass(sched, hooks)
    initial = len(hooks.calls)
    clock.advance(999)
    sched.step(); time.sleep(0.01)
    assert len(hooks.calls) == initial
    clock.advance(1)
    sched.step(); eventually(lambda: len(hooks.calls) == initial + 1)
    assert hooks.calls[-1][0] is Lane.GUARDS
    clock.advance(CYCLE_PERIOD_MS)
    sched.step(); eventually(lambda: any(x[0] is Lane.CYCLE for x in hooks.calls))


def test_protection_precedes_hooks_and_general_work():
    order = []
    row, worker, hooks = binding(shared=order)
    hooks.block_lane = Lane.GUARDS
    sched, _clock = scheduler(row)
    sched.step()
    assert hooks.started.wait(1)
    sched.submit(row.account_id, venue_work('general', Action.ENTER))
    sched.submit(row.account_id, venue_work('protect', Action.PROTECT))
    hooks.release.set()
    eventually(lambda: any(item == (row.account_id, 'general') for item in order))
    names = [item[1] for item in order]
    assert names.index('protect') < names.index(Lane.MANAGE) < names.index('general')
    assert worker.results[0].work_id == 'protect'


def test_blocked_account_does_not_block_other_account():
    a, _wa, ha = binding('a')
    b, _wb, hb = binding('b')
    ha.block_lane = Lane.GUARDS
    sched, _clock = scheduler(a, b)
    sched.step()
    assert ha.started.wait(1)
    eventually(lambda: len(hb.calls) >= 2)
    snap = sched.step()
    by_id = {row.account_id: row for row in snap.accounts}
    assert not by_id[a.account_id].ready
    assert by_id[b.account_id].ready
    ha.release.set()


def test_every_worker_operation_has_one_dedicated_non_owner_thread():
    row, worker, hooks = binding()
    owner_thread = threading.get_ident()
    sched, clock = scheduler(row)
    sched.submit(row.account_id, venue_work('affinity', Action.PROTECT))
    first_pass(sched, hooks)
    eventually(lambda: any(result.work_id == 'affinity' for result in worker.results))
    sched.stop(clock() + 1_000)
    eventually(lambda: bool(worker.stop_calls))
    names = {name for name, _thread_id in worker.thread_calls}
    thread_ids = {thread_id for _name, thread_id in worker.thread_calls}
    assert {'submit', 'snapshot', 'run_next', 'stop'} <= names
    assert len(thread_ids) == 1
    assert owner_thread not in thread_ids


def test_blocking_submit_is_account_local_and_publication_remains_bounded():
    a, wa, _ha = binding('a')
    b, wb, hb = binding('b')
    wa.block_submit = True
    sched, _clock = scheduler(a, b)
    sched.submit(a.account_id, venue_work('blocked-submit', Action.PROTECT))
    assert wa.submit_started.wait(1)
    started = time.perf_counter()
    snap = sched.step()
    assert (time.perf_counter() - started) * 1_000 < 50
    assert 'blocked-submit' in snap.accounts[0].queue.queued
    sched.submit(b.account_id, venue_work('independent-protect', Action.PROTECT))
    assert wb.work_started.wait(0.1)
    eventually(lambda: len(hb.calls) >= 2)
    wa.submit_release.set()


def test_blocking_worker_snapshot_never_blocks_scheduler_or_other_account():
    a, wa, _ha = binding('a')
    b, _wb, hb = binding('b')
    wa.block_snapshot = True
    sched, _clock = scheduler(a, b)
    assert wa.snapshot_started.wait(1)
    started = time.perf_counter()
    snap = sched.step()
    assert (time.perf_counter() - started) * 1_000 < 50
    assert snap.generation == 1
    eventually(lambda: len(hb.calls) >= 2)
    wa.snapshot_release.set()


@pytest.mark.parametrize('invalid, expected', [
    (False, FailureCode.HOOK_EXCEPTION),
    (True, FailureCode.INVALID_HOOK_OUTCOME),
])
def test_hook_failures_are_stable_and_sanitized(invalid, expected):
    row, _worker, hooks = binding()
    hooks.invalid = invalid
    hooks.raises = not invalid
    sched, _clock = scheduler(row)
    sched.step()
    eventually(lambda: len(hooks.calls) >= 2)
    snap = sched.step()
    assert lane(snap, Lane.GUARDS).failure is expected
    assert 'SECRET' not in repr(snap)
    assert 'raw' not in repr(snap)


def test_snapshots_are_deeply_immutable_and_lock_free_reference_reads():
    row, _worker, hooks = binding()
    hooks.block_lane = Lane.GUARDS
    sched, _clock = scheduler(row)
    sched.step(); assert hooks.started.wait(1)
    snap = sched.snapshot()
    started = time.perf_counter()
    assert all(sched.snapshot() is snap for _ in range(20_000))
    assert time.perf_counter() - started < 0.10
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.generation = 99
    with pytest.raises(TypeError):
        snap.accounts[0].lanes[0] = None
    hooks.release.set()


def test_many_concurrent_readers_see_whole_generations():
    row, _worker, hooks = binding()
    sched, clock = scheduler(row)
    first_pass(sched, hooks)
    errors = []
    stop = threading.Event()

    def reader():
        previous = -1
        while not stop.is_set():
            snap = sched.snapshot()
            if snap.generation < previous or snap.boot_id != 'boot-test':
                errors.append((previous, snap))
            previous = snap.generation

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for thread in threads: thread.start()
    for _ in range(30):
        clock.advance(1_000); sched.step()
    stop.set()
    for thread in threads: thread.join(1)
    assert not errors


def test_stale_thresholds_are_strictly_monotonic():
    row, _worker, hooks = binding()
    sched, clock = scheduler(row)
    snap = first_pass(sched, hooks)
    guard_success = lane(snap, Lane.GUARDS).last_success_mono
    clock.advance(3_001)
    snap = sched.step()
    # A new guard was queued but the last completed pass remains stale until it completes.
    assert snap.published_mono - guard_success > 3_000
    assert lane(snap, Lane.GUARDS).stale
    eventually(lambda: sum(x[0] is Lane.GUARDS for x in hooks.calls) >= 2)
    snap = sched.step()
    assert not lane(snap, Lane.GUARDS).stale


def test_stop_clears_pending_hooks_rejects_work_and_drains_protection():
    order = []
    row, worker, hooks = binding(shared=order)
    hooks.block_lane = Lane.GUARDS
    sched, clock = scheduler(row)
    sched.step(); assert hooks.started.wait(1)
    sched.submit(row.account_id, venue_work('protect', Action.PROTECT))
    sched.submit(row.account_id, venue_work('general', Action.ENTER))
    sched.stop(clock() + 1_000)
    with pytest.raises(SchedulerAdmissionRefused, match='scheduler_stopping'):
        sched.submit(row.account_id, venue_work('later', Action.PROTECT))
    hooks.release.set()
    eventually(lambda: any(result.work_id == 'protect' for result in worker.results))
    eventually(lambda: bool(worker.stop_calls))
    sched.step()
    assert all(result.work_id != 'general' for result in worker.results)
    assert sum(call[0] is Lane.MANAGE for call in hooks.calls) == 0
    names = [item[1] for item in order]
    assert names.index('protect') < names.index('stop')


def test_late_completion_marks_reconcile_before_restart():
    row, _worker, hooks = binding()
    hooks.block_lane = Lane.GUARDS
    sched, clock = scheduler(row)
    sched.step(); assert hooks.started.wait(1)
    sched.stop(clock() + 100)
    clock.advance(101)
    hooks.release.set()
    eventually(lambda: len(hooks.calls) >= 1)
    eventually(lambda: sched.step().accounts[0].reconcile_before_restart)
    snap = sched.snapshot()
    assert snap.failure is FailureCode.ACCOUNT_UNKNOWN
    assert not snap.ready


def test_work_completion_at_stop_deadline_can_never_publish_clean_stopped():
    row, worker, _hooks = binding()
    worker.block_work = True
    sched, clock = scheduler(row)
    sched.submit(row.account_id, venue_work('late-work', Action.PROTECT))
    assert worker.work_started.wait(1)
    sched.stop(clock() + 100)
    clock.advance(100)
    worker.work_release.set()
    eventually(lambda: sched.step().accounts[0].reconcile_before_restart)
    eventually(lambda: sched.step().state is SchedulerState.STOPPED)
    snap = sched.snapshot()
    assert snap.accounts[0].reconcile_before_restart
    assert snap.failure is FailureCode.ACCOUNT_UNKNOWN


def test_worker_stop_completion_at_deadline_forces_reconcile():
    row, worker, _hooks = binding()
    worker.block_stop = True
    sched, clock = scheduler(row)
    sched.stop(clock() + 100)
    assert worker.stop_started.wait(1)
    clock.advance(100)
    worker.stop_release.set()
    eventually(lambda: sched.step().state is SchedulerState.STOPPED)
    snap = sched.snapshot()
    assert snap.accounts[0].reconcile_before_restart
    assert snap.failure is FailureCode.ACCOUNT_UNKNOWN


def test_uncertain_worker_snapshot_is_fail_closed_and_sanitized():
    row, worker, _hooks = binding()
    worker.snapshot_raises = True
    sched, _clock = scheduler(row)
    eventually(lambda: sched.step().accounts[0].reconcile_before_restart)
    snap = sched.snapshot()
    assert snap.accounts[0].reconcile_before_restart
    assert lane(snap, Lane.GUARDS).failure is FailureCode.SNAPSHOT_EXCEPTION
    assert 'SECRET' not in repr(snap)


def test_boot_and_generation_are_process_local():
    row1, _worker1, _hooks1 = binding('a')
    row2, _worker2, _hooks2 = binding('b')
    first, _ = scheduler(row1, boot='boot-one')
    second, _ = scheduler(row2, boot='boot-two')
    assert first.snapshot().generation == second.snapshot().generation == 0
    assert first.step().generation == 1
    assert second.snapshot().boot_id == 'boot-two'


def test_owner_mutations_require_the_constructing_thread():
    row, _worker, _hooks = binding()
    sched, _clock = scheduler(row)
    errors = []

    def foreign_step():
        try:
            sched.step()
        except Exception as ex:
            errors.append(ex)
    thread = threading.Thread(target=foreign_step)
    thread.start(); thread.join(1)
    assert len(errors) == 1 and str(errors[0]) == 'scheduler_owner_thread_required'


def test_submit_and_snapshot_latency_probes_are_bounded():
    blocked, _blocked_worker, blocked_hooks = binding('a')
    row, worker, _hooks = binding('b')
    blocked_hooks.block_lane = Lane.GUARDS
    sched, _clock = scheduler(blocked, row)
    sched.step()
    assert blocked_hooks.started.wait(1)
    start = time.perf_counter()
    sched.submit(row.account_id, venue_work('fast-protect', Action.PROTECT))
    assert (time.perf_counter() - start) * 1_000 < 10
    assert worker.work_started.wait(0.1)
    start = time.perf_counter()
    for _ in range(10_000):
        sched.snapshot()
    assert (time.perf_counter() - start) * 1_000 / 10_000 < 1
    start = time.perf_counter()
    sched.step()
    assert (time.perf_counter() - start) * 1_000 < 50
    blocked_hooks.release.set()


def test_import_boundary_has_no_runtime_store_budget_or_venue_dependency():
    path = Path(__file__).parents[2] / 'newcore' / 'scheduler.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.append(node.module or '')
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    forbidden = ('runtime', 'store', 'ports', 'venue.binance', 'account_budget')
    assert not [module for module in modules if any(name in module for name in forbidden)]


def test_duplicate_accounts_and_arbitrary_hooks_are_refused():
    row, worker, _hooks = binding()
    with pytest.raises(ValueError, match='duplicate_account_id'):
        scheduler(row, row)
    with pytest.raises(ValueError, match='scheduler_hooks_invalid'):
        AccountBinding(account_id=row.account_id, worker=worker, hooks=lambda: None)


def test_admission_failures_have_no_raw_cause_or_context():
    row, worker, _hooks = binding()
    sched, _clock = scheduler(row)
    with pytest.raises(SchedulerAdmissionRefused) as caught:
        sched.submit(aid('z'), venue_work('unknown', Action.PROTECT))
    assert caught.value.code == 'unknown_account'
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    worker.submit_raises = True
    sched.submit(row.account_id, venue_work('async-refusal', Action.PROTECT))
    assert worker.submit_started.wait(1)
    eventually(lambda: sched.step().accounts[0].reconcile_before_restart)
    assert 'SECRET' not in repr(sched.snapshot())


def test_hook_outcome_cannot_carry_inconsistent_status():
    with pytest.raises(ValueError, match='successful_hook_has_failure'):
        HookOutcome(status=HookStatus.SUCCESS, failure=FailureCode.HOOK_FAILED)
    with pytest.raises(ValueError, match='failed_hook_missing_failure'):
        HookOutcome(status=HookStatus.FAILED)
