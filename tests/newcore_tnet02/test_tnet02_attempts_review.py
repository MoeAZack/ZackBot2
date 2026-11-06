"""Regressions for the adversarial review of the further-attempts rule (d11af28).

- F1: a cleanup fault after a market miss was masked by ``failure or ...``, and the miss was retried.
- F2: any ASSERTION counted as a market miss, including a model protection HOLD that was noted as "no_trigger".
- F3: retries kept spending orders after another chain was already lost.
- F4: a trigger attempt could place its entry with no waiting window left.
- F5: the diagnostics side file followed symlinks.
"""
from __future__ import annotations

import dataclasses
import json
import os
import types

import pytest

from newcore import management as M
from newcore.testnet_harness import (CaseStatus, FailureCode, FinalTruth, ScenarioKind, SuiteStatus, run_suite)
from newcore.tnet02.adapter import AdapterError
from newcore.tnet02.gate import gate_proof

from .support import BUILD_SHA, manifest, rig
from .test_tnet02_attempts import _quiet_for


def _tamper_first_long_target_cleanup(r, how):
    state = {"target_case": None, "calls": 0}
    execute = r.adapter.execute

    def ex(context):
        if context.case.kind is ScenarioKind.LONG_TARGET and state["target_case"] is None:
            state["target_case"] = context.case.case_id
        return execute(context)
    r.adapter.execute = ex
    cleanup = r.adapter.cleanup

    def cl(ctx):
        ev = cleanup(ctx)
        if ctx.case_id == state["target_case"]:
            state["calls"] += 1
            return how(ctx, ev, state["calls"])
        return ev
    r.adapter.cleanup = cl


def _non_idempotent(ctx, ev, n):
    return ev if n == 1 else dataclasses.replace(ev, cleanup_orders_total=ev.cleanup_orders_total + 1)


def _second_pass_hold(ctx, ev, n):
    return ev if n == 1 else dataclasses.replace(ev, final_truth=FinalTruth.HOLD, reconciled=False,
                                                 flattened_at_ms=None, hold_stopped_at_ms=ev.started_ms + 1)


def _wrong_token(ctx, ev, n):
    return dataclasses.replace(ev, token="cleanup_" + "f" * 32)


def _over_order_bound(ctx, ev, n):
    return dataclasses.replace(ev, cleanup_orders_total=11)


@pytest.mark.parametrize("how, held", [(_non_idempotent, True), (_second_pass_hold, True), (_wrong_token, True),
                                       (_over_order_bound, False)],
                         ids=["non_idempotent", "second_pass_hold", "wrong_token", "over_order_bound"])
def test_F1_cleanup_fault_after_a_miss_is_never_retried_or_hidden(tmp_path, how, held):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_TARGET}, 1)
    _tamper_first_long_target_cleanup(r, how)
    out = []
    report = run_suite(manifest(), r.adapter, out.append)
    first = report.cases[0]
    assert not any(c.attempt > 1 for c in report.cases)
    assert report.status is not SuiteStatus.PASSED
    assert gate_proof(out[0], expected_head=BUILD_SHA, host_id="tnet_test").passed is False
    if held:      # an untrusted cleanup terminal is reported as HOLD, even though the scenario had already failed
        assert first.status is CaseStatus.HOLD and first.final_truth is FinalTruth.HOLD


@pytest.mark.parametrize("kind", [ScenarioKind.LONG_TARGET, ScenarioKind.BOUNDED_DCA, ScenarioKind.PARTIAL_CLOSE])
@pytest.mark.parametrize("reason", [M.PlanReason.PROTECTION_MISSING, M.PlanReason.PROTECTION_UNKNOWN,
                                    M.PlanReason.FLATTEN_REQUIRED])
def test_F2_model_safety_hold_is_a_fault_not_a_market_miss(tmp_path, kind, reason):
    r = rig(tmp_path)
    state = {"runs": 0}
    execute = r.adapter.execute

    def ex(context):
        if context.case.kind is kind:
            state["runs"] += 1
        return execute(context)
    r.adapter.execute = ex
    real = r.adapter._manage

    def manage(case, **kw):
        plan = real(case, **kw)
        if case.context.case.kind is kind and state["runs"] == 1:
            return types.SimpleNamespace(status=M.PlanStatus.HOLD, reason=reason, actions=())
        return plan
    r.adapter._manage = manage
    out = []
    report = run_suite(manifest(), r.adapter, out.append)
    chain = [(c.attempt, c.failure) for c in report.cases if c.kind is kind]
    notes = [n["reason"] for n in r.adapter.notes if n["kind"] == kind.value]
    assert chain == [(1, FailureCode.ADAPTER)]
    assert notes == ["adapter.manage.hold"]
    assert report.status is not SuiteStatus.PASSED
    assert gate_proof(out[0], expected_head=BUILD_SHA, host_id="tnet_test").passed is False


def test_F2_reached_level_without_a_reduce_is_not_a_miss(tmp_path):
    r = rig(tmp_path)
    real = r.adapter._wait_for_plan
    first = {"done": False}

    def wait(case, deadline, **kw):
        if case.context.case.kind is ScenarioKind.LONG_TARGET and not first["done"]:
            first["done"] = True
            return types.SimpleNamespace(status=M.PlanStatus.ACTIONS, actions=())     # level reached, no reduce
        return real(case, deadline, **kw)
    r.adapter._wait_for_plan = wait
    report = run_suite(manifest(), r.adapter, [].append)
    assert [c.attempt for c in report.cases if c.kind is ScenarioKind.LONG_TARGET] == [1]
    assert [n["reason"] for n in r.adapter.notes if n["kind"] == "long_target"] == ["target.no_reduce"]


def test_F2_a_missed_flag_on_a_non_trigger_or_passing_case_is_rejected(tmp_path):
    from newcore import testnet_harness as h
    r = rig(tmp_path)
    execute = r.adapter.execute

    def ex(context):
        ev = execute(context)
        return dataclasses.replace(ev, trigger_missed=True) if context.case.kind is ScenarioKind.LONG_TARGET else ev
    r.adapter.execute = ex
    report = run_suite(manifest(), r.adapter, [].append)
    target = report.cases[0]
    assert target.failure is FailureCode.ASSERTION              # a "missed" flag beside a confirmed target
    assert [c.attempt for c in report.cases if c.kind is ScenarioKind.LONG_TARGET] == [1]
    assert h.RETRYABLE_SCENARIOS


def _quiet_counts(r, counts):
    from decimal import Decimal
    from .support import wave
    moving = wave()
    state = {"quiet": False, "seen": {}}

    def path(now, symbol):
        return (Decimal("60000") if symbol == "BTCUSDT" else Decimal("3000")) if state["quiet"] else moving(now, symbol)
    r.fake.price_path = path
    execute = r.adapter.execute

    def wrapped(context):
        kind = context.case.kind
        state["seen"][kind] = state["seen"].get(kind, 0) + 1
        state["quiet"] = state["seen"][kind] <= counts.get(kind, 0)
        r.fake.tick()
        try:
            return execute(context)
        finally:
            state["quiet"] = False
    r.adapter.execute = wrapped


def test_F3_no_retry_after_another_chain_is_lost(tmp_path):
    r = rig(tmp_path)
    _quiet_counts(r, {ScenarioKind.LONG_TARGET: 3, ScenarioKind.LONG_STOP: 1})
    report = run_suite(manifest(), r.adapter, [].append)
    tail = [(c.kind, c.attempt) for c in report.cases[12:]]
    assert tail == [(ScenarioKind.LONG_TARGET, 2), (ScenarioKind.LONG_TARGET, 3)]
    assert report.status is SuiteStatus.FAILED


def test_F4_no_entry_without_a_real_waiting_window(tmp_path):
    r = rig(tmp_path)
    case = types.SimpleNamespace(context=types.SimpleNamespace(case_deadline_ms=r.adapter._now() + 90_000))
    before = len(r.fake.requests)
    with pytest.raises(AdapterError) as caught:
        r.adapter._require_window(case)
    assert caught.value.code == "window.too_short" and len(r.fake.requests) == before
    roomy = types.SimpleNamespace(context=types.SimpleNamespace(case_deadline_ms=r.adapter._now() + 600_000))
    r.adapter._require_window(roomy)


def test_F4_kernel_never_starts_an_attempt_without_a_full_scenario_window(tmp_path, monkeypatch):
    from newcore import testnet_harness as h
    monkeypatch.setattr(h, "MAX_SUITE_MS", 25 * 60 * 1000)
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.BOUNDED_DCA}, 1)
    report = run_suite(manifest(), r.adapter, [].append)
    retries = [c for c in report.cases if c.attempt > 1]
    for c in retries:
        assert c.started_ms + h.MAX_SCENARIO_MS <= report.started_ms + 25 * 60 * 1000


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_F5_diagnostics_never_follow_a_planted_symlink(tmp_path, capsys):
    from tools import tnet02_run as cli
    head, at = "d" * 40, 77
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    link = tmp_path / f"diagnostics-{head[:12]}-{at}.json"
    try:
        os.symlink(victim, link)
    except OSError:
        pytest.skip("symlink not permitted")
    cli._write_diagnostics(tmp_path, head, at, ())
    assert victim.read_text(encoding="utf-8") == "keep"
    assert "DIAGNOSTICS not written" in capsys.readouterr().out
    cli._write_diagnostics(tmp_path, head, at + 1, ())
    assert json.loads((tmp_path / f"diagnostics-{head[:12]}-{at + 1}.json").read_text(encoding="utf-8"))["notes"] == []


def test_F1_differing_cleanup_passes_alone_block_a_retry(tmp_path):
    from .test_tnet02_attempts import _captured_miss
    miss, context, evidence, cleanup, failure = _captured_miss(tmp_path)
    other = dataclasses.replace(cleanup, cleanup_orders_total=cleanup.cleanup_orders_total + 1)
    assert miss(context, evidence, cleanup, failure)
    assert not miss(context, evidence, cleanup, failure, None, other)


def test_F2_dca_level_reached_without_a_proposal_is_not_a_miss(tmp_path):
    r = rig(tmp_path)
    real = r.adapter._wait_for_plan
    first = {"done": False}

    def wait(case, deadline, **kw):
        if case.context.case.kind is ScenarioKind.BOUNDED_DCA and not first["done"]:
            first["done"] = True
            return types.SimpleNamespace(status=M.PlanStatus.ACTIONS, actions=())
        return real(case, deadline, **kw)
    r.adapter._wait_for_plan = wait
    report = run_suite(manifest(), r.adapter, [].append)
    assert [c.attempt for c in report.cases if c.kind is ScenarioKind.BOUNDED_DCA] == [1]
    assert [n["reason"] for n in r.adapter.notes if n["kind"] == "bounded_dca"] == ["dca.no_proposal"]


def test_F2_a_stop_that_ended_other_than_working_is_not_a_miss(tmp_path):
    from newcore.tnet02.ports import IntentState
    r = rig(tmp_path)
    real = r.adapter._open_protected

    def opened(case, flags, *, adverse):
        entry, stop, order = real(case, flags, adverse=adverse)
        if case.context.case.kind is ScenarioKind.LONG_STOP and not getattr(r, "_done", False):
            r._done = True
            order.state = IntentState.REJECTED          # stop no longer working, never triggered
        return entry, stop, order
    r.adapter._open_protected = opened
    evidences = []
    execute = r.adapter.execute

    def ex(context):
        try:
            ev = execute(context)
        except Exception:
            raise
        evidences.append(ev)
        return ev
    r.adapter.execute = ex
    report = run_suite(manifest(), r.adapter, [].append)
    assert [c.attempt for c in report.cases if c.kind is ScenarioKind.LONG_STOP] == [1]
    assert not any(e.trigger_missed for e in evidences if e.kind is ScenarioKind.LONG_STOP)
    assert [n["reason"] for n in r.adapter.notes if n["kind"] == "long_stop"][0] == "stop.unconfirmed"


def test_F4_kernel_skips_a_late_attempt_entirely(tmp_path, monkeypatch):
    from newcore import testnet_harness as h
    limit = 15 * 60 * 1000
    monkeypatch.setattr(h, "MAX_SUITE_MS", limit)
    r = rig(tmp_path)
    _quiet_counts(r, {ScenarioKind.BOUNDED_DCA: 1})
    report = run_suite(manifest(), r.adapter, [].append)
    assert len(report.cases) >= 12
    end_first = report.cases[11].finished_ms
    assert end_first < report.started_ms + limit < end_first + h.MAX_SCENARIO_MS      # precondition: a late window
    assert [c for c in report.cases if c.attempt > 1] == []
    assert report.status is not SuiteStatus.PASSED


@pytest.mark.parametrize("kind", [ScenarioKind.LONG_TARGET, ScenarioKind.PARTIAL_CLOSE, ScenarioKind.LONG_STOP,
                                  ScenarioKind.BOUNDED_DCA])
def test_F4_every_trigger_scenario_checks_its_window_before_any_order(tmp_path, kind):
    r = rig(tmp_path)
    real = r.adapter._require_window

    def short(case):
        if case.context.case.kind is kind:
            raise AdapterError("window.too_short")
        return real(case)
    r.adapter._require_window = short
    report = run_suite(manifest(), r.adapter, [].append)
    case = next(c for c in report.cases if c.kind is kind)
    assert case.failure is FailureCode.ADAPTER and case.orders_total == 0
