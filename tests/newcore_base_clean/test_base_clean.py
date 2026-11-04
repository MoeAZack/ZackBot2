from __future__ import annotations

import ast
import hashlib
from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path

import pytest

from newcore.base_clean import (
    FULL_COMMAND,
    NC01_MUTATION_COMMAND,
    NC02_MUTATION_COMMAND,
    BaseCleanRegister,
    CommandEvidence,
    GateEvidence,
    GateInputError,
    GatePolicy,
    GateProof,
    GateStatus,
    HoldCode,
    LatencyEvidence,
    MemoryEvidence,
    PathClass,
    PathRole,
    PathRule,
    ProofKind,
    RequestBudgetEvidence,
    TargetDisposition,
    canonical_bytes,
    evaluate_base_clean,
)


HEAD = "a" * 40
OTHER_HEAD = "b" * 40
HASH = "c" * 64
HOST = "owner-windows-host"
HERE = Path(__file__).parents[2]
def _register() -> BaseCleanRegister:
    rules = (
        PathRule(
            "evidence", PathClass.STILL_REQUIRED, PathRole.CONTRACT_EVIDENCE,
            (".github/", "contracts/", "data/", "do" + "cs/", "tests/"), (),
            ("tests/golden/goldenlib/adapters/legacy_backtest.py",
             "tests/golden/goldenlib/adapters/legacy_engine.py"),
            TargetDisposition.KEEP, ("verify_full",), None,
        ),
        PathRule(
            "newcore", PathClass.STILL_REQUIRED, PathRole.NEWCORE_REPLACEMENT,
            ("newcore/",), (), (), TargetDisposition.KEEP, ("wave_c",), None,
        ),
        PathRule(
            "runtime_assets", PathClass.STILL_REQUIRED, PathRole.RUNTIME_ASSET,
            ("ml_shadow/",), (), (), TargetDisposition.KEEP, ("legacy_runtime",), None,
        ),
        PathRule(
            "research", PathClass.RESEARCH_ONLY, PathRole.RESEARCH_EVIDENCE,
            ("research/", "research_evidence/", "tools/"), (), (),
            TargetDisposition.KEEP, ("research_lane",), None,
        ),
        PathRule(
            "golden_legacy_adapters", PathClass.TEMPORARY_ADAPTER, PathRole.COMPARISON_ADAPTER, (),
            ("tests/golden/goldenlib/adapters/legacy_backtest.py",
             "tests/golden/goldenlib/adapters/legacy_engine.py"), (),
            TargetDisposition.RETIRE_AFTER_COMPARISON, ("checkpoint_c_comparison",),
            "newcore_golden_adapters_accepted",
        ),
        PathRule(
            "legacy_target_delete", PathClass.STILL_REQUIRED, PathRole.RUNTIME_LEGACY, (),
            ("engine.py", "grid.py", "ai_filter.py"), (), TargetDisposition.TARGET_DELETE_NO_PORT,
            ("legacy_entrypoint", "pyinstaller_bundle"), "replacement_entrypoint_zero_consumers",
        ),
        PathRule(
            "root_product", PathClass.STILL_REQUIRED, PathRole.RUNTIME_LEGACY, (),
            ("app.py", "verify.py"), (), TargetDisposition.KEEP, ("legacy_runtime", "verify_full"), None,
        ),
    )
    policy = GatePolicy(
        (FULL_COMMAND, NC01_MUTATION_COMMAND, NC02_MUTATION_COMMAND),
        Decimal("1000"), Decimal("6750"), Decimal("23400"), Decimal("10"), Decimal("50"),
        Decimal("1"), Decimal("250"), Decimal("512"), Decimal("32"), 3_600_000, True,
        "3.12.10",
        "binance-usdm-v1-2026-11-03",
        "max(minimum,ceil(verified_limit*0.10));capped_at_verified_limit",
        "66e65242d60d23a8d07ecb99c17f4b449b28ce6b8ed64b5c05b248475937fa90",
        "request_weight_1m=60000;orders_10s=10000;orders_1m=60000",
        "ee19285ed8c051d52bc02ca3854a05359fafe80d9f050f062bc5c7bd6779e81f",
    )
    return BaseCleanRegister("zackbot-base-clean-register/1", 1, rules, policy)


TRACKED = tuple(sorted((
    ".github/workflows/verify.yml",
    "ai_filter.py",
    "app.py",
    "contracts/example/contract.json",
    "data/exchange_rules_testnet.json",
    "do" + "cs/newcore/BASE_CLEAN_CONTRACT.md",
    "engine.py",
    "grid.py",
    "ml_shadow/common.py",
    "newcore/base_clean.py",
    "research/icons/BTC.svg",
    "research_evidence/ledger/example.jsonl",
    "tests/golden/goldenlib/adapters/legacy_backtest.py",
    "tests/golden/goldenlib/adapters/legacy_engine.py",
    "tests/golden/test_golden.py",
    "tests/newcore_base_clean/test_base_clean.py",
    "tools/research/example.py",
    "verify.py",
)))


def _command(command: str, *, head: str = HEAD, passed: bool = True, complete: bool = True) -> CommandEvidence:
    return CommandEvidence(command, head, passed, complete, HASH)


def _proof(kind: ProofKind, *, head: str = HEAD, passed: bool = True) -> GateProof:
    return GateProof(kind, head, HOST, passed, HASH)


def _latency(**changes) -> LatencyEvidence:
    row = LatencyEvidence(
        HEAD,
        HOST,
        "3.12.10",
        Decimal("500"),
        Decimal("3000"),
        Decimal("10000"),
        Decimal("5"),
        Decimal("25"),
        Decimal("0.1"),
        Decimal("100"),
        HASH,
    )
    return replace(row, **changes)


def _memory(**changes) -> MemoryEvidence:
    row = MemoryEvidence(HEAD, HOST, 3_600_000, Decimal("256"), Decimal("16"), True, HASH)
    return replace(row, **changes)


def _request(**changes) -> RequestBudgetEvidence:
    row = RequestBudgetEvidence(
        HEAD,
        True,
        True,
        False,
        "binance-usdm-v1-2026-11-03",
        "max(minimum,ceil(verified_limit*0.10));capped_at_verified_limit",
        True,
        True,
        "66e65242d60d23a8d07ecb99c17f4b449b28ce6b8ed64b5c05b248475937fa90",
        "request_weight_1m=60000;orders_10s=10000;orders_1m=60000",
        "ee19285ed8c051d52bc02ca3854a05359fafe80d9f050f062bc5c7bd6779e81f",
    )
    return replace(row, **changes)


def _evidence(*, proofs=None, commands=None, tracked=TRACKED, latency=None, memory=None, request=None) -> GateEvidence:
    if proofs is None:
        proofs = tuple(_proof(kind) for kind in ProofKind)
    if commands is None:
        commands = tuple(_command(command) for command in (FULL_COMMAND, NC01_MUTATION_COMMAND, NC02_MUTATION_COMMAND))
    tracked = tuple(sorted(tracked))
    universe_hash = hashlib.sha256(canonical_bytes(tracked)).hexdigest()
    return GateEvidence(
        HEAD,
        HOST,
        tracked,
        "git_ls_files",
        True,
        universe_hash,
        tuple(commands),
        tuple(proofs),
        _latency() if latency is None else latency,
        _memory() if memory is None else memory,
        _request() if request is None else request,
    )


def test_complete_exact_head_evidence_is_eligible_and_deterministic() -> None:
    first = evaluate_base_clean(_register(), _evidence())
    second = evaluate_base_clean(_register(), _evidence())
    assert first == second
    assert first.status is GateStatus.ELIGIBLE
    assert first.holds == ()
    assert first.tracked_count == len(TRACKED)
    assert canonical_bytes(first) == canonical_bytes(second)


def test_current_expected_result_is_exactly_the_four_missing_proofs() -> None:
    report = evaluate_base_clean(_register(), _evidence(proofs=()))
    assert report.status is GateStatus.HOLD
    assert set(report.holds) == {
        HoldCode.GOLDEN_ADAPTERS_MISSING,
        HoldCode.REAL_TNET_MISSING,
        HoldCode.TWO_ACCOUNT_ISOLATION_MISSING,
        HoldCode.HOST_CODEC_REBASELINE_MISSING,
    }
    assert tuple(sorted(hold.value for hold in report.holds)) == (
        "golden_adapters_missing",
        "host_codec_rebaseline_missing",
        "real_tnet_missing",
        "two_account_isolation_missing",
    )


def test_digest_bound_current_host_artifact_removes_only_its_own_hold() -> None:
    evidence = _evidence(proofs=(_proof(ProofKind.CURRENT_HOST_CODEC_BASELINE),))
    report = evaluate_base_clean(_register(), evidence)
    assert report.status is GateStatus.HOLD
    assert report.holds == (
        HoldCode.GOLDEN_ADAPTERS_MISSING,
        HoldCode.REAL_TNET_MISSING,
        HoldCode.TWO_ACCOUNT_ISOLATION_MISSING,
    )


def test_register_uses_only_four_classes_and_evidence_is_a_typed_role() -> None:
    register = _register()
    assert {rule.classification for rule in register.rules} <= set(PathClass)
    assert len(PathClass) == 4
    evidence_rules = [rule for rule in register.rules if rule.role is PathRole.CONTRACT_EVIDENCE]
    assert evidence_rules and all(rule.classification is PathClass.STILL_REQUIRED for rule in evidence_rules)


def test_legacy_targets_are_still_required_no_port_until_zero_consumers() -> None:
    rule = next(rule for rule in _register().rules if rule.rule_id == "legacy_target_delete")
    assert set(rule.paths) == {"engine.py", "grid.py", "ai_filter.py"}
    assert rule.classification is PathClass.STILL_REQUIRED
    assert rule.target is TargetDisposition.TARGET_DELETE_NO_PORT
    assert rule.consumers and rule.expiry_gate == "replacement_entrypoint_zero_consumers"


def test_legacy_golden_adapters_are_temporary_but_pack_evidence_is_not() -> None:
    register = _register()
    temporary = next(rule for rule in register.rules if rule.rule_id == "golden_legacy_adapters")
    evidence = next(rule for rule in register.rules if rule.rule_id == "evidence")
    assert temporary.classification is PathClass.TEMPORARY_ADAPTER
    assert temporary.target is TargetDisposition.RETIRE_AFTER_COMPARISON
    assert evidence.classification is PathClass.STILL_REQUIRED
    assert set(temporary.paths) == set(evidence.excluded_paths)


def test_authoritative_commands_are_exact_and_include_both_mutation_scripts() -> None:
    assert _register().policy.required_commands == (
        "python verify.py full",
        "python tests/newcore/nc01_mutations.py",
        "python tests/newcore_store/nc02a_mutations.py",
    )


def test_register_cannot_raise_a_frozen_ceiling() -> None:
    register = _register()
    with pytest.raises(GateInputError):
        replace(register.policy, memory_private_max_mib=Decimal("1024"))


def test_target_delete_metadata_cannot_drop_zero_consumer_expiry() -> None:
    register = _register()
    rules = tuple(
        replace(rule, expiry_gate="other_gate") if rule.rule_id == "legacy_target_delete" else rule
        for rule in register.rules
    )
    result = evaluate_base_clean(replace(register, rules=rules), _evidence())
    assert HoldCode.REGISTER_INVALID in result.holds


def test_duplicate_register_rule_id_is_rejected() -> None:
    register = _register()
    with pytest.raises(GateInputError):
        replace(register, rules=register.rules + (register.rules[0],))


def test_unclassified_path_holds() -> None:
    evidence = _evidence(tracked=TRACKED + ("unknown/place.txt",))
    result = evaluate_base_clean(_register(), evidence)
    assert HoldCode.UNIVERSE_UNCLASSIFIED in result.holds


@pytest.mark.parametrize(
    "changes",
    [
        {"universe_complete": False},
        {"universe_sha256": "f" * 64},
    ],
)
def test_git_tracked_universe_requires_complete_hash_bound_evidence(changes: dict[str, object]) -> None:
    result = evaluate_base_clean(_register(), replace(_evidence(), **changes))
    assert HoldCode.UNIVERSE_UNVERIFIED in result.holds


def test_overlapping_classification_holds() -> None:
    register = _register()
    overlap = PathRule(
        "overlap", PathClass.DELETE, PathRole.RUNTIME_LEGACY, (), ("app.py",), (),
        TargetDisposition.KEEP, (), None,
    )
    result = evaluate_base_clean(replace(register, rules=register.rules + (overlap,)), _evidence())
    assert HoldCode.UNIVERSE_AMBIGUOUS in result.holds


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("missing", HoldCode.COMMAND_MISSING),
        ("failed", HoldCode.COMMAND_FAILED),
        ("stale", HoldCode.COMMAND_HEAD_MISMATCH),
    ],
)
def test_command_matrix_fails_closed(mode: str, expected: HoldCode) -> None:
    rows = [_command(FULL_COMMAND), _command(NC01_MUTATION_COMMAND)]
    if mode == "failed":
        rows.append(_command(NC02_MUTATION_COMMAND, passed=False))
    elif mode == "stale":
        rows.append(_command(NC02_MUTATION_COMMAND, head=OTHER_HEAD))
    result = evaluate_base_clean(_register(), _evidence(commands=tuple(rows)))
    assert expected in result.holds


@pytest.mark.parametrize("missing", tuple(ProofKind))
def test_each_required_proof_has_its_own_missing_hold(missing: ProofKind) -> None:
    proofs = tuple(_proof(kind) for kind in ProofKind if kind is not missing)
    result = evaluate_base_clean(_register(), _evidence(proofs=proofs))
    expected = {
        ProofKind.NEWCORE_GOLDEN_ADAPTERS: HoldCode.GOLDEN_ADAPTERS_MISSING,
        ProofKind.REAL_TNET_EXACT_HEAD: HoldCode.REAL_TNET_MISSING,
        ProofKind.TWO_ACCOUNT_ISOLATION: HoldCode.TWO_ACCOUNT_ISOLATION_MISSING,
        ProofKind.CURRENT_HOST_CODEC_BASELINE: HoldCode.HOST_CODEC_REBASELINE_MISSING,
    }[missing]
    assert result.holds == (expected,)


@pytest.mark.parametrize(
    "proof",
    [
        _proof(ProofKind.REAL_TNET_EXACT_HEAD, passed=False),
        _proof(ProofKind.REAL_TNET_EXACT_HEAD, head=OTHER_HEAD),
    ],
)
def test_failed_or_wrong_head_proof_never_promotes(proof: GateProof) -> None:
    proofs = tuple(proof if kind is proof.kind else _proof(kind) for kind in ProofKind)
    result = evaluate_base_clean(_register(), _evidence(proofs=proofs))
    assert result.status is GateStatus.HOLD
    assert ({HoldCode.PROOF_FAILED, HoldCode.PROOF_HEAD_MISMATCH} & set(result.holds))


def test_current_host_proof_cannot_be_reused_from_another_host() -> None:
    proofs = tuple(
        replace(_proof(kind), host_id="other-windows-host")
        if kind is ProofKind.CURRENT_HOST_CODEC_BASELINE else _proof(kind)
        for kind in ProofKind
    )
    result = evaluate_base_clean(_register(), _evidence(proofs=proofs))
    assert HoldCode.PROOF_HOST_MISMATCH in result.holds


@pytest.mark.parametrize("record", ["proof", "latency", "memory"])
def test_current_host_proof_latency_and_memory_require_one_artifact_digest(record: str) -> None:
    evidence = _evidence()
    altered = "f" * 64
    if record == "proof":
        proofs = tuple(
            replace(row, evidence_sha256=altered)
            if row.kind is ProofKind.CURRENT_HOST_CODEC_BASELINE else row
            for row in evidence.proofs
        )
        evidence = replace(evidence, proofs=proofs)
    elif record == "latency":
        evidence = replace(evidence, latency=replace(evidence.latency, evidence_sha256=altered))
    else:
        evidence = replace(evidence, memory=replace(evidence.memory, evidence_sha256=altered))
    result = evaluate_base_clean(_register(), evidence)
    assert result.status is GateStatus.HOLD
    assert result.holds == (HoldCode.HOST_CODEC_REBASELINE_MISSING,)


@pytest.mark.parametrize("record", ["latency", "memory"])
def test_current_host_proof_cannot_pass_without_all_digest_bound_records(record: str) -> None:
    evidence = replace(_evidence(), **{record: None})
    result = evaluate_base_clean(_register(), evidence)
    assert HoldCode.HOST_CODEC_REBASELINE_MISSING in result.holds


@pytest.mark.parametrize(
    "changes",
    [
        {"validate_us": Decimal("1000.1")},
        {"encode_us": Decimal("6750.1")},
        {"decode_us": Decimal("23400.1")},
        {"submit_ms": Decimal("10.1")},
        {"step_ms": Decimal("50.1")},
        {"snapshot_mean_ms": Decimal("1.1")},
        {"publish_ms": Decimal("250.1")},
    ],
)
def test_each_frozen_latency_ceiling_is_enforced(changes: dict[str, Decimal]) -> None:
    result = evaluate_base_clean(_register(), _evidence(latency=_latency(**changes)))
    assert HoldCode.LATENCY_EXCEEDED in result.holds


def test_current_host_codec_evidence_rejects_an_old_python_baseline() -> None:
    result = evaluate_base_clean(_register(), _evidence(latency=_latency(python_version="2.7.18")))
    assert result.status is GateStatus.HOLD
    assert result.holds == (HoldCode.HOST_CODEC_REBASELINE_MISSING,)


@pytest.mark.parametrize(
    "changes",
    [
        {"private_working_set_mib": Decimal("512.1")},
        {"growth_mib": Decimal("32.1")},
        {"duration_ms": 3_599_999},
        {"provisional": False},
    ],
)
def test_provisional_memory_ceilings_and_sixty_minute_run_are_enforced(changes: dict[str, object]) -> None:
    result = evaluate_base_clean(_register(), _evidence(memory=_memory(**changes)))
    assert HoldCode.MEMORY_EXCEEDED in result.holds


@pytest.mark.parametrize(
    "changes",
    [
        {"exchange_info_limits": False},
        {"user_rate_limit_limits": False},
        {"hardcoded_ceiling_present": True},
        {"endpoint_table_version": "guessed"},
        {"reserve_formula": "fixed numbers"},
        {"cost_table_verified": False},
        {"windows_verified": False},
        {"cost_table_sha256": "f" * 64},
        {"window_policy": "guessed"},
        {"policy_sha256": "f" * 64},
        {"head_sha": OTHER_HEAD},
    ],
)
def test_nc04_budget_evidence_must_be_exchange_derived_and_exact(changes: dict[str, object]) -> None:
    result = evaluate_base_clean(_register(), _evidence(request=_request(**changes)))
    assert HoldCode.REQUEST_BUDGET_UNVERIFIED in result.holds


def test_equivalent_rule_command_and_proof_order_has_one_result_identity() -> None:
    register = _register()
    evidence = _evidence()
    shuffled_register = replace(register, rules=tuple(reversed(register.rules)))
    shuffled_evidence = replace(
        evidence,
        commands=tuple(reversed(evidence.commands)),
        proofs=tuple(reversed(evidence.proofs)),
    )
    assert evaluate_base_clean(register, evidence) == evaluate_base_clean(shuffled_register, shuffled_evidence)


def test_exact_frozen_types_reject_forgery_and_mutable_path_container() -> None:
    evidence = _evidence()
    forged = object.__new__(GateEvidence)
    for field in fields(GateEvidence):
        object.__setattr__(forged, field.name, getattr(evidence, field.name))
    object.__setattr__(forged, "tracked_paths", list(evidence.tracked_paths))
    with pytest.raises(GateInputError):
        evaluate_base_clean(_register(), forged)


def test_returned_result_is_sealed_against_caller_mutation() -> None:
    result = evaluate_base_clean(_register(), _evidence())
    before = canonical_bytes(result)
    with pytest.raises((AttributeError, TypeError)):
        result.status = GateStatus.HOLD  # type: ignore[misc]
    assert canonical_bytes(result) == before


@pytest.mark.parametrize("value", [True, 1, 1.0, Decimal("NaN"), Decimal("Infinity")])
def test_wrong_numeric_types_and_nonfinite_decimals_are_rejected(value: object) -> None:
    with pytest.raises(GateInputError):
        _latency(validate_us=value)


def test_module_has_no_discovery_effect_or_runtime_import() -> None:
    source = (HERE / "newcore" / "base_clean.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_imports = {"os", "pathlib", "subprocess", "socket", "requests", "urllib", "threading", "time"}
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not (imported & forbidden_imports)
    assert not ({"open", "exec", "eval", "compile", "__import__"} & calls)
    assert "engine" not in imported and "app" not in imported
