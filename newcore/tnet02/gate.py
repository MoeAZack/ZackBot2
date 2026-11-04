"""TNET-02 strict decoder for ``zackbot-tnet01/1`` reports and REAL_TNET_EXACT_HEAD gate conversion.

Pure: no network, filesystem, clock or process access.  The only accepted input is the exact canonical byte string
the TNET-01 kernel hands its writer.  Anything else is refused with a stable code that never echoes input text.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from newcore.base_clean import GateProof, ProofKind
from newcore.testnet_harness import (MAX_AGGREGATE_NOTIONAL, MAX_LEVERAGE, MAX_ORDERS_PER_SCENARIO,
                                     MAX_SCENARIO_MS, MAX_SCENARIO_NOTIONAL, MAX_SUITE_MS, REPORT_SCHEMA, REQUIRED_SCENARIOS, CaseReport, CaseStatus, FailureCode,
                                     FinalTruth, HarnessError, ScenarioKind, SuiteReport, SuiteStatus,
                                     canonical_report_bytes)


MAX_REPORT_BYTES = 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?")
_REPORT_KEYS = frozenset({"schema", "report_sha256", "suite_id", "build_sha", "account_id", "binding_digest",
                          "started_ms", "finished_ms", "status", "failure", "cases", "redaction_clean"})
_CASE_KEYS = frozenset({"case_id", "kind", "status", "failure", "started_ms", "finished_ms", "final_truth",
                        "orders_total", "peak_notional", "aggregate_peak_notional", "max_leverage",
                        "evidence_sha256"})
_SAFE_TERMINALS = (FinalTruth.FLAT, FinalTruth.PROTECTED_RECONCILED)


class GateError(ValueError):
    """Stable refusal; ``code`` is a closed identifier and the message never contains report text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise GateError(code) from None


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            _fail("report.duplicate_key")
        out[key] = value
    return out


def _constant(_value: str) -> None:
    _fail("report.constant")


def _float(_value: str) -> None:
    _fail("report.float")


def _keys(value: Any, expected: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != expected:
        _fail(code)
    return value


def _enum(enum_type: type, value: Any, code: str) -> Any:
    if type(value) is not str:
        _fail(code)
    try:
        return enum_type(value)
    except ValueError:
        _fail(code)


def _optional_failure(value: Any, code: str) -> FailureCode | None:
    return None if value is None else _enum(FailureCode, value, code)


def _decimal(value: Any, code: str) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        _fail(code)
    try:
        return Decimal(value)
    except InvalidOperation:
        _fail(code)


def _case(raw: Any) -> CaseReport:
    row = _keys(raw, _CASE_KEYS, "report.case_keys")
    try:
        return CaseReport(
            row["case_id"], _enum(ScenarioKind, row["kind"], "report.case_kind"),
            _enum(CaseStatus, row["status"], "report.case_status"),
            _optional_failure(row["failure"], "report.case_failure"), row["started_ms"], row["finished_ms"],
            _enum(FinalTruth, row["final_truth"], "report.case_final_truth"), row["orders_total"],
            _decimal(row["peak_notional"], "report.case_peak_notional"),
            _decimal(row["aggregate_peak_notional"], "report.case_aggregate_peak_notional"), row["max_leverage"],
            row["evidence_sha256"])
    except HarnessError:
        _fail("report.case_invalid")


def _digest(raw: dict[str, Any]) -> str:
    # Mirrors the kernel recipe in newcore.testnet_harness._report: sha256 over the canonical base tuple.
    base = [raw["schema"], raw["suite_id"], raw["build_sha"], raw["account_id"], raw["binding_digest"],
            raw["started_ms"], raw["finished_ms"], raw["status"], raw["failure"], raw["cases"], True]
    data = json.dumps(base, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def decode_report(data: bytes) -> SuiteReport:
    """Rebuild a sealed ``SuiteReport`` from exact canonical bytes, or raise ``GateError``."""
    if type(data) is not bytes:
        _fail("report.bytes")
    if not data or len(data) > MAX_REPORT_BYTES:
        _fail("report.size")
    raw = None
    try:
        raw = json.loads(data.decode("ascii"), object_pairs_hook=_pairs, parse_constant=_constant,
                         parse_float=_float)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        pass
    if raw is None:
        _fail("report.syntax")
    row = _keys(raw, _REPORT_KEYS, "report.keys")
    if row["schema"] != REPORT_SCHEMA:
        _fail("report.schema")
    if type(row["cases"]) is not list:
        _fail("report.cases")
    cases = tuple(_case(item) for item in row["cases"])
    try:
        report = SuiteReport(
            row["schema"], row["report_sha256"], row["suite_id"], row["build_sha"], row["account_id"],
            row["binding_digest"], row["started_ms"], row["finished_ms"],
            _enum(SuiteStatus, row["status"], "report.status"), _optional_failure(row["failure"], "report.failure"),
            cases, row["redaction_clean"])
    except HarnessError:
        _fail("report.invalid")
    try:
        canonical = canonical_report_bytes(report)
    except HarnessError:
        _fail("report.invalid")
    if canonical != data:
        _fail("report.non_canonical")
    if _digest(row) != report.report_sha256:
        _fail("report.digest")
    return report


def report_passed(report: SuiteReport) -> bool:
    """True only for a complete, ordered, all-passed, safely terminated twelve-case suite."""
    if type(report) is not SuiteReport:
        _fail("report.type")
    if report.status is not SuiteStatus.PASSED or report.failure is not None:
        return False
    if tuple(case.kind for case in report.cases) != REQUIRED_SCENARIOS:
        return False
    if len({case.case_id for case in report.cases}) != len(REQUIRED_SCENARIOS):
        return False
    if not report.redaction_clean:
        return False
    if not report.started_ms <= report.finished_ms <= report.started_ms + MAX_SUITE_MS:
        return False
    cursor = report.started_ms
    for case in report.cases:
        # the digest is unkeyed, so re-check every frozen bound and the time order instead of trusting the status
        if not (case.status is CaseStatus.PASSED and case.failure is None and case.final_truth in _SAFE_TERMINALS):
            return False
        if not (0 <= case.orders_total <= MAX_ORDERS_PER_SCENARIO and 1 <= case.max_leverage <= MAX_LEVERAGE):
            return False
        if not (Decimal(0) <= case.peak_notional <= MAX_SCENARIO_NOTIONAL
                and case.peak_notional <= case.aggregate_peak_notional <= MAX_AGGREGATE_NOTIONAL):
            return False
        if not (cursor <= case.started_ms <= case.finished_ms <= case.started_ms + MAX_SCENARIO_MS):
            return False
        cursor = case.finished_ms
    return cursor <= report.finished_ms


def gate_proof(data: bytes, *, expected_head: str, host_id: str, expected_account_id: str | None = None,
               expected_binding_digest: str | None = None) -> GateProof:
    """Convert exact report bytes into ``GateProof(REAL_TNET_EXACT_HEAD, ...)``.

    A report for a different build is refused outright; a failed or held run becomes ``passed=False`` evidence.
    """
    if type(expected_head) is not str or _SHA.fullmatch(expected_head) is None:
        _fail("gate.expected_head")
    report = decode_report(data)
    if report.build_sha != expected_head:
        _fail("gate.head_mismatch")
    if expected_account_id is not None and report.account_id != expected_account_id:
        _fail("gate.account_mismatch")
    if expected_binding_digest is not None and report.binding_digest != expected_binding_digest:
        _fail("gate.binding_mismatch")
    try:
        return GateProof(ProofKind.REAL_TNET_EXACT_HEAD, report.build_sha, host_id, report_passed(report),
                         hashlib.sha256(data).hexdigest())
    except ValueError:
        _fail("gate.proof_invalid")


__all__ = ["GateError", "MAX_REPORT_BYTES", "decode_report", "gate_proof", "report_passed"]
