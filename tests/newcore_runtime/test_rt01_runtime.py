"""RT-01 runtime wiring: boot (NC-02 -> NC-04 -> REC-02), NC-05 hooks (guards/manage/cycle), stop and close."""
from __future__ import annotations

import dataclasses
import sys
import threading
import types
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rt01_fixtures import (D, World, account_record, account_view, acct)  # noqa: E402

import newcore.runtime.runtime as rt_module  # noqa: E402
from newcore.domain.account import BindingState, Environment  # noqa: E402
from newcore.management import (LotState, ManagementEvaluation, ManagementPolicy, MarketObservation,  # noqa: E402
                                PathOrder, PendingKind, PendingSummary, PositionSide, ProtectionState,
                                ProtectionStatus, StrategyExitFact)
from newcore.reconciliation import VenuePosition  # noqa: E402
from newcore.domain.orders import Side  # noqa: E402
from newcore.runtime import (AccountPhase, HoldCode, RuntimeBootError, RuntimeConfig,  # noqa: E402
                             RuntimeEnvironment, boot)
from newcore.scheduler import SchedulerState  # noqa: E402
from newcore.store import Verdict, recover_journal  # noqa: E402


@pytest.fixture
def world(tmp_path):
    made = []

    def make(n=1, *, root=None, **kwargs):
        w = World(root or tmp_path, n, **kwargs)
        made.append(w)
        return w

    yield make
    for w in made:
        try:
            w.shutdown()
        except Exception:
            pass


def ready(snap):
    return snap.scheduler.ready


# ------------------------------------------------------------------------------------------------ boot
def test_flat_account_boots_proven_and_becomes_ready_without_any_order(world):
    w = world()
    rt = w.boot()
    a = acct(1)
    view = account_view(rt.snapshot(), a)
    assert view.journal_verdict == 'clean'
    assert view.restore_proven and view.holds == () and view.last_plan_status == 'flat_confirmed'
    snap = w.settle(ready)
    assert snap.scheduler.state is SchedulerState.RUNNING
    assert account_view(snap, a).phase is AccountPhase.PROVEN
    assert w.venues[a].writes() == []
    assert {c[0] for c in w.venues[a].calls} == {'positions', 'open_orders'}


def test_protected_position_boots_proven(world):
    w = world()
    w.truths[acct(1)].protected_long()
    rt = w.boot()
    view = account_view(rt.snapshot(), acct(1))
    assert view.restore_proven and view.last_plan_status == 'protected_confirmed'


@pytest.mark.parametrize('verdict, code', [
    (Verdict.MISSING, 'journal_missing'), (Verdict.DAMAGED, 'journal_damaged'),
    (Verdict.UNREADABLE, 'journal_unreadable'), (Verdict.ABORT_RO, 'journal_abort_ro'),
    (Verdict.DURABILITY_UNAVAILABLE, 'journal_durability_unavailable')])
def test_every_non_clean_journal_verdict_holds_without_touching_the_venue(world, verdict, code):
    w = world()

    def recover(*_args):
        return types.SimpleNamespace(verdict=verdict, journal=None)

    rt = w.boot(recover=recover)
    view = account_view(rt.snapshot(), acct(1))
    assert view.holds == (code,) and not view.restore_proven and view.phase is AccountPhase.HOLD
    w.settle(lambda s: s.scheduler.generation > 20, advance_ms=1_000)
    assert not rt.snapshot().scheduler.ready
    assert w.venues[acct(1)].calls == [] and w.sources[acct(1)].truth.loads == 0


def test_real_missing_journal_is_a_hold_and_is_never_created(world, tmp_path):
    w = world(create=False)
    rt = w.boot()
    assert account_view(rt.snapshot(), acct(1)).holds == ('journal_missing',)
    assert not (tmp_path / 'a1' / 'journal').exists() or not any((tmp_path / 'a1' / 'journal').iterdir())


def test_journal_held_by_another_writer_holds(world):
    w = world()
    spec = w.specs[0]
    other = recover_journal(spec.account_dir, spec.account_id, spec.aggregate_id)
    try:
        rt = w.boot()
        assert account_view(rt.snapshot(), acct(1)).holds == ('journal_locked',)
        assert w.venues[acct(1)].calls == []
    finally:
        other.journal.close()


def test_recover_raising_anything_else_is_a_journal_error_hold(world):
    w = world()

    def recover(*_args):
        raise OSError('disk')

    rt = w.boot(recover=recover)
    assert account_view(rt.snapshot(), acct(1)).holds == ('journal_error',)


@pytest.mark.parametrize('mutate, code', [
    (lambda t: setattr(t, 'account', account_record(t.account_id, state=BindingState.UNCONFIRMED)),
     'binding_unconfirmed'),
    (lambda t: setattr(t, 'account', account_record(t.account_id, environment=Environment.MAINNET)),
     'environment_mismatch'),
    (lambda t: setattr(t, 'account', account_record(t.account_id, environment=Environment.SIM)),
     'environment_mismatch'),
    (lambda t: setattr(t, 'account', account_record(acct(9))), 'durable_state_invalid'),
    (lambda t: setattr(t, 'load_error', RuntimeError('projection')), 'durable_state_invalid'),
])
def test_durable_state_must_be_confirmed_and_bound_to_the_configured_environment(world, mutate, code):
    w = world()
    mutate(w.truths[acct(1)])
    rt = w.boot()
    view = account_view(rt.snapshot(), acct(1))
    assert view.holds == (code,) and not view.restore_proven
    assert w.venues[acct(1)].calls == []


def test_paper_sim_requires_a_sim_binding(world, tmp_path):
    w = world(environment=RuntimeEnvironment.PAPER_SIM)
    rt = w.boot()
    assert account_view(rt.snapshot(), acct(1)).holds == ('environment_mismatch',)
    (tmp_path / 'second').mkdir()
    w2 = world(environment=RuntimeEnvironment.PAPER_SIM, root=tmp_path / 'second')
    w2.truths[acct(1)].account = account_record(acct(1), environment=Environment.SIM)
    rt2 = w2.boot()
    assert account_view(rt2.snapshot(), acct(1)).restore_proven


def test_evidence_work_that_could_place_an_order_is_refused_before_any_venue_call(world):
    w = world()
    w.evidence[acct(1)].forbidden_work = True
    rt = w.boot()
    assert account_view(rt.snapshot(), acct(1)).holds == ('evidence_work_invalid',)
    assert w.venues[acct(1)].calls == []


def test_venue_read_failure_at_boot_holds_and_worker_reconcile_is_sticky(world):
    w = world()
    w.venues[acct(1)].raise_reads = True
    rt = w.boot()
    assert account_view(rt.snapshot(), acct(1)).holds == ('evidence_unavailable',)
    w.venues[acct(1)].raise_reads = False
    snap = w.settle(lambda s: 'worker_reconcile_before_restart' in account_view(s, acct(1)).holds,
                    advance_ms=1_000)
    assert not snap.scheduler.ready
    calls = len(w.venues[acct(1)].calls)
    w.settle(lambda s: s.scheduler.generation > 40, advance_ms=1_000)
    assert len(w.venues[acct(1)].calls) == calls  # a sticky hold stops further refreshes


def test_reconcile_hold_at_boot_clears_only_after_a_confirmed_proof(world):
    w = world()
    t = w.truths[acct(1)]
    t.venue_positions = (VenuePosition('BTCUSDT', Side.LONG, D('1')),)  # foreign position
    rt = w.boot()
    view = account_view(rt.snapshot(), acct(1))
    assert view.holds == ('reconcile_hold',) and view.last_plan_status == 'hold' and not view.restore_proven
    w.settle(lambda s: s.scheduler.generation > 15, advance_ms=1_000)
    assert not rt.snapshot().scheduler.ready
    t.venue_positions = ()  # owner flattened it on the exchange
    snap = w.settle(ready, advance_ms=1_000)
    view = account_view(snap, acct(1))
    assert view.holds == () and view.restore_proven and view.last_plan_status == 'flat_confirmed'
    assert any(e.kind == 'restore' for e in snap.events)


def test_invalid_snapshot_from_assembler_is_an_evidence_hold(world):
    w = world()
    w.evidence[acct(1)].assemble_result = object()
    rt = w.boot()
    assert account_view(rt.snapshot(), acct(1)).holds == ('evidence_invalid',)


def test_guards_refresh_keeps_proof_fresh_and_a_new_foreign_position_holds(world):
    w = world(refresh_period_ms=1_000, proof_max_age_ms=12_000)
    w.boot()
    w.settle(ready)
    before = w.evidence[acct(1)].assembled
    w.settle(lambda s: w.evidence[acct(1)].assembled >= before + 3, advance_ms=1_000)
    assert w.runtime.snapshot().scheduler.ready
    w.truths[acct(1)].venue_positions = (VenuePosition('BTCUSDT', Side.SHORT, D('2')),)
    snap = w.settle(lambda s: 'reconcile_hold' in account_view(s, acct(1)).holds, advance_ms=1_000)
    snap = w.settle(lambda s: not s.scheduler.ready, advance_ms=1_000)
    assert account_view(snap, acct(1)).phase is AccountPhase.HOLD
    assert w.venues[acct(1)].writes() == []


def test_read_still_in_flight_past_its_deadline_becomes_a_sticky_hold(world):
    w = world(refresh_period_ms=1_000, evidence_deadline_ms=2_000, proof_max_age_ms=12_000)
    w.boot()
    w.settle(ready)
    venue = w.venues[acct(1)]
    venue.entered.clear()
    venue.block = threading.Event()
    w.settle(lambda s: venue.entered.is_set(), advance_ms=1_000)
    w.mono.advance(5_000)
    w.wall.advance(5_000)
    venue.block.set()
    venue.block = None
    snap = w.settle(lambda s: 'worker_reconcile_before_restart' in account_view(s, acct(1)).holds,
                    advance_ms=500)
    w.settle(lambda s: not s.scheduler.ready, advance_ms=500)
    assert venue.writes() == []


def test_two_accounts_are_independent_and_a_held_one_blocks_global_readiness(world):
    w = world(2)
    w.truths[acct(2)].venue_positions = (VenuePosition('ETHUSDT', Side.LONG, D('1')),)
    rt = w.boot()
    a1, a2 = account_view(rt.snapshot(), acct(1)), account_view(rt.snapshot(), acct(2))
    assert a1.restore_proven and a1.holds == ()
    assert a2.holds == ('reconcile_hold',)
    snap = w.settle(lambda s: account_view(s, acct(1)).phase is AccountPhase.PROVEN
                    and next(x for x in s.scheduler.accounts if x.account_id == acct(1)).ready)
    assert not snap.scheduler.ready  # cycle is gated on every account being ready


# ------------------------------------------------------------------------------------------------ manage
def _evaluation(*, protection_status=ProtectionStatus.WORKING):
    lot = LotState(lot_id='lot_' + 'a' * 32, strategy_id='strategy-a', generation=5, fresh=True,
                   side=PositionSide.LONG, quantity=D('10'), initial_quantity=D('10'), entry_price=D('100'),
                   initial_stop=D('95'), initial_risk_per_unit=D('5'), completed_target_levels=(),
                   completed_risk_levels=(), breakeven_applied=False, trail_applied=False,
                   completed_bars_held=10, add_count=0, cumulative_add_risk=D('0'))
    policy = ManagementPolicy(policy_id='management-v1', version='v1', generation=7, fresh=True,
                              tick_size=D('.1'), quantity_step=D('.1'), min_quantity=D('.1'), targets=(),
                              preserve_runner=True, runner_quantity=D('2'), breakeven_trigger_r=D('100'),
                              breakeven_offset_ticks=D('1'), trail_trigger_r=D('200'), trail_distance_r=D('1'),
                              max_holding_bars=100, max_add_count=0, max_single_add_risk=D('0'),
                              max_cumulative_add_risk=D('0'), risk_increases=())
    market = MarketObservation(observation_id='bar_' + 'b' * 32, generation=11, fresh=True, completed=True,
                               high=D('101'), low=D('99'), mark=D('100'), path_order=PathOrder.NONE,
                               forced_exit_confirmed=False)
    if protection_status in (ProtectionStatus.MISSING, ProtectionStatus.UNKNOWN):
        protection = ProtectionState(protection_id='protect_' + 'c' * 32, generation=13, fresh=True,
                                     status=protection_status, confirmed_stop=None,
                                     confirmed_quantity=D('0'), confirmed_target_levels=())
    else:
        protection = ProtectionState(protection_id='protect_' + 'c' * 32, generation=13, fresh=True,
                                     status=ProtectionStatus.WORKING, confirmed_stop=D('95'),
                                     confirmed_quantity=D('10'), confirmed_target_levels=())
    return ManagementEvaluation(
        lot_generation=5, policy_generation=7, market_generation=11, protection_generation=13,
        strategy_exit_generation=17, pending_generation=19, lot=lot, policy=policy, market=market,
        protection=protection,
        strategy_exit=StrategyExitFact(version='signal-v1', generation=17, fresh=True, exit_requested=False),
        pending=PendingSummary(generation=19, fresh=True, kind=PendingKind.NONE))


class Lots:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.calls = rows, error, 0

    def evaluations(self, account_id, now_mono):
        self.calls += 1
        if self.error:
            raise self.error
        return self.rows


class Sink:
    def __init__(self, answer=False, error=None):
        self.answer, self.error = answer, error
        self.management, self.entries = [], []

    def accept_management(self, account_id, plan):
        self.management.append(plan)
        if self.error:
            raise self.error
        return self.answer

    def accept_entry(self, account_id, decision, evaluation):
        self.entries.append(decision)
        return self.answer


def test_quiet_lot_is_traced_as_why_held_and_stays_ready(world):
    w = world()
    w.extra_ports[acct(1)] = {'management': Lots((_evaluation(),))}
    w.boot()
    snap = w.settle(lambda s: s.scheduler.ready and any(e.code.startswith('why_held:') for e in s.events))
    assert snap.scheduler.ready


def test_missing_protection_with_the_default_refusing_sink_holds_the_account(world):
    w = world()
    w.extra_ports[acct(1)] = {'management': Lots((_evaluation(protection_status=ProtectionStatus.MISSING),))}
    w.boot()
    snap = w.settle(lambda s: 'management_unrouted' in account_view(s, acct(1)).holds)
    assert not snap.scheduler.ready
    assert w.venues[acct(1)].writes() == []


@pytest.mark.parametrize('sink, code', [(Sink(answer='yes'), 'management_unrouted'),
                                        (Sink(error=RuntimeError('x')), 'action_sink_error')])
def test_only_a_literal_true_from_the_sink_counts_as_routed(world, sink, code):
    w = world()
    w.extra_ports[acct(1)] = {'management': Lots((_evaluation(protection_status=ProtectionStatus.MISSING),)),
                              'actions': sink}
    w.boot()
    w.settle(lambda s: code in account_view(s, acct(1)).holds)
    assert sink.management


def test_accepted_management_action_does_not_hold(world):
    sink = Sink(answer=True)
    w = world()
    w.extra_ports[acct(1)] = {'management': Lots((_evaluation(protection_status=ProtectionStatus.MISSING),)),
                              'actions': sink}
    w.boot()
    snap = w.settle(lambda s: s.scheduler.ready and sink.management)
    assert account_view(snap, acct(1)).holds == ()


@pytest.mark.parametrize('lots', [Lots(error=RuntimeError('feed')), Lots(rows=[1]), Lots(rows=('x',))])
def test_management_input_errors_hold(world, lots):
    w = world()
    w.extra_ports[acct(1)] = {'management': lots}
    w.boot()
    snap = w.settle(lambda s: 'management_input_invalid' in account_view(s, acct(1)).holds)
    assert not snap.scheduler.ready


# ------------------------------------------------------------------------------------------------ cycle
class FakeEvaluation:
    pass


class FakeApproved:
    pass


class Candidates:
    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def candidates(self, account_id, now_mono):
        self.calls += 1
        return self.rows


@pytest.fixture
def fake_nc06(monkeypatch):
    seen = []

    def evaluate(evaluation):
        seen.append(evaluation)
        return FakeApproved()

    monkeypatch.setattr(rt_module, 'nc06', types.SimpleNamespace(
        RiskEvaluation=FakeEvaluation, ApprovedDecision=FakeApproved, evaluate=evaluate))
    return seen


def test_entries_paused_by_default_never_consult_the_entry_source_or_risk(world, fake_nc06):
    source = Candidates((FakeEvaluation(),))
    w = world()
    w.extra_ports[acct(1)] = {'entries': source}
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    w.settle(lambda s: s.scheduler.generation > 300, advance_ms=1_000)
    assert source.calls == 0 and fake_nc06 == []


def test_enabled_entries_run_nc06_and_an_unrouted_approval_places_nothing(world, fake_nc06):
    source, sink = Candidates((FakeEvaluation(),)), Sink(answer=False)
    w = world(entries_enabled=True)
    w.extra_ports[acct(1)] = {'entries': source, 'actions': sink}
    w.boot()
    w.settle(ready)
    snap = w.settle(lambda s: any(e.code == 'entry_unrouted' for e in s.events), advance_ms=5_000)
    assert source.calls >= 1 and len(fake_nc06) >= 1 and sink.entries
    assert w.venues[acct(1)].writes() == []
    assert account_view(snap, acct(1)).holds == ()


def test_enabled_entries_are_held_while_any_account_is_held(world, fake_nc06):
    source = Candidates((FakeEvaluation(),))
    w = world(2, entries_enabled=True)
    w.truths[acct(2)].venue_positions = (VenuePosition('ETHUSDT', Side.LONG, D('1')),)
    w.extra_ports[acct(1)] = {'entries': source}
    w.boot()
    w.settle(lambda s: s.scheduler.generation > 200, advance_ms=1_000)
    assert source.calls == 0 and fake_nc06 == []


def test_invalid_candidates_fail_the_cycle_without_calling_risk(world, fake_nc06):
    w = world(entries_enabled=True)
    w.extra_ports[acct(1)] = {'entries': Candidates(['x'])}
    w.boot()
    w.settle(ready)
    w.settle(lambda s: any(l.lane.value == 'cycle' and l.failure.value == 'hook_failed'
                           for a in s.scheduler.accounts for l in a.lanes), advance_ms=5_000)
    assert fake_nc06 == []


# ------------------------------------------------------------------------------------------------ wiring guards
def test_boot_refuses_mismatched_or_shared_wiring(world):
    w = world(2)
    ports = w.ports()
    with pytest.raises(RuntimeBootError, match='ports_accounts_mismatch'):
        boot(w.config, {acct(1): ports[acct(1)]}, boot_id='b', monotonic_clock=w.mono, wall_clock=w.wall)
    swapped = {acct(1): ports[acct(2)], acct(2): ports[acct(1)]}
    with pytest.raises(RuntimeBootError, match='context_account_mismatch'):
        boot(w.config, swapped, boot_id='b', monotonic_clock=w.mono, wall_clock=w.wall)
    with pytest.raises(RuntimeBootError, match='config_invalid'):
        boot(object(), ports, boot_id='b', monotonic_clock=w.mono, wall_clock=w.wall)


def test_hooks_refuse_a_foreign_account_id(world):
    w = world()
    rt = w.boot()
    hooks = rt._scheduler._bindings[acct(1)].hooks
    for lane in ('guards', 'manage', 'cycle'):
        with pytest.raises(ValueError, match='hook_account_mismatch'):
            getattr(hooks, lane)(acct(2), 1)


def test_stop_then_close_releases_the_journal_and_close_before_stop_is_refused(world):
    w = world()
    rt = w.boot()
    w.settle(ready)
    with pytest.raises(RuntimeError, match='runtime_not_stopped'):
        rt.close()
    rt.stop(w.mono() + 1_000)
    w.settle(lambda s: s.scheduler.state is SchedulerState.STOPPED)
    rt.close()
    spec = w.specs[0]
    again = recover_journal(spec.account_dir, spec.account_id, spec.aggregate_id)
    assert again.verdict is Verdict.CLEAN
    again.journal.close()
    w.runtime = None


def test_snapshots_are_immutable_values(world):
    w = world()
    rt = w.boot()
    snap = rt.snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.entries_enabled = True
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.accounts[0].holds = ()
    assert type(snap.accounts) is tuple and type(snap.events) is tuple


def test_event_log_is_bounded_and_counts_drops():
    log = rt_module._EventLog(capacity=3)
    for i in range(5):
        log.record(acct(1), 'k', 'c', i)
    rows = log.rows()
    assert len(rows) == 3 and log.dropped == 2 and [r.sequence for r in rows] == [3, 4, 5]
