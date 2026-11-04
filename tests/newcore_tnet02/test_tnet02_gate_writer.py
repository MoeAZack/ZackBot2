"""TNET-02 gate conversion and exact-build writer: tampering, canonical form, identity and filesystem refusals."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from newcore.base_clean import ProofKind
from newcore.testnet_harness import REPORT_SCHEMA, run_suite
from newcore.tnet02.gate import GateError, decode_report, gate_proof, report_passed
from newcore.tnet02.writer import ExactBuildReportWriter, WriterError, report_filename

from .support import BUILD_SHA, manifest, rig


@pytest.fixture(scope="module")
def passed_bytes(tmp_path_factory) -> bytes:
    out: list[bytes] = []
    run_suite(manifest(), rig(tmp_path_factory.mktemp("rig")).adapter, out.append)
    return out[0]


def _mutate(data: bytes, fn) -> bytes:
    raw = json.loads(data)
    fn(raw)
    return json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("ascii")


def _resign(raw: dict) -> None:
    base = [raw["schema"], raw["suite_id"], raw["build_sha"], raw["account_id"], raw["binding_digest"],
            raw["started_ms"], raw["finished_ms"], raw["status"], raw["failure"], raw["cases"], True]
    raw["report_sha256"] = hashlib.sha256(json.dumps(base, sort_keys=True, separators=(",", ":"))
                                          .encode("ascii")).hexdigest()


def test_round_trip_and_proof(passed_bytes):
    report = decode_report(passed_bytes)
    assert report_passed(report)
    proof = gate_proof(passed_bytes, expected_head=BUILD_SHA, host_id="tnet_owner.pc")
    assert proof.kind is ProofKind.REAL_TNET_EXACT_HEAD and proof.head_sha == BUILD_SHA and proof.passed
    assert proof.evidence_sha256 == hashlib.sha256(passed_bytes).hexdigest()


@pytest.mark.parametrize("mutation, code", [
    (lambda b: b + b" ", "report.non_canonical"),
    (lambda b: b[:-1], "report.syntax"),
    (lambda b: b.replace(b'{"', b'{ "', 1), "report.non_canonical"),
    (lambda b: b.replace(b'"schema"', b'"schema":"x","schema"', 1), "report.duplicate_key"),
    (lambda b: b.replace(b'"redaction_clean":true', b'"redaction_clean":NaN', 1), "report.constant"),
    (lambda b: b.replace(b'"started_ms":', b'"started_ms":1.5,"x":', 1), "report.float"),
    (lambda b: "é".encode("utf-8") + b, "report.syntax"),
    (lambda b: b"", "report.size"),
])
def test_byte_level_tampering_refused(passed_bytes, mutation, code):
    with pytest.raises(GateError) as caught:
        decode_report(mutation(passed_bytes))
    assert caught.value.code == code


def test_flipped_byte_breaks_digest(passed_bytes):
    tampered = _mutate(passed_bytes, lambda raw: raw["cases"][0].__setitem__("orders_total", 1))
    with pytest.raises(GateError) as caught:
        decode_report(tampered)
    assert caught.value.code == "report.digest"


def test_wrong_schema_unknown_key_and_missing_key_refused(passed_bytes):
    for fn, code in ((lambda raw: raw.__setitem__("schema", "zackbot-tnet01/2"), "report.schema"),
                     (lambda raw: raw.__setitem__("extra", 1), "report.keys"),
                     (lambda raw: raw.pop("failure"), "report.keys"),
                     (lambda raw: raw["cases"][0].__setitem__("note", "x"), "report.case_keys")):
        with pytest.raises(GateError) as caught:
            decode_report(_mutate(passed_bytes, fn))
        assert caught.value.code == code


def test_resigned_passed_status_with_failed_case_is_not_a_pass(passed_bytes):
    def fn(raw):
        raw["cases"][3]["status"] = "failed"
        raw["cases"][3]["failure"] = "assertion"
        _resign(raw)
    report = decode_report(_mutate(passed_bytes, fn))
    assert not report_passed(report)
    assert gate_proof(_mutate(passed_bytes, fn), expected_head=BUILD_SHA, host_id="tnet_x").passed is False


def test_resigned_missing_or_reordered_case_is_not_a_pass(passed_bytes):
    def drop(raw):
        raw["cases"].pop()
        _resign(raw)

    def swap(raw):
        raw["cases"][0], raw["cases"][1] = raw["cases"][1], raw["cases"][0]
        _resign(raw)
    for fn in (drop, swap):
        assert gate_proof(_mutate(passed_bytes, fn), expected_head=BUILD_SHA, host_id="tnet_x").passed is False


def test_head_mismatch_is_an_error_not_a_failed_proof(passed_bytes):
    with pytest.raises(GateError) as caught:
        gate_proof(passed_bytes, expected_head="c" * 40, host_id="tnet_x")
    assert caught.value.code == "gate.head_mismatch"
    for bad in ("B" * 40, "b" * 39, None):
        with pytest.raises(GateError):
            gate_proof(passed_bytes, expected_head=bad, host_id="tnet_x")


def test_invalid_host_id_refused(passed_bytes):
    with pytest.raises(GateError) as caught:
        gate_proof(passed_bytes, expected_head=BUILD_SHA, host_id="Owner PC")
    assert caught.value.code == "gate.proof_invalid"


def test_schema_constant_is_frozen():
    assert REPORT_SCHEMA == "zackbot-tnet01/1"


# ------------------------------------------------------------------------------------------------- writer
def test_writer_writes_once_exactly(tmp_path, passed_bytes):
    writer = ExactBuildReportWriter(tmp_path, BUILD_SHA)
    writer(passed_bytes)
    report = decode_report(passed_bytes)
    assert writer.path == tmp_path / report_filename(BUILD_SHA, report.report_sha256)
    assert writer.path.read_bytes() == passed_bytes
    with pytest.raises(WriterError) as caught:
        writer(passed_bytes)
    assert caught.value.code == "writer.already_written"
    with pytest.raises(WriterError) as caught:
        ExactBuildReportWriter(tmp_path, BUILD_SHA)(passed_bytes)
    assert caught.value.code == "writer.exists"
    assert writer.path.read_bytes() == passed_bytes


def test_writer_refuses_other_build_and_non_canonical_bytes(tmp_path, passed_bytes):
    with pytest.raises(WriterError) as caught:
        ExactBuildReportWriter(tmp_path, "c" * 40)(passed_bytes)
    assert caught.value.code == "writer.build_mismatch"
    with pytest.raises(WriterError) as caught:
        ExactBuildReportWriter(tmp_path, BUILD_SHA)(passed_bytes + b"\n")
    assert caught.value.code == "writer.report_invalid"
    assert list(tmp_path.iterdir()) == []


def test_writer_refuses_missing_file_or_symlinked_dir(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    (tmp_path / "file").write_bytes(b"x")
    with pytest.raises(WriterError):
        ExactBuildReportWriter(tmp_path / "missing", BUILD_SHA)
    with pytest.raises(WriterError):
        ExactBuildReportWriter(tmp_path / "file", BUILD_SHA)
    if hasattr(os, "symlink"):
        try:
            os.symlink(target, tmp_path / "link", target_is_directory=True)
        except OSError:
            pytest.skip("symlink not permitted on this host")
        with pytest.raises(WriterError):
            ExactBuildReportWriter(tmp_path / "link", BUILD_SHA)
    with pytest.raises(WriterError):
        ExactBuildReportWriter(tmp_path, "nope")
