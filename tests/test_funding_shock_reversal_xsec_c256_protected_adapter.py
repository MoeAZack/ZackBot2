"""Hostile metadata-only tests for the C256 protected adapter PREP.

No test opens an archive store, market row, outcome, holdout, or real identity.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.research import funding_shock_reversal_xsec_c256_protected_adapter as A
from tools.research import funding_shock_reversal_xsec_c256_stagea as C256
from tools.research import run_funding_shock_reversal_xsec_c256_protected as CLI


def _synthetic_bindings() -> A.Bindings:
    return A.Bindings(contract={}, digest="b" * 64, paths=("a", "b"))


def _eval_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(evaluate=True, validate_adapter=False,
                              out=str(tmp_path / "never-written.json"),
                              identity_dir=str(tmp_path / "never-created"),
                              archive_store=str(tmp_path / "never-opened"))


def test_exact_frozen_source_authority_and_cost_bindings_validate_without_market_access():
    bindings = A.load_bindings()
    assert bindings.contract["candidate_id"] == 256
    assert bindings.contract["evaluation_locked"] is True
    assert bindings.contract["data_authority"]["archive_manifest_sha256"] == A.MANIFEST_SHA256
    assert bindings.contract["data_authority"]["universe_digest"] == A.PIT_DIGEST
    assert bindings.contract["costs"]["taker_fee_bps_per_fill_per_leg"] == 5.0
    assert bindings.contract["costs"]["adverse_slippage_bps_per_fill_per_leg"] == 2.0
    assert bindings.contract["costs"]["stress_adverse_slippage_bps_per_fill_per_leg"] == 10.0
    assert len(bindings.digest) == 64


def test_source_byte_tamper_refuses_before_stagea_or_manifest(monkeypatch):
    calls = []
    real = A._read_bound

    def hostile(rel, expected, label):
        calls.append(rel)
        if rel == A.SOURCE_MODULE_PATH:
            raise A.ProtectedAdapterError("C256 Stage-A module bytes changed")
        return real(rel, expected, label)

    monkeypatch.setattr(A, "_read_bound", hostile)
    with pytest.raises(A.ProtectedAdapterError, match="Stage-A module bytes changed"):
        A.load_bindings()
    assert calls == [A.SOURCE_MODULE_PATH]


def test_named_source_head_must_contain_the_exact_bound_blobs(monkeypatch):
    calls = []

    def fake_git(args, **unused):
        calls.append(args)
        if args == ["rev-parse", f"{A.SOURCE_RUNNER_HEAD}^{{commit}}"]:
            return SimpleNamespace(returncode=0, stdout=A.SOURCE_RUNNER_HEAD + "\n")
        if args[0:2] == ["rev-parse", f"{A.SOURCE_RUNNER_HEAD}:a"]:
            return SimpleNamespace(returncode=0, stdout="1" * 40 + "\n")
        return SimpleNamespace(returncode=0, stdout="2" * 40 + "\n")

    monkeypatch.setattr(A, "_git", fake_git)
    with pytest.raises(A.ProtectedAdapterError, match="differs from exact C256 source runner head"):
        A._verify_source_runner_head(("a",))
    assert calls[-2:] == [["rev-parse", f"{A.SOURCE_RUNNER_HEAD}:a"], ["hash-object", "--", "a"]]


@pytest.mark.parametrize("argv,message", [
    (["--evaluate", "--out", "a", "--out", "b", "--identity-dir", "i", "--archive-store", "s"],
     "duplicate singleton"),
    (["--evaluate", "--out", "a", "--identity-dir", "i"], "requires --out"),
    (["--validate-adapter", "--out", "a"], "accepts no evaluation paths"),
    (["--evaluate", "--out", "a", "--identity-dir", "i", "--archive-store", "s", "--escape"],
     "unrecognized arguments"),
    (["--evaluate", "--validate-adapter"], "not allowed"),
])
def test_strict_argv_rejects_duplicates_mixed_modes_missing_paths_and_unknowns(argv, message):
    with pytest.raises(A.ProtectedAdapterError, match=message):
        A._strict_parser(argv)


def test_public_evaluate_refuses_unprotected_checkout_before_resolver_or_claim(monkeypatch, tmp_path):
    touched = {"resolver": 0, "claim": 0}
    monkeypatch.setattr(A, "load_bindings", _synthetic_bindings)
    monkeypatch.setattr(A, "_clean_exact_protected_head",
                        lambda *_: (_ for _ in ()).throw(A.ProtectedAdapterError("clean HEAD")))
    monkeypatch.setattr(A, "_resolve_evaluator", lambda: touched.__setitem__("resolver", 1))
    monkeypatch.setattr(A, "_reserve_claim", lambda *args: touched.__setitem__("claim", 1))
    argv = ["--evaluate", "--out", str(tmp_path / "out"), "--identity-dir", str(tmp_path / "ids"),
            "--archive-store", str(tmp_path / "archive")]
    with pytest.raises(A.ProtectedAdapterError, match="clean HEAD"):
        A.guarded_dispatch(argv)
    assert touched == {"resolver": 0, "claim": 0}
    assert list(tmp_path.iterdir()) == []


def test_exact_protected_byte_containment_is_required_for_every_binding_and_adapter(monkeypatch):
    seen = []
    monkeypatch.setattr(A, "_clean_exact_protected_head", lambda *_: "f" * 40)

    def match(rel, ref):
        seen.append((rel, ref))
        return rel != "b"

    monkeypatch.setattr(A, "_protected_blob_matches", match)
    with pytest.raises(A.ProtectedAdapterError, match="exact b bytes"):
        A.require_protected_authority(_synthetic_bindings())
    assert seen == [("a", A.PROTECTED_REF), ("b", A.PROTECTED_REF)]


def test_even_forged_protected_gate_cannot_dispatch_without_reviewed_evaluator(monkeypatch, tmp_path):
    touched = []
    monkeypatch.setattr(A, "load_bindings", _synthetic_bindings)
    monkeypatch.setattr(A, "require_protected_authority", lambda *_: "f" * 40)
    monkeypatch.setattr(A, "_reserve_claim", lambda *args: touched.append("claim"))
    argv = ["--evaluate", "--out", str(tmp_path / "out"), "--identity-dir", str(tmp_path / "ids"),
            "--archive-store", str(tmp_path / "archive")]
    with pytest.raises(A.ProtectedAdapterError, match="REAL_EVALUATOR_NOT_BOUND"):
        A.guarded_dispatch(argv)
    assert touched == []
    assert list(tmp_path.iterdir()) == []


def test_stagea_capability_rejects_wrong_token_and_restores_after_success():
    original = C256.assert_real_evaluation_authorized
    with A._stagea_capability() as token:
        with pytest.raises(C256.EvaluationLocked, match="missing or invalid"):
            C256.assert_real_evaluation_authorized(gate={"capability": object()})
        C256.assert_real_evaluation_authorized(gate={"capability": token})
    assert C256.assert_real_evaluation_authorized is original
    with pytest.raises(C256.EvaluationLocked, match="market, outcome, identity"):
        C256.assert_real_evaluation_authorized(gate={"capability": token})


def test_stagea_lock_restores_and_failed_claim_is_consumed_when_evaluator_raises(monkeypatch, tmp_path):
    original = C256.assert_real_evaluation_authorized
    events = []
    fake_claim = tmp_path / "synthetic-claim-not-written"
    monkeypatch.setattr(A, "_reserve_claim", lambda *args: events.append("reserve") or fake_claim)
    monkeypatch.setattr(A, "_finish_claim", lambda claim, value: events.append((claim, dict(value))))
    monkeypatch.setattr(A, "_exclusive", lambda *args: events.append("unexpected-output"))

    def explode(args, gate):
        events.append("evaluator")
        C256.assert_real_evaluation_authorized(gate=gate)
        raise RuntimeError("synthetic stop")

    with pytest.raises(RuntimeError, match="synthetic stop"):
        A._execute_authenticated(_eval_args(tmp_path), _synthetic_bindings(), "f" * 40, explode)
    assert C256.assert_real_evaluation_authorized is original
    assert events[0:2] == ["reserve", "evaluator"]
    assert events[2][0] == fake_claim
    assert events[2][1]["state"] == "FAILED_CONSUMED"
    assert "unexpected-output" not in events
    assert list(tmp_path.iterdir()) == []


def test_cli_forwards_real_process_argv_and_returns_refusal(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(CLI, "guarded_dispatch", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(CLI.sys, "argv", ["prog", "--validate-adapter"])
    assert CLI.main() == 0
    assert seen == [["--validate-adapter"]]

    def refuse(argv):
        raise A.ProtectedAdapterError("closed")

    monkeypatch.setattr(CLI, "guarded_dispatch", refuse)
    assert CLI.main(["--evaluate"]) == 3
    assert "refused: closed" in capsys.readouterr().err
