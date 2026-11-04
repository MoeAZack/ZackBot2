from __future__ import annotations

import ast
import json
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import pytest

from newcore.base_clean import ProofKind
from tools import base_clean_host_rebaseline as H


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
HOST = "host_" + "c" * 32
OTHER_HOST = "host_" + "d" * 32
WORKLOAD = "e" * 64
HERE = Path(__file__).parents[2]


def measurements(**changes) -> H.HostMeasurements:
    values = {
        "validate_us": Decimal("500"),
        "encode_us": Decimal("3000"),
        "decode_us": Decimal("10000"),
        "submit_ms": Decimal("5"),
        "step_ms": Decimal("25"),
        "snapshot_mean_ms": Decimal("0.1"),
        "publish_ms": Decimal("100"),
    }
    values.update(changes)
    return H.HostMeasurements(**values)


def artifact(**changes) -> H.HostArtifact:
    values = {
        "head_sha": HEAD,
        "host_id": HOST,
        "python_version": "3.12.10",
        "workload_sha256": WORKLOAD,
        "duration_ms": 3_600_000,
        "measurements": measurements(),
        "private_working_set_mib": Decimal("256"),
        "growth_mib": Decimal("16"),
    }
    values.update(changes)
    return H.build_artifact(**values)


def test_canonical_round_trip_and_gate_records_share_one_digest() -> None:
    first = artifact()
    encoded = H.encode_artifact(first)
    second = H.decode_artifact(encoded)
    bundle = H.gate_evidence(second)
    assert second == first
    assert encoded == H.encode_artifact(second)
    assert bundle.proof.kind is ProofKind.CURRENT_HOST_CODEC_BASELINE
    assert bundle.proof.passed
    assert {
        bundle.proof.evidence_sha256,
        bundle.latency.evidence_sha256,
        bundle.memory.evidence_sha256,
    } == {first.artifact_sha256}
    assert bundle.proof.head_sha == bundle.latency.head_sha == bundle.memory.head_sha == HEAD
    assert bundle.proof.host_id == bundle.latency.host_id == bundle.memory.host_id == HOST


def test_artifact_digest_is_sha256_of_the_exact_reconstructed_body() -> None:
    row = artifact()
    import hashlib
    assert row.artifact_sha256 == hashlib.sha256(H.canonical_bytes(row.body)).hexdigest()


def test_altered_digest_or_body_never_decodes() -> None:
    encoded = H.encode_artifact(artifact())
    doc = json.loads(encoded)
    doc["artifact_sha256"] = "f" * 64
    altered_digest = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(H.CollectorError, match="validation.envelope.digest"):
        H.decode_artifact(altered_digest)

    doc = json.loads(encoded)
    doc["body"]["measurements"]["validate_us"] = "501"
    altered_body = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(H.CollectorError, match="validation.envelope.digest"):
        H.decode_artifact(altered_body)


def test_duplicate_unknown_and_noncanonical_json_refuse() -> None:
    encoded = H.encode_artifact(artifact())
    duplicate = encoded.replace(b'{"artifact_sha256":', b'{"artifact_sha256":"f' + b'f' * 63 + b'","artifact_sha256":', 1)
    with pytest.raises(H.CollectorError, match="validation.json.duplicate"):
        H.decode_artifact(duplicate)
    doc = json.loads(encoded)
    doc["body"]["unexpected"] = None
    unknown = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii")
    with pytest.raises(H.CollectorError, match="validation.artifact.keys"):
        H.decode_artifact(unknown)
    with pytest.raises(H.CollectorError, match="validation.json.canonical"):
        H.decode_artifact(encoded + b"\n")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"head_sha": OTHER_HEAD[:-1]}, "validation.artifact.head"),
        ({"host_id": OTHER_HOST[:-1]}, "validation.artifact.host"),
        ({"python_version": "2.7.18"}, "validation.artifact.python"),
        ({"duration_ms": 3_599_999}, "collection_partial_duration"),
        ({"private_working_set_mib": Decimal("512.1")}, "collection_memory_ceiling"),
        ({"growth_mib": Decimal("32.1")}, "collection_memory_ceiling"),
    ],
)
def test_identity_partial_duration_and_memory_uncertainty_refuse(changes: dict, code: str) -> None:
    with pytest.raises(H.CollectorError, match=code):
        artifact(**changes)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("validate_us", "1000.1"),
        ("encode_us", "6750.1"),
        ("decode_us", "23400.1"),
        ("submit_ms", "10.1"),
        ("step_ms", "50.1"),
        ("snapshot_mean_ms", "1.1"),
        ("publish_ms", "250.1"),
    ],
)
def test_each_frozen_latency_ceiling_refuses(field: str, value: str) -> None:
    with pytest.raises(H.CollectorError, match="collection_latency_ceiling"):
        artifact(measurements=measurements(**{field: Decimal(value)}))


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"actual_head": OTHER_HEAD}, "head_mismatch"),
        ({"actual_host_id": OTHER_HOST}, "host_mismatch"),
        ({"python_version": "2.7.18"}, "python_mismatch"),
        ({"dirty": True}, "worktree_dirty"),
    ],
)
def test_run_identity_fails_closed(changes: dict, code: str) -> None:
    values = {
        "expected_head": HEAD,
        "expected_host_id": HOST,
        "actual_head": HEAD,
        "actual_host_id": HOST,
        "python_version": "3.12.10",
        "dirty": False,
    }
    values.update(changes)
    with pytest.raises(H.CollectorError, match=code):
        H._validate_identity(**values)


def test_interrupted_collection_returns_only_a_stable_refusal(monkeypatch) -> None:
    class Interrupted:
        workload_sha256 = WORKLOAD

        def __init__(self, _repo):
            self.closed = False

        def measure(self):
            raise KeyboardInterrupt

        def close(self):
            self.closed = True

    monkeypatch.setattr(H, "_git_state", lambda _repo: (HEAD, False))
    monkeypatch.setattr(H, "derive_host_id", lambda: HOST)
    monkeypatch.setattr(H.platform, "python_version", lambda: "3.12.10")
    monkeypatch.setattr(H, "_RealProbes", Interrupted)
    with pytest.raises(H.CollectorError, match="collection_interrupted") as raised:
        H.collect_current_host(expected_head=HEAD, expected_host_id=HOST, repo=HERE)
    assert raised.value.__cause__ is None and raised.value.__context__ is None


def test_unexpected_collection_failure_retains_no_raw_exception(monkeypatch) -> None:
    class Broken:
        def __init__(self, _repo):
            pass

        def measure(self):
            raise RuntimeError("SECRET raw probe failure")

        def close(self):
            pass

    monkeypatch.setattr(H, "_git_state", lambda _repo: (HEAD, False))
    monkeypatch.setattr(H, "derive_host_id", lambda: HOST)
    monkeypatch.setattr(H.platform, "python_version", lambda: "3.12.10")
    monkeypatch.setattr(H, "_RealProbes", Broken)
    with pytest.raises(H.CollectorError, match="collection_failed") as raised:
        H.collect_current_host(expected_head=HEAD, expected_host_id=HOST, repo=HERE)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert "SECRET" not in str(raised.value)


def test_soak_requires_the_frozen_duration() -> None:
    with pytest.raises(H.CollectorError, match="duration_not_frozen"):
        H._run_soak(object(), duration_ms=1)  # type: ignore[arg-type]


def test_soak_uses_monotonic_elapsed_time_and_peak_private_growth() -> None:
    class Probe:
        calls = 0

        def exercise(self):
            self.calls += 1

    clock_values = iter((0, 0, 3_600_000_000_000, 3_600_000_000_000))
    memory_values = iter((Decimal("10"), Decimal("14"), Decimal("12")))
    probe = Probe()
    elapsed, peak, growth = H._run_soak(
        probe, clock_ns=lambda: next(clock_values), sleeper=lambda _seconds: None,
        memory_reader=lambda: next(memory_values),
    )
    assert probe.calls == 1
    assert (elapsed, peak, growth) == (3_600_000, Decimal("14"), Decimal("4"))


def test_digest_valid_but_over_ceiling_body_cannot_become_gate_evidence() -> None:
    body = H.HostArtifactBody(
        H.SCHEMA, HEAD, HOST, "3.12.10", WORKLOAD, 3_600_000,
        measurements(validate_us=Decimal("1000.1")), Decimal("256"), Decimal("16"), True,
    )
    import hashlib
    forged = H.HostArtifact(body, hashlib.sha256(H.canonical_bytes(body)).hexdigest())
    with pytest.raises(H.CollectorError, match="collection_latency_ceiling"):
        H.gate_evidence(forged)


def test_exact_types_and_noncanonical_wire_values_refuse() -> None:
    encoded = H.encode_artifact(artifact())
    for field, value in (("duration_ms", True), ("private_working_set_mib", "256.0")):
        doc = json.loads(encoded)
        doc["body"][field] = value
        body = doc["body"]
        body_row = H.HostArtifactBody(
            body["schema"], body["head_sha"], body["host_id"], body["python_version"],
            body["workload_sha256"], 3_600_000, measurements(), Decimal("256"),
            Decimal("16"), True,
        )
        import hashlib
        doc["artifact_sha256"] = hashlib.sha256(H.canonical_bytes(body_row)).hexdigest()
        bad = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii")
        with pytest.raises(H.CollectorError):
            H.decode_artifact(bad)

    with pytest.raises(H.CollectorError, match="validation.measurements.validate_us"):
        measurements(validate_us=Decimal("500.0"))


def test_collector_module_has_no_network_runtime_or_credential_import() -> None:
    source = (HERE / "tools" / "base_clean_host_rebaseline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not (imported & {"requests", "urllib", "socket", "http", "websocket", "keyring"})
    assert not any(name in source.lower() for name in ("api_key", "api_secret", "os.environ"))


def test_public_records_are_frozen_and_exact() -> None:
    row = artifact()
    with pytest.raises((AttributeError, TypeError)):
        row.artifact_sha256 = "f" * 64  # type: ignore[misc]
    assert all(field.name for field in fields(H.HostArtifactBody))
