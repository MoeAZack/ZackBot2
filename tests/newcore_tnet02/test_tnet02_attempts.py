"""TNET-02 bounded further attempts for market-trigger scenarios (owner decision, 6 Nov 2026).

Four real runs never got all twelve scenarios to pass in one run, because price did not move the needed way in time.
A trigger scenario that misses cleanly may be attempted again after the first pass, up to MAX_ATTEMPTS. Every
attempt is reported, and the last one must be a real pass.
"""
from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

import pytest

from newcore.testnet_harness import (MAX_ATTEMPTS, REQUIRED_SCENARIOS, RETRYABLE_SCENARIOS, CaseReport, CaseStatus,
                                     FailureCode, FinalTruth, HarnessError, ScenarioKind, SuiteStatus,
                                     attempts_complete, canonical_report_bytes, run_suite)
from newcore.tnet02.gate import gate_proof, report_passed

from .support import BUILD_SHA, manifest, rig, wave

FLAT = Decimal("60000")


def _quiet_for(r, kinds: set[ScenarioKind], quiet_attempts: int):
    """Flat price while the first ``quiet_attempts`` executes of any kind in ``kinds`` run, the usual wave otherwise."""
    moving = wave()
    state = {"quiet": False, "seen": {}}

    def path(now, symbol):
        if state["quiet"]:
            return FLAT if symbol == "BTCUSDT" else Decimal("3000")
        return moving(now, symbol)
    r.fake.price_path = path
    execute = r.adapter.execute

    def wrapped(context):
        kind = context.case.kind
        count = state["seen"].get(kind, 0) + 1
        state["seen"][kind] = count
        state["quiet"] = kind in kinds and count <= quiet_attempts
        r.fake.tick()
        try:
            return execute(context)
        finally:
            state["quiet"] = False
    r.adapter.execute = wrapped
    return state


def _run(r):
    out: list[bytes] = []
    report = run_suite(manifest(), r.adapter, out.append)
    return report, out[0]


def test_one_clean_miss_then_a_real_pass_passes_the_suite_and_the_gate(tmp_path):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_TARGET}, 1)
    report, data = _run(r)
    assert report.status is SuiteStatus.PASSED, [(c.kind.value, c.status.value, c.failure) for c in report.cases]
    assert len(report.cases) == 13
    first, again = report.cases[0], report.cases[12]
    assert first.kind is again.kind is ScenarioKind.LONG_TARGET
    assert (first.status, first.failure, first.final_truth) == (CaseStatus.FAILED, FailureCode.ASSERTION,
                                                                FinalTruth.FLAT)
    assert (again.attempt, again.attempt_of, again.status) == (2, first.case_id, CaseStatus.PASSED)
    assert again.case_id != first.case_id and again.started_ms >= report.cases[11].finished_ms
    assert gate_proof(data, expected_head=BUILD_SHA, host_id="tnet_test").passed is True
    # the retry used fresh order ids: no duplicate client id was ever sent
    cids = [q.get("newClientOrderId") for m, p, q in r.fake.requests if m == "POST" and "newClientOrderId" in q]
    assert cids and len(cids) == len(set(cids))


def test_misses_on_every_attempt_fail_honestly_after_the_bound(tmp_path):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.SHORT_TARGET}, MAX_ATTEMPTS)
    report, data = _run(r)
    assert report.status is SuiteStatus.FAILED
    chain = [c for c in report.cases if c.kind is ScenarioKind.SHORT_TARGET]
    assert [c.attempt for c in chain] == [1, 2, 3] and all(c.status is CaseStatus.FAILED for c in chain)
    assert gate_proof(data, expected_head=BUILD_SHA, host_id="tnet_test").passed is False


def test_several_scenarios_each_get_their_own_attempts(tmp_path):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_STOP, ScenarioKind.BOUNDED_DCA}, 2)
    report, data = _run(r)
    assert report.status is SuiteStatus.PASSED
    kinds = [(c.kind, c.attempt) for c in report.cases[12:]]
    assert kinds == [(ScenarioKind.LONG_STOP, 2), (ScenarioKind.LONG_STOP, 3),
                     (ScenarioKind.BOUNDED_DCA, 2), (ScenarioKind.BOUNDED_DCA, 3)]
    assert report_passed(report)


def test_an_adapter_failure_is_never_retried(tmp_path):
    r = rig(tmp_path)
    run_target = r.adapter._run_target

    def broken(case, flags, *args):
        if case.context.case.kind is ScenarioKind.LONG_TARGET:
            raise RuntimeError("boom")
        return run_target(case, flags, *args)
    r.adapter._run_target = broken
    report, _ = _run(r)
    assert [c.attempt for c in report.cases if c.kind is ScenarioKind.LONG_TARGET] == [1]
    assert report.status is not SuiteStatus.PASSED


def test_no_further_attempts_once_another_scenario_failed(tmp_path):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_TARGET}, 1)
    run_minimum = r.adapter._run_minimum

    def broken(case, flags):
        raise RuntimeError("boom")
    r.adapter._run_minimum = broken
    report, _ = _run(r)
    assert len(report.cases) == 12 and report.status is not SuiteStatus.PASSED
    assert run_minimum is not None


def test_non_trigger_scenarios_are_not_retryable():
    assert RETRYABLE_SCENARIOS == {ScenarioKind.LONG_TARGET, ScenarioKind.SHORT_TARGET, ScenarioKind.LONG_STOP,
                                   ScenarioKind.SHORT_STOP, ScenarioKind.PARTIAL_CLOSE, ScenarioKind.BOUNDED_DCA}
    assert MAX_ATTEMPTS == 3


# ----------------------------------------------------------------------------- the shared pass rule, adversarially
def _passing_cases(tmp_path):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_TARGET}, 1)
    report, _ = _run(r)
    assert report.status is SuiteStatus.PASSED
    return report.cases


def _swap(cases, index, **changes):
    items = list(cases)
    items[index] = dataclasses.replace(items[index], **changes)
    return tuple(items)


def test_rule_accepts_the_real_shape(tmp_path):
    assert attempts_complete(_passing_cases(tmp_path))


@pytest.mark.parametrize("mutate", [
    lambda c: c[:12],                                                       # chain ends in a miss
    lambda c: c[12:] + c[:12],                                              # retry before the first pass
    lambda c: _swap(c, 12, attempt=3),                                      # skipped attempt number
    lambda c: _swap(c, 12, attempt_of="case_" + "0" * 32),                  # retry of an unknown case
    lambda c: _swap(c, 0, final_truth=FinalTruth.PROTECTED_RECONCILED),     # miss left a position
    lambda c: _swap(c, 0, failure=FailureCode.ADAPTER),                     # miss was not a clean assertion
    lambda c: _swap(c, 12, case_id=c[0].case_id),                           # reused case id
    lambda c: c + (dataclasses.replace(c[12], case_id="case_" + "1" * 32, attempt=3),),   # attempt after a pass
], ids=["ends_in_miss", "order", "numbering", "unknown_parent", "not_flat", "not_assertion", "dup_id",
        "after_pass"])
def test_rule_rejects_tampered_attempt_shapes(tmp_path, mutate):
    cases = _passing_cases(tmp_path)
    try:
        tampered = mutate(cases)
    except HarnessError:
        return
    assert not attempts_complete(tampered)


def test_case_report_refuses_retry_of_a_non_trigger_scenario_or_bad_attempt(tmp_path):
    cases = _passing_cases(tmp_path)
    base = cases[6]                         # cancel_replace_race
    assert base.kind not in RETRYABLE_SCENARIOS
    for changes in (dict(attempt=2, attempt_of=cases[0].case_id), dict(attempt=4, attempt_of=cases[0].case_id),
                    dict(attempt=0), dict(attempt=1, attempt_of=cases[0].case_id),
                    dict(attempt=2, attempt_of=None)):
        with pytest.raises(HarnessError):
            dataclasses.replace(base, **changes)


def test_gate_rejects_a_retry_edited_into_a_pass(tmp_path):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_TARGET}, MAX_ATTEMPTS)
    report, data = _run(r)
    raw = json.loads(data)
    raw["cases"][-1]["status"], raw["cases"][-1]["failure"] = "passed", None
    tampered = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(Exception):
        gate_proof(tampered, expected_head=BUILD_SHA, host_id="tnet_test")
    assert canonical_report_bytes(report) == data
    assert len(REQUIRED_SCENARIOS) == 12
    assert isinstance(report.cases[0], CaseReport)


# ----------------------------------------------------------------------------- what counts as a clean miss
def _captured_miss(tmp_path):
    from newcore import testnet_harness as h
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_TARGET}, MAX_ATTEMPTS)
    seen = []
    real = h._retryable_miss

    def spy(context, evidence, cleanup, failure, cleanup_failure=None, cleanup_repeat=None):
        seen.append((context, evidence, cleanup, failure, cleanup_failure, cleanup_repeat))
        return real(context, evidence, cleanup, failure, cleanup_failure, cleanup_repeat)
    h._retryable_miss = spy
    try:
        _run(r)
    finally:
        h._retryable_miss = real
    context, evidence, cleanup, failure, cleanup_failure, repeat = seen[0]
    assert cleanup_failure is None and repeat == cleanup

    def miss(c, e, cl, f, cf=None, rep=None):
        return real(c, e, cl, f, cf, cl if rep is None else rep)
    assert context.case.kind is ScenarioKind.LONG_TARGET and miss(context, evidence, cleanup, failure)
    return miss, context, evidence, cleanup, failure


def test_only_a_clean_protected_flat_assertion_miss_is_retryable(tmp_path):
    miss, context, evidence, cleanup, failure = _captured_miss(tmp_path)
    for other in (FailureCode.ADAPTER, FailureCode.BOUNDS, FailureCode.DEADLINE, FailureCode.EVIDENCE_HOLD, None):
        assert not miss(context, evidence, cleanup, other)
    for field in ("entry_confirmed", "protection_confirmed", "nc06_authorized", "reconciled"):
        assert not miss(context, dataclasses.replace(evidence, **{field: False}), cleanup, failure), field
    assert not miss(context, dataclasses.replace(evidence, final_truth=FinalTruth.HOLD, reconciled=False), cleanup,
                    failure)
    protected = dataclasses.replace(cleanup, final_truth=FinalTruth.PROTECTED_RECONCILED,
                                    protected_at_ms=cleanup.flattened_at_ms, flattened_at_ms=None)
    assert not miss(context, evidence, protected, failure)
    assert not miss(context, evidence, None, failure) and not miss(context, None, cleanup, failure)
    assert not miss(context, dataclasses.replace(evidence, authorization_id="dec_" + "0" * 32), cleanup, failure)


def test_rule_rejects_reordered_first_pass_and_cross_kind_duplicate_ids(tmp_path):
    cases = _passing_cases(tmp_path)
    swapped = (cases[1], cases[0]) + cases[2:]
    assert not attempts_complete(swapped)
    assert not attempts_complete(_swap(cases, 12, case_id=cases[1].case_id))
    assert not attempts_complete(_swap(cases, 3, attempt=2, attempt_of=cases[0].case_id))


def test_gate_rechecks_every_attempt_terminal_even_with_a_passed_status(tmp_path):
    r = rig(tmp_path)
    _quiet_for(r, {ScenarioKind.LONG_TARGET}, 1)
    report, _ = _run(r)
    assert report_passed(report)
    forged = dataclasses.replace(report, cases=_swap(report.cases, 5, final_truth=FinalTruth.HOLD))
    assert not report_passed(forged)
