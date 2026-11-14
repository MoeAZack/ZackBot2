"""TNET-02 gate conversion and exact-build writer: tampering, canonical form, identity and filesystem refusals."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

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
    for fn, code in ((lambda raw: raw.__setitem__("schema", "zackbot-tnet01/1"), "report.schema"),
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
    assert REPORT_SCHEMA == "zackbot-tnet01/2"


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
    recovered = ExactBuildReportWriter(tmp_path, BUILD_SHA)
    recovered(passed_bytes)
    assert recovered.path == writer.path
    assert writer.path.read_bytes() == passed_bytes


def test_writer_never_exposes_partial_final_and_failed_write_can_retry(tmp_path, passed_bytes, monkeypatch):
    writer = ExactBuildReportWriter(tmp_path, BUILD_SHA)
    real_write = os.write
    calls = 0

    def interrupted(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:max(1, len(data) // 2)])
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "write", interrupted)
    with pytest.raises(WriterError) as caught:
        writer(passed_bytes)
    assert caught.value.code == "writer.write_failed"
    assert list(tmp_path.iterdir()) == [] and writer.path is None

    monkeypatch.setattr(os, "write", real_write)
    writer(passed_bytes)
    assert writer.path.read_bytes() == passed_bytes


def test_writer_publish_failure_leaves_no_final_that_blocks_retry(tmp_path, passed_bytes, monkeypatch):
    writer = ExactBuildReportWriter(tmp_path, BUILD_SHA)
    real_link = os.link
    monkeypatch.setattr(os, "link", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("crash boundary")))
    with pytest.raises(WriterError) as caught:
        writer(passed_bytes)
    assert caught.value.code == "writer.publish_failed"
    assert list(tmp_path.iterdir()) == [] and writer.path is None

    (tmp_path / ".stale-crash.tmp").write_bytes(b"partial temporary bytes")
    monkeypatch.setattr(os, "link", real_link)
    writer(passed_bytes)
    assert writer.path.read_bytes() == passed_bytes


def test_writer_losing_final_name_race_preserves_competitor_bytes(tmp_path, passed_bytes, monkeypatch):
    report = decode_report(passed_bytes)
    target = tmp_path / report_filename(BUILD_SHA, report.report_sha256)
    real_link = os.link

    def competitor_wins(*args, **kwargs):
        target.write_bytes(b"competitor")
        return real_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", competitor_wins)
    with pytest.raises(WriterError) as caught:
        ExactBuildReportWriter(tmp_path, BUILD_SHA)(passed_bytes)
    assert caught.value.code == "writer.exists"
    assert target.read_bytes() == b"competitor"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("swap_point", ["before_link", "after_link"])
def test_writer_fails_closed_when_windows_path_directory_swaps_during_publication(
        tmp_path, passed_bytes, monkeypatch, swap_point):
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    pinned = tmp_path / "reports.pinned"
    replacement = tmp_path / "reports.replacement"
    writer = ExactBuildReportWriter(out_dir, BUILD_SHA)
    report = decode_report(passed_bytes)
    final_name = report_filename(BUILD_SHA, report.report_sha256)
    real_link = os.link
    monkeypatch.setattr(os, "supports_dir_fd", set())

    def swap_dir():
        os.rename(out_dir, pinned)
        out_dir.mkdir()

    def hostile_link(src, dst, **kwargs):
        if swap_point == "before_link":
            swap_dir()
            (out_dir / Path(src).name).write_bytes(b"ATTACKER")
            return real_link(src, dst, **kwargs)
        result = real_link(src, dst, **kwargs)
        swap_dir()
        (out_dir / final_name).write_bytes(b"ATTACKER")
        return result

    monkeypatch.setattr(os, "link", hostile_link)
    with pytest.raises(WriterError) as caught:
        writer(passed_bytes)
    # Windows normally refuses the rename while the temporary file handle remains open; platforms that permit the
    # rename reach the post-publication identity proof.  Both are fail-closed outcomes.
    assert caught.value.code in ("writer.publish_failed", "writer.publish_verify") and writer.path is None
    if (out_dir / final_name).exists():
        assert (out_dir / final_name).read_bytes() == passed_bytes
    if pinned.exists() and (pinned / final_name).exists():
        assert (pinned / final_name).read_bytes() == passed_bytes

    if pinned.exists():
        os.rename(out_dir, replacement)
        os.rename(pinned, out_dir)
    monkeypatch.setattr(os, "link", real_link)
    if not (out_dir / final_name).exists():
        writer(passed_bytes)
        assert writer.path.read_bytes() == passed_bytes
    else:
        assert (out_dir / final_name).read_bytes() == passed_bytes


@pytest.mark.parametrize("stage", ["post_temp_unlink", "post_final_verify", "pre_return"])
def test_writer_retains_namespace_guard_at_every_success_boundary(tmp_path, passed_bytes, monkeypatch, stage):
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    pinned = tmp_path / "reports.pinned"
    writer = ExactBuildReportWriter(out_dir, BUILD_SHA)
    final_name = report_filename(BUILD_SHA, decode_report(passed_bytes).report_sha256)
    monkeypatch.setattr(os, "supports_dir_fd", set())
    attempted = []

    def hostile_hook(observed):
        if observed != stage:
            return
        attempted.append(observed)
        try:
            os.rename(out_dir, pinned)
        except OSError:
            return  # Windows: the retained final handle is the namespace guard.
        out_dir.mkdir()
        (out_dir / final_name).write_bytes(b"ATTACKER")

    monkeypatch.setattr(ExactBuildReportWriter, "_publication_hook", staticmethod(hostile_hook))
    try:
        writer(passed_bytes)
    except WriterError as error:
        assert error.code == "writer.publish_verify" and writer.path is None
    else:
        assert writer.path.read_bytes() == passed_bytes
    assert attempted == [stage]
    if (out_dir / final_name).exists() and (out_dir / final_name).read_bytes() == b"ATTACKER":
        # Cleanup must not remove an unrelated same-name file in the replacement directory.
        assert (out_dir / final_name).read_bytes() == b"ATTACKER"
    if (pinned / final_name).exists():
        assert (pinned / final_name).read_bytes() == passed_bytes


def test_post_link_failure_never_deletes_a_substituted_final_and_canonical_retry_recovers(
        tmp_path, passed_bytes, monkeypatch):
    final_name = report_filename(BUILD_SHA, decode_report(passed_bytes).report_sha256)
    final = tmp_path / final_name
    real_unlink = os.unlink
    final_unlinks = []

    def observe_unlink(path, *args, **kwargs):
        if Path(path).name == final_name:
            final_unlinks.append(path)
        return real_unlink(path, *args, **kwargs)

    def fail_after_verify(stage):
        if stage == "post_final_verify":
            raise WriterError("injected")

    monkeypatch.setattr(os, "unlink", observe_unlink)
    monkeypatch.setattr(ExactBuildReportWriter, "_publication_hook", staticmethod(fail_after_verify))
    with pytest.raises(WriterError) as caught:
        ExactBuildReportWriter(tmp_path, BUILD_SHA)(passed_bytes)
    assert caught.value.code == "writer.publish_verify"
    assert final.read_bytes() == passed_bytes
    assert final_unlinks == []

    monkeypatch.setattr(ExactBuildReportWriter, "_publication_hook", staticmethod(lambda stage: None))
    recovered = ExactBuildReportWriter(tmp_path, BUILD_SHA)
    recovered(passed_bytes)
    assert recovered.path == final and final.read_bytes() == passed_bytes

    # A same-name substitution after all prior checks is never removed or overwritten on another retry.
    del recovered
    monkeypatch.setattr(os, "unlink", real_unlink)
    final.unlink()
    final.write_bytes(b"COMPETITOR")
    with pytest.raises(WriterError) as caught:
        ExactBuildReportWriter(tmp_path, BUILD_SHA)(passed_bytes)
    assert caught.value.code == "writer.exists"
    assert final.read_bytes() == b"COMPETITOR"


def test_writer_losing_final_name_race_never_overwrites_or_follows_symlink(tmp_path, passed_bytes, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("no symlink support")
    report = decode_report(passed_bytes)
    target = tmp_path / report_filename(BUILD_SHA, report.report_sha256)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"untouched")
    real_link = os.link

    def plant_symlink(*args, **kwargs):
        try:
            os.symlink(sentinel, target)
        except OSError:
            pytest.skip("symlink not permitted on this host")
        return real_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", plant_symlink)
    with pytest.raises(WriterError) as caught:
        ExactBuildReportWriter(tmp_path, BUILD_SHA)(passed_bytes)
    assert caught.value.code == "writer.exists"
    assert sentinel.read_bytes() == b"untouched" and target.is_symlink()
    assert not list(tmp_path.glob("*.tmp"))


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
