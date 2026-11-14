"""Regressions for the adversarial review of the TNET-02 slice (one test per confirmed defect)."""
from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal

import pytest

from newcore.testnet_harness import (CaseStatus, FailureCode, FinalTruth, HarnessError, ScenarioKind, SuiteStatus,
                                     canonical_report_bytes, run_suite)
from newcore.tnet02.adapter import AdapterError
from newcore.tnet02.faults import FaultKind
from newcore.tnet02.gate import GateError, gate_proof
from newcore.tnet02.journal import JournalError
from newcore.tnet02.writer import ExactBuildReportWriter

from .fake_binance import Order
from .support import ACCOUNT_ID, BUILD_SHA, DIGEST, manifest, rig


def _run(r):
    out: list[bytes] = []
    report = run_suite(manifest(), r.adapter, out.append)
    assert out[0] == canonical_report_bytes(report)
    return report, out[0]


def _writes(r):
    return [x for x in r.fake.requests if x[0] in ("POST", "DELETE")]


def _live_algos(r):
    return [a for a in r.fake.algos.values() if a.status in ("NEW", "WORKING")]


# 1 -------------------------------------------------------------------------------------- unsettled close in cleanup
def test_cleanup_never_sends_a_second_close_over_an_unsettled_one(tmp_path):
    r = rig(tmp_path)
    fake = r.fake
    original = fake._post_fapi_v1_order
    rested: dict[str, str] = {}

    def post(params):
        closing = params["positionSide"] == "LONG" and params["side"] == "SELL"
        if closing and not rested:
            cid = params["newClientOrderId"]
            order = Order(fake._id(), cid, params["symbol"], params["side"], params["positionSide"],
                          Decimal(params["quantity"]), True)
            fake.orders[cid] = order
            rested["cid"] = cid
            return fake._order_body(order)
        return original(params)
    fake._post_fapi_v1_order = post
    real_cleanup = r.adapter.cleanup
    sent_in_cleanup: list = []

    def cleanup(context):
        before = len(fake.requests)
        evidence = real_cleanup(context)
        sent_in_cleanup.extend(x for x in fake.requests[before:] if x[0] in ("POST", "DELETE"))
        return evidence
    r.adapter.cleanup = cleanup
    report, _ = _run(r)
    assert report.cases[0].final_truth is FinalTruth.HOLD
    assert sent_in_cleanup == []
    assert fake.orders[rested["cid"]].status == "NEW"
    assert len(_live_algos(r)) == 1  # the position stays protected while held


# 2 -------------------------------------------------------------------------------------- truncated journal on restart
def test_truncated_journal_prefix_is_refused_by_the_tail_anchor(tmp_path):
    r = rig(tmp_path)
    opener = r.adapter._journals
    journal = opener.open("case_" + "a" * 32, "boot_" + "a" * 32, reopen=False)
    journal.close()
    opener._anchors["case_" + "a" * 32] = ("0" * 64, 3)  # what a longer, now-truncated chain would have left
    with pytest.raises(JournalError) as caught:
        opener.open("case_" + "a" * 32, "boot_" + "b" * 32, reopen=True)
    assert caught.value.code == "journal.anchor"


def test_restart_on_truncated_journal_holds_and_leaves_the_stop_protecting(tmp_path):
    r = rig(tmp_path)
    opener = r.adapter._journals
    real_open = opener.open

    def hostile(case_id, boot_id, *, reopen):
        if reopen:
            path = r.journal_dir / f"{case_id}.journal"
            lines = path.read_bytes().splitlines(keepends=True)
            keep = []
            for line in lines:
                keep.append(line)
                body = json.loads(line)["body"]
                if body["kind"] == "order" and body["purpose"] == "entry" and body["state"] == "filled":
                    break
            assert len(keep) < len(lines)
            path.write_bytes(b"".join(keep))
        return real_open(case_id, boot_id, reopen=reopen)
    opener.open = hostile
    report, data = _run(r)
    restart = next(c for c in report.cases if c.kind is ScenarioKind.RESTART_OPEN_POSITION)
    assert restart.final_truth is FinalTruth.HOLD and report.status is SuiteStatus.HOLD
    assert len(_live_algos(r)) == 1
    assert _writes(r)[-1][1] == "/fapi/v1/algoOrder"  # nothing sent after the restart's protective stop
    assert gate_proof(data, expected_head=BUILD_SHA, host_id="tnet_test").passed is False


def test_restart_mismatch_between_journal_and_memory_holds(tmp_path):
    r = rig(tmp_path)
    original = r.adapter._rebuild

    def drift(case, records):
        orders, position = original(case, records)
        return orders, position + Decimal("0.001")
    r.adapter._rebuild = drift
    report, _ = _run(r)
    restart = next(c for c in report.cases if c.kind is ScenarioKind.RESTART_OPEN_POSITION)
    assert restart.final_truth is FinalTruth.HOLD
    assert restart.failure is FailureCode.ADAPTER  # refused in execute, before the drifted state was adopted
    assert len(_live_algos(r)) == 1


# 3 -------------------------------------------------------------------------------------- 418 lost stop
def test_stop_lost_on_exchange_under_418_is_never_a_pass(tmp_path):
    r = rig(tmp_path)
    real_arm = r.wired.faults.arm

    def arm(kind, match, **kwargs):
        if kind is FaultKind.HTTP_418:
            for algo in r.fake.algos.values():
                if algo.status == "NEW":
                    algo.status = "CANCELED"
        return real_arm(kind, match, **kwargs)
    r.wired.faults.arm = arm
    report, data = _run(r)
    ban = next(c for c in report.cases if c.kind is ScenarioKind.IP_BAN_418)
    assert ban.status is not CaseStatus.PASSED and ban.final_truth is FinalTruth.HOLD
    assert gate_proof(data, expected_head=BUILD_SHA, host_id="tnet_test").passed is False


# 4 -------------------------------------------------------------------------------------- gate bounds and identity
@pytest.fixture(scope="module")
def passed_bytes(tmp_path_factory) -> bytes:
    out: list[bytes] = []
    run_suite(manifest(), rig(tmp_path_factory.mktemp("rig")).adapter, out.append)
    return out[0]


def _resigned(data: bytes, fn) -> bytes:
    raw = json.loads(data)
    fn(raw)
    base = [raw[k] for k in ("schema", "suite_id", "build_sha", "account_id", "binding_digest", "started_ms",
                             "finished_ms", "status", "failure", "cases")] + [True]
    raw["report_sha256"] = hashlib.sha256(json.dumps(base, sort_keys=True, separators=(",", ":"))
                                          .encode("ascii")).hexdigest()
    return json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.mark.parametrize("field, value", [("orders_total", 13), ("peak_notional", "1000.01"),
                                          ("aggregate_peak_notional", "2000.01"), ("max_leverage", 4),
                                          ("max_leverage", 0), ("finished_ms", 1)])
def test_resigned_report_outside_frozen_bounds_is_not_a_pass(passed_bytes, field, value):
    def fn(raw):
        raw["cases"][5][field] = value
    assert gate_proof(_resigned(passed_bytes, fn), expected_head=BUILD_SHA, host_id="tnet_x").passed is False


def test_resigned_report_with_cases_out_of_time_order_is_not_a_pass(passed_bytes):
    def fn(raw):
        raw["cases"][3]["started_ms"] = raw["started_ms"] - 1
    assert gate_proof(_resigned(passed_bytes, fn), expected_head=BUILD_SHA, host_id="tnet_x").passed is False


def test_gate_refuses_report_for_another_account_or_key(passed_bytes):
    assert gate_proof(passed_bytes, expected_head=BUILD_SHA, host_id="tnet_x", expected_account_id=ACCOUNT_ID,
                      expected_binding_digest=DIGEST).passed is True
    for kwargs, code in (({"expected_account_id": "acct_" + "f" * 32}, "gate.account_mismatch"),
                         ({"expected_binding_digest": "f" * 16}, "gate.binding_mismatch")):
        with pytest.raises(GateError) as caught:
            gate_proof(passed_bytes, expected_head=BUILD_SHA, host_id="tnet_x", **kwargs)
        assert caught.value.code == code


# 5 -------------------------------------------------------------------------------------- writer directory swap
def test_writer_refuses_a_directory_swapped_for_a_symlink_after_construction(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlink support")
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    writer = ExactBuildReportWriter(out_dir, BUILD_SHA)
    os.rename(out_dir, tmp_path / "reports.orig")
    try:
        os.symlink(elsewhere, out_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not permitted on this host")
    (tmp_path / "rig").mkdir()
    with pytest.raises(HarnessError):
        run_suite(manifest(), rig(tmp_path / "rig").adapter, writer)
    assert list(elsewhere.iterdir()) == [] and writer.path is None


@pytest.mark.parametrize("dir_fd", [True, False])
def test_writer_refuses_a_directory_replaced_by_another_directory(tmp_path, passed_bytes, monkeypatch, dir_fd):
    if not dir_fd:  # the Windows path: no dir_fd support, only the identity re-check guards the write
        monkeypatch.setattr(os, "supports_dir_fd", set())
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    writer = ExactBuildReportWriter(out_dir, BUILD_SHA)
    os.rename(out_dir, tmp_path / "reports.orig")
    out_dir.mkdir()
    with pytest.raises(Exception):
        writer(passed_bytes)
    assert list(out_dir.iterdir()) == []


# 6 -------------------------------------------------------------------------------------- venue clock
def test_backward_venue_observation_between_reads_holds(tmp_path):
    adapter = rig(tmp_path).adapter
    adapter._observe(5_000)
    with pytest.raises(AdapterError) as caught:
        adapter._observe(10)
    assert caught.value.code == "clock.backward"
