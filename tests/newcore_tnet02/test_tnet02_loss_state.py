"""Persistent account loss-state integration at the TNET-02 risk boundary."""
from __future__ import annotations

import shutil
from decimal import Decimal

import pytest

from newcore import risk as R
from newcore.testnet_harness import (CLEANUP_FLATTEN_MS, CLEANUP_HOLD_MS, CLEANUP_PROTECT_MS, CleanupContext,
                                     SuiteStatus, _case_context, _derive, run_suite)
from newcore.tnet02.adapter import AdapterError
from newcore.tnet02.loss_store import FileLossStore, LossStoreError
from tools import tnet02_run as cli

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


def test_full_tracker_and_wallet_survive_process_equivalent_restart(tmp_path):
    first, spec, _suite_id, _context = _bound(tmp_path / "first")
    first.fake.equity = Decimal("6000")
    assert isinstance(_decision(first), R.ApprovedDecision)
    first.clock.now += 1
    first.fake.equity = Decimal("4000")
    assert isinstance(_decision(first), R.RefusedDecision)
    case = first.adapter._case
    assert case is not None
    first.adapter._loss_record_entry(case)
    first.adapter._loss_record_entry(case)
    for _ in range(3):
        first.clock.now += 1
        first.adapter._sync_loss_state(first.adapter._facts((case.symbol,)), stopped_out=True)
    expected_tracker = first.adapter._loss_tracker
    expected_wallet = first.adapter._loss_wallet_balance
    assert expected_tracker is not None
    assert (expected_tracker.halt_latched and expected_tracker.drawdown_kill_latched
            and expected_tracker.peak_equity == Decimal("6000"))
    assert len(expected_tracker.stop_outs) == 3 and len(expected_tracker.entries) == 2

    second_root = tmp_path / "second"
    second_root.mkdir()
    second = rig(second_root, loss_store=FileLossStore(first.journal_dir))
    second.clock.now = first.clock.now
    second.fake.equity = first.fake.equity
    second.adapter.confirm_binding(spec.binding)
    assert second.adapter._loss_tracker == expected_tracker
    assert second.adapter._loss_wallet_balance == expected_wallet
    context = _case_context(spec, _derive("suite", "loss-restart"), spec.cases[0], second.clock.now)
    second.adapter.setup(context)
    decision = _decision(second)
    assert isinstance(decision, R.RefusedDecision)
    assert {R.RefusalCode.DAILY_LOSS_HALT, R.RefusalCode.DRAWDOWN_KILL,
            R.RefusalCode.STRATEGY_COOLDOWN, R.RefusalCode.CANDLE_CLUSTER_LIMIT} <= set(decision.reasons)


@pytest.mark.parametrize("fault", ["missing", "corrupt", "rollback"])
def test_known_durable_state_never_reinitializes_after_damage(fault, tmp_path):
    first, spec, _suite_id, _context = _bound(tmp_path / "first")
    state_path = first.journal_dir / ".tnet02-loss-state.json"
    anchor_path = first.journal_dir / ".tnet02-loss-anchor.json"
    old = state_path.read_bytes()
    old_anchor = anchor_path.read_bytes()
    if fault == "missing":
        state_path.unlink()
    elif fault == "corrupt":
        state_path.write_bytes(b"{}\n")
    else:
        first.fake.equity = Decimal("6000")
        _decision(first)
        state_path.write_bytes(old)
        anchor_path.write_bytes(old_anchor)

    second_root = tmp_path / "second"
    second_root.mkdir()
    second = rig(second_root, loss_store=FileLossStore(first.journal_dir))
    second.clock.now = first.clock.now
    with pytest.raises(AdapterError) as caught:
        second.adapter.confirm_binding(spec.binding)
    assert caught.value.code == "loss.state"
    assert second.adapter._loss_tracker is None


def test_deleting_entire_state_directory_does_not_restore_first_use(tmp_path):
    first, _spec, _suite_id, _context = _bound(tmp_path)
    state_root = first.journal_dir
    shutil.rmtree(state_root)
    state_root.mkdir()
    with pytest.raises(LossStoreError) as caught:
        FileLossStore(state_root).load(first.adapter._account.account_id, first.adapter._digest)
    assert caught.value.code == "loss_store.deleted"


def test_interrupted_high_water_advance_leaves_store_fail_closed(tmp_path):
    first, _spec, _suite_id, _context = _bound(tmp_path)
    durable = first.adapter._loss_store._high_water

    class Interrupted:
        def load(self, account_id, binding_digest):
            return durable.load(account_id, binding_digest)

        def advance(self, account_id, binding_digest, expected_generation, record):
            if expected_generation is not None:
                raise OSError("simulated interruption")
            return durable.advance(account_id, binding_digest, expected_generation, record)

    first.adapter._loss_store._high_water = Interrupted()
    first.fake.equity = Decimal("6000")
    with pytest.raises(AdapterError) as caught:
        _decision(first)
    assert caught.value.code == "loss.state"
    with pytest.raises(LossStoreError) as reopened:
        FileLossStore(first.journal_dir, high_water=durable).load(first.adapter._account.account_id,
                                                                 first.adapter._digest)
    assert reopened.value.code == "loss_store.rollback"


def test_loss_store_refuses_account_or_binding_mismatch(tmp_path):
    first, _spec, _suite_id, _context = _bound(tmp_path)
    store = FileLossStore(first.journal_dir)
    with pytest.raises(LossStoreError):
        store.load("acct_ffffffffffffffffffffffffffffffff", first.adapter._digest)
    with pytest.raises(LossStoreError):
        store.load(first.adapter._account.account_id, "f" * 16)


def test_cli_machine_store_survives_fresh_journal_directory(tmp_path):
    spec = manifest()
    first = rig(tmp_path / "run-a", loss_store=cli._machine_loss_store(spec.binding.account))
    first.adapter.confirm_binding(spec.binding)
    first_context = _case_context(spec, _derive("suite", "cli-loss-a"), spec.cases[0], first.clock.now)
    first.adapter.setup(first_context)
    first.fake.equity = Decimal("4000")
    refused = _decision(first)
    assert isinstance(refused, R.RefusedDecision)
    assert R.RefusalCode.DAILY_LOSS_HALT in refused.reasons

    second = rig(tmp_path / "run-b", loss_store=cli._machine_loss_store(spec.binding.account))
    assert second.journal_dir != first.journal_dir
    second.clock.now = first.clock.now
    second.fake.equity = Decimal("5000")
    second.adapter.confirm_binding(spec.binding)
    assert second.adapter._loss_tracker == first.adapter._loss_tracker
    second_context = _case_context(spec, _derive("suite", "cli-loss-b"), spec.cases[1], second.clock.now)
    second.adapter.setup(second_context)
    recovered = _decision(second)
    assert isinstance(recovered, R.RefusedDecision)
    assert R.RefusalCode.DAILY_LOSS_HALT in recovered.reasons
