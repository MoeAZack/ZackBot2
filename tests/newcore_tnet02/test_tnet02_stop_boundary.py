"""Run-7 regressions: a stop may trigger between the final algo read and the position snapshot."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from newcore.testnet_harness import (CLEANUP_FLATTEN_MS, CLEANUP_HOLD_MS, CLEANUP_PROTECT_MS, CleanupContext,
                                     FailureCode, FinalTruth, ScenarioKind, _case_context, _derive,
                                     _retryable_miss, _scenario_failure)
from newcore.tnet02.adapter import AdapterError, HarnessConfig

from .fake_binance import ok
from .support import T0, manifest, rig


def _prepared(tmp_path, kind: ScenarioKind, *, settle_polls: int = 4):
    r = rig(tmp_path, price_path=lambda _now, symbol: Decimal("60000") if symbol == "BTCUSDT" else Decimal("3000"),
            config=HarnessConfig(poll_ms=1000, settle_polls=settle_polls))
    spec = manifest()
    r.adapter.confirm_binding(spec.binding)
    case = next(case for case in spec.cases if case.kind is kind)
    context = _case_context(spec, _derive("suite", "stop-boundary", kind.value), case, T0)
    setup = r.adapter.setup(context)
    return r, context, setup


def _cleanup_context(context) -> CleanupContext:
    started = context.case_started_budget_ms + 1
    return CleanupContext(context.suite_id, context.case.case_id,
                          _derive("cleanup", context.suite_id, context.case.case_id), started,
                          started + CLEANUP_PROTECT_MS, started + CLEANUP_FLATTEN_MS,
                          started + CLEANUP_HOLD_MS)


def _writes(r):
    return [request for request in r.fake.requests if request[0] in ("POST", "DELETE")]


def _arm_boundary_trigger(r):
    """Trigger the one owned stop inside the next position snapshot, after its preceding WORKING read."""
    real = r.fake._get_fapi_v2_positionRisk
    state = {"armed": True, "fired": 0}

    def position(params):
        live = [algo for algo in r.fake.algos.values() if algo.status in ("NEW", "WORKING")]
        if state["armed"] and state["fired"] == 0 and live:
            algo = live[0]
            r.fake.price_path = None
            r.fake.marks[algo.symbol] = algo.trigger
            r.fake.tick()
            state["fired"] += 1
        return real(params)

    r.fake._get_fapi_v2_positionRisk = position
    return state


def _journal_bodies(r, context):
    path = r.journal_dir / f"{context.case.case_id}.journal"
    return [json.loads(line)["body"] for line in path.read_text(encoding="ascii").splitlines()]


def test_stop_fills_after_final_working_read_and_finishes_flat_without_cleanup_mutation(tmp_path):
    r, context, _setup = _prepared(tmp_path, ScenarioKind.LONG_STOP)
    r.adapter._trigger_deadline = lambda _case: r.clock.now       # reach finalization without a price-path trigger
    state = _arm_boundary_trigger(r)

    evidence = r.adapter.execute(context)
    before = list(_writes(r))
    cleanup = r.adapter.cleanup(_cleanup_context(context))
    bodies = _journal_bodies(r, context)

    assert state["fired"] == 1
    assert evidence.stop_confirmed and not evidence.trigger_missed
    assert evidence.orders_placed == 2 and evidence.final_truth is FinalTruth.FLAT
    assert cleanup.final_truth is FinalTruth.FLAT and cleanup.cleanup_orders_total == 0
    assert _writes(r) == before
    assert any(row["kind"] == "order" and row.get("purpose") == "protect" and row.get("state") == "filled"
               for row in bodies)
    assert sum(row["kind"] == "position" and Decimal(row["executed_qty"]) == 0 for row in bodies) == 1


def test_triggered_child_fill_on_a_later_poll_is_absorbed_exactly_once(tmp_path):
    r, context, _setup = _prepared(tmp_path, ScenarioKind.LONG_STOP)
    r.adapter._trigger_deadline = lambda _case: r.clock.now
    _arm_boundary_trigger(r)
    real = r.fake._get_fapi_v1_userTrades
    calls = {"fills": 0}

    def delayed(params):
        calls["fills"] += 1
        return ok([]) if calls["fills"] == 1 else real(params)

    r.fake._get_fapi_v1_userTrades = delayed
    evidence = r.adapter.execute(context)
    bodies = _journal_bodies(r, context)
    assert evidence.stop_confirmed and evidence.final_truth is FinalTruth.FLAT and calls["fills"] == 2
    assert sum(row["kind"] == "position" and Decimal(row["executed_qty"]) == 0 for row in bodies) == 1


def test_triggered_child_that_stays_unsettled_holds_without_a_second_order(tmp_path):
    r, context, _setup = _prepared(tmp_path, ScenarioKind.LONG_STOP, settle_polls=3)
    r.adapter._trigger_deadline = lambda _case: r.clock.now
    _arm_boundary_trigger(r)
    r.fake._get_fapi_v1_userTrades = lambda _params: ok([])
    with pytest.raises(AdapterError, match="refresh.position_unsettled"):
        r.adapter.execute(context)
    assert len(_writes(r)) == 2       # entry + stop only; no close or cancel while the child is unsettled


def test_same_boundary_during_cleanup_is_flat_idempotent_and_sends_nothing(tmp_path):
    r, context, _setup = _prepared(tmp_path, ScenarioKind.LONG_TARGET)
    r.adapter._wait_for_plan = lambda *_args, **_kwargs: None
    evidence = r.adapter.execute(context)
    assert evidence.final_truth is FinalTruth.PROTECTED_RECONCILED
    _arm_boundary_trigger(r)
    cleanup_context = _cleanup_context(context)
    before = list(_writes(r))
    first = r.adapter.cleanup(cleanup_context)
    second = r.adapter.cleanup(cleanup_context)
    assert first == second and first.final_truth is FinalTruth.FLAT and first.cleanup_orders_total == 0
    assert _writes(r) == before


def test_unexplained_lower_position_with_working_stop_holds_and_halts_without_mutation(tmp_path):
    r, context, _setup = _prepared(tmp_path, ScenarioKind.LONG_TARGET, settle_polls=3)
    r.adapter._wait_for_plan = lambda *_args, **_kwargs: None
    real = r.fake._get_fapi_v2_positionRisk

    def unexplained(params):
        if any(algo.status in ("NEW", "WORKING") for algo in r.fake.algos.values()):
            r.fake.positions[(context.case.symbol, context.case.side.value)] = Decimal("0")
        return real(params)

    r.fake._get_fapi_v2_positionRisk = unexplained
    with pytest.raises(AdapterError, match="refresh.position_unsettled"):
        r.adapter.execute(context)
    before_cleanup = list(_writes(r))
    cleanup = r.adapter.cleanup(_cleanup_context(context))
    assert cleanup.final_truth is FinalTruth.HOLD and r.adapter._halted
    assert _writes(r) == before_cleanup and len(before_cleanup) == 2
    assert any(note["reason"] == "adapter.refresh.position_unsettled" for note in r.adapter.notes)


@pytest.mark.parametrize("kind", [ScenarioKind.LONG_TARGET, ScenarioKind.BOUNDED_DCA])
def test_boundary_stop_in_target_or_dca_miss_is_confirmed_and_never_retryable(tmp_path, kind):
    r, context, setup = _prepared(tmp_path, kind)
    r.adapter._wait_for_plan = lambda *_args, **_kwargs: None
    _arm_boundary_trigger(r)
    evidence = r.adapter.execute(context)
    failure = _scenario_failure(context, setup, evidence)
    cleanup = r.adapter.cleanup(_cleanup_context(context))
    assert evidence.stop_confirmed and not evidence.trigger_missed and evidence.final_truth is FinalTruth.FLAT
    assert failure is FailureCode.ASSERTION
    assert not _retryable_miss(context, evidence, cleanup, failure, None, cleanup)
