from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from newcore.base_clean import ProofKind
from tools import base_clean_golden_proof as G


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
HERE = Path(__file__).parents[2]


def _case_and_trace():
    case = G.schema.load_all()[0]
    trades = []
    for expected in case["expect"]["trades"]:
        row = {key: value for key, value in expected.items() if key != "tol"}
        for key in ("R", "pnl"):
            if key in row:
                row[key] = float(row[key])
        trades.append(row)
    return case, {"trades": trades, "final": {}}


def _accepted(monkeypatch, adapters=("newcore_sim",)):
    case, trace = _case_and_trace()
    for adapter in adapters:
        monkeypatch.setitem(G.adapters.CAPS, adapter, {"final": frozenset(), "funding": True})
    plan = tuple(sorted(
        (G.PairSpec(case["id"], adapter, G.schema.contract_sha(case), "core") for adapter in adapters),
        key=lambda row: (row.case_id, row.adapter),
    ))
    traces = tuple((case["id"], adapter, trace) for adapter in reversed(adapters))
    artifact = G._seal_artifact(HEAD, plan, traces, {case["id"]: case})
    return artifact, plan, {case["id"]: case}, trace


def test_pending_known_divergence_and_missing_adapter_all_refuse_plan(monkeypatch) -> None:
    loaded = G.schema.load_all()
    monkeypatch.setattr(G.schema, "load_all", lambda: loaded)
    monkeypatch.setattr(G.schema, "status", lambda _case, _adapter: "pending_adapter")
    with pytest.raises(G.CollectorError, match="golden_status_not_required"):
        G._load_plan()

    monkeypatch.setattr(G.schema, "status", lambda _case, _adapter: "known_divergence")
    with pytest.raises(G.CollectorError, match="golden_status_not_required"):
        G._load_plan()

    monkeypatch.setattr(G.schema, "status", lambda _case, _adapter: "required")
    monkeypatch.setattr(G.adapters, "CAPS", {})
    with pytest.raises(G.CollectorError, match="adapter_unavailable"):
        G._load_plan()


def test_canonical_round_trip_and_gate_proof_bind_one_digest(monkeypatch) -> None:
    artifact, plan, cases, _trace = _accepted(monkeypatch)
    encoded = G.encode_artifact(artifact)
    decoded = G.decode_artifact(encoded)
    monkeypatch.setattr(G, "_load_plan", lambda: (plan, cases))
    proof = G.gate_evidence(decoded)
    assert decoded == artifact
    assert encoded == G.encode_artifact(decoded)
    assert proof.kind is ProofKind.NEWCORE_GOLDEN_ADAPTERS
    assert proof.head_sha == HEAD
    assert proof.host_id == G.HOST_ID
    assert proof.passed
    assert proof.evidence_sha256 == artifact.artifact_sha256


def test_artifact_is_deterministic_for_trace_input_order(monkeypatch) -> None:
    artifact, plan, cases, trace = _accepted(monkeypatch, G.NEWCORE_ADAPTERS)
    reversed_traces = tuple((row.case_id, row.adapter, trace) for row in reversed(plan))
    second = G._seal_artifact(HEAD, plan, reversed_traces, cases)
    assert artifact == second
    assert tuple((row.case_id, row.adapter) for row in artifact.body.pairs) == tuple(
        sorted((row.case_id, row.adapter) for row in artifact.body.pairs)
    )
    assert artifact.body.pack_sha256 == hashlib.sha256(G.canonical_bytes(plan)).hexdigest()


def test_mismatch_and_expectation_poison_refuse(monkeypatch) -> None:
    _artifact, plan, cases, trace = _accepted(monkeypatch)
    wrong = json.loads(json.dumps(trace))
    wrong["trades"][0]["exit"] = "FLATTEN"
    with pytest.raises(G.CollectorError, match="golden_comparison_mismatch"):
        G._seal_artifact(HEAD, plan, ((plan[0].case_id, plan[0].adapter, wrong),), cases)

    poisoned = json.loads(json.dumps(trace))
    poisoned["trades"][0]["sym"] = G.adapters.POISON
    with pytest.raises(G.CollectorError, match="adapter_expectation_leak"):
        G._seal_artifact(HEAD, plan, ((plan[0].case_id, plan[0].adapter, poisoned),), cases)


def test_missing_duplicate_and_unknown_trace_refuse(monkeypatch) -> None:
    _artifact, plan, cases, trace = _accepted(monkeypatch)
    row = (plan[0].case_id, plan[0].adapter, trace)
    with pytest.raises(G.CollectorError, match="golden_trace_missing"):
        G._seal_artifact(HEAD, plan, (), cases)
    with pytest.raises(G.CollectorError, match="golden_trace_duplicate"):
        G._seal_artifact(HEAD, plan, (row, row), cases)
    with pytest.raises(G.CollectorError, match="golden_trace_unknown"):
        G._seal_artifact(HEAD, plan, (("unknown-case", plan[0].adapter, trace),), cases)


def test_altered_digest_body_pack_or_matrix_refuse(monkeypatch) -> None:
    artifact, plan, cases, _trace = _accepted(monkeypatch)
    encoded = G.encode_artifact(artifact)
    doc = json.loads(encoded)
    doc["artifact_sha256"] = "f" * 64
    with pytest.raises(G.CollectorError, match="validation.envelope.digest"):
        G.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))

    doc = json.loads(encoded)
    doc["body"]["pairs"][0]["trace_sha256"] = "e" * 64
    with pytest.raises(G.CollectorError, match="validation.envelope.digest"):
        G.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))

    monkeypatch.setattr(G, "_load_plan", lambda: (
        (G.PairSpec(plan[0].case_id, plan[0].adapter, "d" * 64, plan[0].tier),), cases))
    with pytest.raises(G.CollectorError, match="artifact_pack_mismatch"):
        G.gate_evidence(artifact)


def test_duplicate_unknown_and_noncanonical_json_refuse(monkeypatch) -> None:
    artifact, _plan, _cases, _trace = _accepted(monkeypatch)
    encoded = G.encode_artifact(artifact)
    duplicate = encoded.replace(b'{"artifact_sha256":',
                                b'{"artifact_sha256":"' + b'f' * 64 + b'","artifact_sha256":', 1)
    with pytest.raises(G.CollectorError, match="validation.json.duplicate"):
        G.decode_artifact(duplicate)
    doc = json.loads(encoded)
    doc["body"]["extra"] = None
    with pytest.raises(G.CollectorError, match="validation.artifact.keys"):
        G.decode_artifact(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))
    with pytest.raises(G.CollectorError, match="validation.json.canonical"):
        G.decode_artifact(encoded + b"\n")


def test_records_are_frozen_and_reconstructed_before_proof(monkeypatch) -> None:
    artifact, plan, cases, _trace = _accepted(monkeypatch)
    with pytest.raises(FrozenInstanceError):
        artifact.body.head_sha = OTHER_HEAD  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        artifact.body.pairs[0].trace_sha256 = "f" * 64  # type: ignore[misc]
    monkeypatch.setattr(G, "_load_plan", lambda: (plan, cases))
    assert G.gate_evidence(artifact).evidence_sha256 == artifact.artifact_sha256


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"actual_head": OTHER_HEAD}, "head_mismatch"),
        ({"dirty": True}, "worktree_dirty"),
    ],
)
def test_exact_clean_head_is_mandatory(changes, code) -> None:
    values = {"expected_head": HEAD, "actual_head": HEAD, "dirty": False}
    values.update(changes)
    with pytest.raises(G.CollectorError, match=code):
        G._identity(**values)


def test_worker_timeout_and_failure_are_stable_without_raw_text(monkeypatch) -> None:
    monkeypatch.setattr(G.deadline, "run", lambda *_args, **_kwargs: SimpleNamespace(
        timed_out=True, returncode=None, stdout="", stderr="SECRET"))
    with pytest.raises(G.CollectorError, match="adapter_timeout") as timeout:
        G._run_worker()
    assert "SECRET" not in str(timeout.value)

    monkeypatch.setattr(G.deadline, "run", lambda *_args, **_kwargs: SimpleNamespace(
        timed_out=False, returncode=2, stdout="", stderr="SECRET"))
    with pytest.raises(G.CollectorError, match="adapter_failed") as failed:
        G._run_worker()
    assert "SECRET" not in str(failed.value)


def test_collection_failure_and_interrupt_suppress_exception_chaining(monkeypatch) -> None:
    monkeypatch.setattr(G, "_git_state", lambda _repo: (HEAD, False))
    monkeypatch.setattr(G, "_load_plan", lambda: (_ for _ in ()).throw(RuntimeError("SECRET")))
    with pytest.raises(G.CollectorError, match="collection_failed") as failed:
        G.collect(expected_head=HEAD, repo=HERE)
    assert failed.value.__cause__ is None and failed.value.__context__ is None
    assert "SECRET" not in str(failed.value)

    monkeypatch.setattr(G, "_load_plan", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(G.CollectorError, match="collection_interrupted") as interrupted:
        G.collect(expected_head=HEAD, repo=HERE)
    assert interrupted.value.__cause__ is None and interrupted.value.__context__ is None


def test_collector_is_default_off_and_has_no_network_runtime_or_strategy_imports() -> None:
    source = (HERE / "tools" / "base_clean_golden_proof.py").read_text(encoding="utf-8")
    forbidden = ("requests", "urllib", "socket", "binance_client", "engine", "strategies", "paper_books")
    assert all(f"import {name}" not in source and f"from {name}" not in source for name in forbidden)
    for path in (HERE / "verify.py", HERE / "tests" / "golden" / "runner.py",
                 HERE / "tests" / "golden" / "pack_worker.py"):
        assert "base_clean_golden_proof" not in path.read_text(encoding="utf-8")
