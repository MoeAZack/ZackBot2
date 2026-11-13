from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import fields
from pathlib import Path

import pytest

from newcore.base_clean import ProofKind
from newcore.ports.rate_blocks import RateBlockState
from tools import base_clean_two_account_isolation as I


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
HOST = "host_" + "c" * 32
OTHER_HOST = "host_" + "d" * 32
HERE = Path(__file__).parents[2]


def artifact() -> I.IsolationArtifact:
    return I.run_two_account_isolation(head_sha=HEAD, host_id=HOST)


def test_real_account_context_fault_sequence_preserves_b_and_meets_deadline() -> None:
    row = artifact().body
    assert tuple(item.kind for item in row.fault_trace) == tuple(I.FaultKind)
    assert tuple(item.account_a_budget_state for item in row.fault_trace) == (
        RateBlockState.OPEN, RateBlockState.OPEN,
        RateBlockState.BLOCKED_UNTIL, RateBlockState.BLOCKED_UNKNOWN_DURATION,
    )
    assert all(item.account_b_queued == 1 for item in row.fault_trace)
    assert row.account_b_completed_offset_ms == 75 < row.account_b_deadline_offset_ms == 100
    assert row.no_cross_account_mutation and row.distinct_egress
    assert row.journal_isolated and row.venue_isolated


def test_429_then_418_is_latched_only_on_a_and_b_protection_completes() -> None:
    row = artifact().body
    assert row.account_a_latched_418_state.value == "refused"
    assert row.account_a_exception_state.value == "unknown"
    assert row.account_a_reconcile_before_restart
    assert row.account_a_journal_records == 2 and row.account_b_journal_records == 0
    assert row.account_b_budget_state is RateBlockState.OPEN
    assert row.account_b_protection_state.value == "completed"


def test_canonical_round_trip_digest_and_gate_proof() -> None:
    first = artifact()
    encoded = I.encode_artifact(first)
    second = I.decode_artifact(encoded)
    proof = I.gate_proof(second)
    assert first == second and encoded == I.encode_artifact(second)
    assert first.artifact_sha256 == hashlib.sha256(I.canonical_bytes(first.body)).hexdigest()
    assert proof.kind is ProofKind.TWO_ACCOUNT_ISOLATION and proof.passed
    assert (proof.head_sha, proof.host_id, proof.evidence_sha256) == (HEAD, HOST, first.artifact_sha256)


def test_artifact_is_redacted_and_contains_no_payload_or_exception_text() -> None:
    encoded = I.encode_artifact(artifact()).decode("ascii")
    forbidden = (
        "BTCUSDT", "zbn1", "proof-a", "proof-b", "50000", "0.001",
        "raw fake venue secret", "api_key", "api_secret", "https://",
    )
    assert not any(value.lower() in encoded.lower() for value in forbidden)


def test_altered_digest_body_duplicate_unknown_and_noncanonical_bytes_refuse() -> None:
    encoded = I.encode_artifact(artifact())
    doc = json.loads(encoded)
    doc["artifact_sha256"] = "f" * 64
    with pytest.raises(I.ProofError, match="validation.envelope.digest"):
        I.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))
    doc = json.loads(encoded)
    doc["body"]["account_b_completed_offset_ms"] = 76
    with pytest.raises(I.ProofError):
        I.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))
    duplicate = encoded.replace(b'{"artifact_sha256":',
                                b'{"artifact_sha256":"' + b'f' * 64 + b'","artifact_sha256":', 1)
    with pytest.raises(I.ProofError, match="validation.json.duplicate"):
        I.decode_artifact(duplicate)
    doc = json.loads(encoded); doc["body"]["unexpected"] = True
    with pytest.raises(I.ProofError, match="validation.artifact.keys"):
        I.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))
    with pytest.raises(I.ProofError, match="validation.json.canonical"):
        I.decode_artifact(encoded + b"\n")


@pytest.mark.parametrize(("field", "value"), (
    ("account_a_journal_records", True),
    ("account_b_completed_offset_ms", "75"),
    ("distinct_egress", 1),
    ("no_cross_account_mutation", []),
))
def test_exact_raw_field_types_refuse(field: str, value: object) -> None:
    encoded = I.encode_artifact(artifact())
    doc = json.loads(encoded); doc["body"][field] = value
    with pytest.raises(I.ProofError):
        I.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))


@pytest.mark.parametrize(("change", "code"), (
    ({"fault_trace": I._EXPECTED_TRACE[:-1]}, "isolation.fault_sequence"),
    ({"account_b_protection_state": I.WorkState.UNKNOWN}, "isolation.b_protection_not_completed"),
    ({"account_b_completed_offset_ms": 100}, "isolation.b_deadline"),
    ({"distinct_egress": False}, "isolation.distinct_egress"),
    ({"no_cross_account_mutation": False}, "isolation.no_cross_account_mutation"),
    ({"journal_isolated": False}, "isolation.journal_isolated"),
    ({"venue_isolated": False}, "isolation.venue_isolated"),
))
def test_failed_isolation_invariants_cannot_form_artifact(change: dict, code: str) -> None:
    body = artifact().body
    values = {field.name: getattr(body, field.name) for field in fields(I.IsolationArtifactBody)}
    values.update(change)
    with pytest.raises(I.ProofError, match=code):
        I.IsolationArtifactBody(**values)


def test_exact_forgery_and_post_build_mutation_never_reach_gate() -> None:
    row = artifact()
    forged_body = object.__new__(I.IsolationArtifactBody)
    for field in fields(I.IsolationArtifactBody):
        object.__setattr__(forged_body, field.name, getattr(row.body, field.name))
    object.__setattr__(forged_body, "no_cross_account_mutation", False)
    forged = object.__new__(I.IsolationArtifact)
    object.__setattr__(forged, "body", forged_body)
    object.__setattr__(forged, "artifact_sha256", hashlib.sha256(I.canonical_bytes(forged_body)).hexdigest())
    with pytest.raises(I.ProofError, match="isolation.no_cross_account_mutation"):
        I.gate_proof(forged)
    with pytest.raises((AttributeError, TypeError)):
        row.artifact_sha256 = "f" * 64  # type: ignore[misc]


@pytest.mark.parametrize(("values", "code"), (
    ((HEAD, HOST, OTHER_HEAD, HOST, False), "head_mismatch"),
    ((HEAD, HOST, HEAD, OTHER_HOST, False), "host_mismatch"),
    ((HEAD, HOST, HEAD, HOST, True), "worktree_dirty"),
))
def test_exact_head_host_and_clean_identity(values: tuple, code: str) -> None:
    with pytest.raises(I.ProofError, match=code):
        I._validate_identity(*values)


def test_collector_checks_identity_before_and_after(monkeypatch) -> None:
    states = iter(((HEAD, False), (HEAD, False)))
    monkeypatch.setattr(I, "_git_state", lambda _repo: next(states))
    monkeypatch.setattr(I, "derive_host_id", lambda: HOST)
    row = I.collect_two_account_isolation(expected_head=HEAD, expected_host_id=HOST, repo=HERE)
    assert row.body.head_sha == HEAD


def test_collector_suppresses_raw_unexpected_exception(monkeypatch) -> None:
    monkeypatch.setattr(I, "_git_state", lambda _repo: (HEAD, False))
    monkeypatch.setattr(I, "derive_host_id", lambda: HOST)
    monkeypatch.setattr(I, "run_two_account_isolation",
                        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("SECRET raw failure")))
    with pytest.raises(I.ProofError, match="collection_failed") as raised:
        I.collect_two_account_isolation(expected_head=HEAD, expected_host_id=HOST, repo=HERE)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert "SECRET" not in str(raised.value)


def test_module_has_no_network_credentials_or_real_venue_construction() -> None:
    source = (HERE / "tools" / "base_clean_two_account_isolation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not (imported & {"requests", "urllib", "socket", "http", "websocket", "keyring"})
    lowered = source.lower()
    assert not any(name in lowered for name in ("api_key", "api_secret", "os.environ", "binanceusdmtestnetvenue("))
