"""RT-01 regression tests for each guard line the mutation check found unpinned."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rt01_fixtures import D, World, account_view, acct  # noqa: E402

import newcore.runtime.runtime as rt_module  # noqa: E402
from newcore.account_context import OpenOrdersRequest, Operation, PositionsRequest, VenueWork  # noqa: E402
from newcore.domain import Action  # noqa: E402
from newcore.management import ProtectionStatus  # noqa: E402
from newcore.domain.orders import Side  # noqa: E402
from newcore.ports.venue import OrderRef  # noqa: E402
from newcore.reconciliation import VenuePosition  # noqa: E402
from newcore.runtime import HoldCode  # noqa: E402
from newcore.store import Verdict  # noqa: E402

from test_rt01_runtime import Candidates, FakeApproved, FakeEvaluation, Lots, Sink, _evaluation  # noqa: E402


@pytest.fixture
def world(tmp_path):
    made = []

    def make(n=1, **kwargs):
        w = World(tmp_path, n, **kwargs)
        made.append(w)
        return w

    yield make
    for w in made:
        try:
            w.shutdown()
        except Exception:
            pass


def _row(rt, n=1):
    return rt._accounts[acct(n)]


def _hooks(rt, n=1):
    return rt._scheduler._bindings[acct(n)].hooks


# ------------------------------------------------------------------------------------------------ boot
@pytest.mark.parametrize('verdict', [Verdict.MISSING, Verdict.DAMAGED, Verdict.UNREADABLE, Verdict.ABORT_RO])
def test_a_non_clean_verdict_holds_even_if_a_journal_object_is_returned(world, verdict):
    w = world()

    class Journal:
        def state(self):
            raise AssertionError('a held journal must not be read')

        def close(self):
            pass

    rt = w.boot(recover=lambda *_a: types.SimpleNamespace(verdict=verdict, journal=Journal()))
    view = account_view(rt.snapshot(), acct(1))
    assert view.holds == (f'journal_{verdict.value}',) and not view.restore_proven
    assert w.venues[acct(1)].calls == []


def _protect_read(prefix, now, deadline):
    return VenueWork(work_id=f'{prefix}-pr', action=Action.PROTECT, operation=Operation.POSITIONS,
                     payload=PositionsRequest(), enqueued_at_ms=now, deadline_ms=deadline)


def _reconcile_cancel(prefix, now, deadline):
    return VenueWork(work_id=f'{prefix}-rc', action=Action.RECONCILE, operation=Operation.CANCEL,
                     payload=OrderRef(symbol='BTCUSDT', client_id='zbn1o-1', route='classic'),
                     enqueued_at_ms=now, deadline_ms=deadline)


def _late_read(prefix, now, deadline):
    return VenueWork(work_id=f'{prefix}-late', action=Action.RECONCILE, operation=Operation.OPEN_ORDERS,
                     payload=OpenOrdersRequest(symbol='ETHUSDT'), enqueued_at_ms=now, deadline_ms=deadline + 1)


def _duplicate(prefix, now, deadline):
    return VenueWork(work_id=f'{prefix}-p', action=Action.RECONCILE, operation=Operation.POSITIONS,
                     payload=PositionsRequest(), enqueued_at_ms=now, deadline_ms=deadline)


@pytest.mark.parametrize('extra', [_protect_read, _reconcile_cancel, _late_read, _duplicate])
def test_evidence_work_must_be_reconcile_reads_within_deadline_and_unique(world, extra):
    w = world()
    w.evidence[acct(1)].extra_work = extra
    rt = w.boot()
    assert account_view(rt.snapshot(), acct(1)).holds == ('evidence_work_invalid',)
    assert w.venues[acct(1)].calls == []


def test_boot_holds_when_the_worker_returns_no_result(world):
    w = world()
    ports = w.ports()
    ports[acct(1)].context.run_next = lambda: None  # a worker that never runs the queued reads
    rt = rt_module.boot(w.config, ports, boot_id='b', monotonic_clock=w.mono, wall_clock=w.wall)
    w.runtime = rt
    assert account_view(rt.snapshot(), acct(1)).holds == ('evidence_unavailable',)
    assert w.venues[acct(1)].calls == []


def test_boot_holds_when_the_worker_latched_reconcile_before_restart(world):
    w = world()
    ports = w.ports()
    context = ports[acct(1)].context
    real = context.snapshot

    def latched():
        import dataclasses
        return dataclasses.replace(real(), reconcile_before_restart=True)

    context.snapshot = latched
    rt = rt_module.boot(w.config, ports, boot_id='b', monotonic_clock=w.mono, wall_clock=w.wall)
    w.runtime = rt
    view = account_view(rt.snapshot(), acct(1))
    assert view.holds == ('worker_reconcile_before_restart',) and not view.restore_proven


# ------------------------------------------------------------------------------------------------ guards
def test_sticky_hold_never_folds_pending_evidence(world):
    w = world()
    rt = w.boot()
    row = _row(rt)
    before = w.evidence[acct(1)].assembled
    row.holds.add(HoldCode.DURABLE_STATE_INVALID)
    row.pending = rt_module._Pending(99, ('rt-boot-p', ), w.mono() + 10_000)
    row.ports.context.result = lambda _id: object()  # pretend the read completed
    assert _hooks(rt).guards(acct(1), w.mono()).status.value == 'refused'
    assert w.evidence[acct(1)].assembled == before and row.pending is not None


def test_clearable_hold_with_a_fresh_proof_still_refuses_guards(world):
    w = world()
    rt = w.boot()
    row = _row(rt)
    row.holds.add(HoldCode.EVIDENCE_UNAVAILABLE)
    row.last_refresh_mono = w.mono()  # no refresh due
    assert row.proof_mono is not None
    assert _hooks(rt).guards(acct(1), w.mono()).status.value == 'refused'


def test_confirmed_fold_clears_only_clearable_holds(world):
    w = world()
    rt = w.boot()
    row = _row(rt)
    evidence = w.evidence[acct(1)]
    real = evidence.assemble

    def assemble_while_manage_holds(*args):
        row.holds.add(HoldCode.MANAGEMENT_UNROUTED)  # concurrent manage hook on the same account
        return real(*args)

    evidence.assemble = assemble_while_manage_holds
    row.holds.add(HoldCode.RECONCILE_HOLD)
    row.pending = rt_module._Pending(7, ('w1',), w.mono() + 10_000)
    row.ports.context.result = lambda _id: rt_module.WorkResult(work_id='w1', state=rt_module.WorkState.COMPLETED,
                                                                detail='one_shot')
    _hooks(rt).guards(acct(1), w.mono())
    assert row.holds == {HoldCode.MANAGEMENT_UNROUTED}


def test_restore_mark_waits_for_a_clean_account_then_happens_once(world):
    w = world()
    w.truths[acct(1)].venue_positions = (VenuePosition('BTCUSDT', Side.LONG, D('1')),)  # boot: reconcile_hold
    rt = w.boot()
    row = _row(rt)
    assert not row.restore_proven
    row.restore_mark_due = True
    row.holds = {HoldCode.MANAGEMENT_UNROUTED}  # a hold that appeared after the proof
    rt.step()
    assert rt._scheduler._states[acct(1)].restore_proven is False and row.restore_mark_due
    row.holds = set()
    rt.step()
    assert rt._scheduler._states[acct(1)].restore_proven is True
    assert [e.code for e in rt.snapshot().events if e.kind == 'restore'] == ['proven']


def test_reads_that_never_complete_time_out_into_a_hold(world):
    w = world(refresh_period_ms=1_000, evidence_deadline_ms=2_000, proof_max_age_ms=12_000)
    rt = w.boot()
    w.settle(lambda s: s.scheduler.ready)
    row = _row(rt)
    row.ports.context.result = lambda _id: None  # results never become visible
    snap = w.settle(lambda s: 'evidence_timeout' in account_view(s, acct(1)).holds, advance_ms=1_000)
    assert account_view(snap, acct(1)).proof_mono is None


# ------------------------------------------------------------------------------------------------ manage / cycle
def _invalid_lot():
    import dataclasses
    row = _evaluation()
    return dataclasses.replace(row, lot=dataclasses.replace(row.lot, quantity=D('-1')))


@pytest.mark.parametrize('make', [lambda: _evaluation(protection_status=ProtectionStatus.UNKNOWN), _invalid_lot])
def test_unknown_protection_or_invalid_input_is_unresolved_not_traced(world, make):
    w = world()
    w.extra_ports[acct(1)] = {'management': Lots((make(),))}
    w.boot()
    snap = w.settle(lambda s: 'management_unresolved' in account_view(s, acct(1)).holds)
    assert not any(e.code.startswith('why_held:') for e in snap.events)


def test_cycle_refuses_when_the_published_scheduler_is_not_ready(world, monkeypatch):
    seen = []
    monkeypatch.setattr(rt_module, 'nc06', types.SimpleNamespace(
        RiskEvaluation=FakeEvaluation, ApprovedDecision=FakeApproved, evaluate=lambda e: seen.append(e)))
    source = Candidates((FakeEvaluation(),))
    w = world(entries_enabled=True)
    w.extra_ports[acct(1)] = {'entries': source}
    rt = w.boot()  # proven at boot, but the scheduler has not published READY yet
    assert not rt.snapshot().scheduler.ready
    assert _hooks(rt).cycle(acct(1), w.mono()).status.value == 'refused'
    assert source.calls == 0 and seen == []


def test_refused_risk_decision_never_reaches_the_sink(world, monkeypatch):
    class Refused:
        pass

    monkeypatch.setattr(rt_module, 'nc06', types.SimpleNamespace(
        RiskEvaluation=FakeEvaluation, ApprovedDecision=FakeApproved, evaluate=lambda e: Refused()))
    sink = Sink(answer=True)
    w = world(entries_enabled=True)
    w.extra_ports[acct(1)] = {'entries': Candidates((FakeEvaluation(),)), 'actions': sink}
    w.boot()
    w.settle(lambda s: s.scheduler.ready)
    w.settle(lambda s: any(e.code == 'entry_refused' for e in s.events), advance_ms=5_000)
    assert sink.entries == []
