"""Persistent account loss-state integration at the TNET-02 risk boundary."""
from __future__ import annotations

from decimal import Decimal

import pytest

from newcore import risk as R
from newcore.testnet_harness import (CLEANUP_FLATTEN_MS, CLEANUP_HOLD_MS, CLEANUP_PROTECT_MS, CleanupContext,
                                     SuiteStatus, _case_context, _derive, run_suite)
from newcore.tnet02.adapter import AdapterError

from .support import T0, manifest, rig


def _bound(tmp_path):
    wired = rig(tmp_path)
    spec = manifest()
    wired.adapter.confirm_binding(spec.binding)
    suite_id = _derive("suite", "loss-state")
    context = _case_context(spec, suite_id, spec.cases[0], T0)
    wired.adapter.setup(context)
    return wired, spec, suite_id, context


def _decision(wired):
    case = wired.adapter._case
    assert case is not None
    return wired.adapter._authorize(case, R.RiskAction.ENTRY, Decimal("60000"), Decimal("59400"), Decimal("300"))


def _cleanup_context(context, now):
    token = _derive("cleanup", context.suite_id, context.case.case_id)
    return CleanupContext(context.suite_id, context.case.case_id, token, now, now + CLEANUP_PROTECT_MS,
                          now + CLEANUP_FLATTEN_MS, now + CLEANUP_HOLD_MS)


def test_twenty_percent_equity_fall_is_refused(tmp_path):
    wired, _spec, _suite_id, _context = _bound(tmp_path)
    wired.fake.equity = Decimal("4000")
    wired.fake.unrealized_pnl = Decimal("-1000")
    decision = _decision(wired)
    assert isinstance(decision, R.RefusedDecision)
    assert R.RefusalCode.DRAWDOWN_KILL in decision.reasons


def test_daily_loss_latch_survives_equity_recovery(tmp_path):
    wired, _spec, _suite_id, _context = _bound(tmp_path)
    wired.fake.equity = Decimal("4800")
    first = _decision(wired)
    assert isinstance(first, R.RefusedDecision)
    assert R.RefusalCode.DAILY_LOSS_HALT in first.reasons

    wired.clock.now += 1
    wired.fake.equity = Decimal("5200")
    recovered = _decision(wired)
    assert isinstance(recovered, R.RefusedDecision)
    assert R.RefusalCode.DAILY_LOSS_HALT in recovered.reasons


def test_drawdown_latch_survives_new_equity_high(tmp_path):
    wired, _spec, _suite_id, _context = _bound(tmp_path)
    wired.fake.equity = Decimal("6000")
    assert isinstance(_decision(wired), R.ApprovedDecision)

    wired.clock.now += 1
    wired.fake.equity = Decimal("5400")
    fallen = _decision(wired)
    assert isinstance(fallen, R.RefusedDecision)
    assert R.RefusalCode.DRAWDOWN_KILL in fallen.reasons

    wired.clock.now += 1
    wired.fake.equity = Decimal("6500")
    recovered = _decision(wired)
    assert isinstance(recovered, R.RefusedDecision)
    assert R.RefusalCode.DRAWDOWN_KILL in recovered.reasons


def test_entry_cluster_persists_across_authorizations_and_cases(tmp_path):
    wired, spec, suite_id, first_context = _bound(tmp_path)
    first_case = wired.adapter._case
    assert first_case is not None
    wired.adapter._loss_record_entry(first_case)
    cleanup = wired.adapter.cleanup(_cleanup_context(first_context, wired.clock.now))
    assert cleanup.final_truth.value == "flat"

    second_context = _case_context(spec, suite_id, spec.cases[1], wired.clock.now)
    wired.adapter.setup(second_context)
    second_case = wired.adapter._case
    assert second_case is not None
    wired.adapter._loss_record_entry(second_case)
    decision = _decision(wired)
    assert isinstance(decision, R.RefusedDecision)
    assert R.RefusalCode.CANDLE_CLUSTER_LIMIT in decision.reasons


@pytest.mark.parametrize("failure", ["missing", "backward_clock"])
def test_missing_tracker_or_invalid_clock_holds(failure, tmp_path):
    wired, _spec, _suite_id, _context = _bound(tmp_path)
    if failure == "missing":
        wired.adapter._loss_tracker = None
    else:
        wired.clock.now = T0 - 1
    with pytest.raises(AdapterError) as caught:
        _decision(wired)
    assert caught.value.code in {"loss.state", "loss.account_facts"}


def test_normal_twelve_case_matrix_still_passes(tmp_path):
    wired = rig(tmp_path)
    report = run_suite(manifest(), wired.adapter, [].append)
    assert report.status is SuiteStatus.PASSED
    assert len(report.cases) == 12
