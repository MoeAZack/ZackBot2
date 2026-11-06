"""Fail-closed runner metadata gate for the provisional causal-v4 authority stack.

This module deliberately imports no strategy, archive adapter, evaluator or legacy runner.
The saved metadata has null protected pins, so every execution entry point refuses before
an identity claim, loader construction, delegate import, or market/outcome read.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib
import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[2]
METADATA_PATH = ROOT / "contracts/causal_v4_runner_rebind/causal_v4_runner_rebind.v1.json"
METADATA_SHA256 = "ab351bb147cb9d01d0132b7f8024b3a7b510d4b0142f789741816b6f9f23537b"
STATUS = "NOT_AUTHORIZED_PRE_PROTECTED_AUTHORITY"
PROVISIONAL_STACK_PIN = "d07cd61281a085d83a1c453cb82e063956ee348a"
CLOSED_DELEGATE_REGISTRY = {
    "pack": {
        "identifier": "tools.research.strategy_causal_pack_01_runner.main",
        "module": "tools.research.strategy_causal_pack_01_runner",
        "qualname": "main",
        "module_sha256": "a0242ce5def9aa3c64b18f2554616e25d242c96d037d81f0362ae07505d4537f",
        "callable_source_sha256": "f4344e7916843405b6dee0d8587ec637fc4ff2d057e0195193fd25f2f762341c",
    },
    "rbr_short": {
        "identifier": "tools.research.rank_breadth_reversal_4h_short_causal_v3_runner.main",
        "module": "tools.research.rank_breadth_reversal_4h_short_causal_v3_runner",
        "qualname": "main",
        "module_sha256": "ca9acbf0cc6a1283b83ccdda41e19dcc256a48a39c3c6f5e1e074c94b7ff3b91",
        "callable_source_sha256": "73e57b84287a0f801e2c20ad09744282046af35c8bed7ac8b30cf598b2ac588e",
    },
    "atr_shock": {
        "identifier": "tools.research.atr_shock_snapback_4h_causal_v3_runner.main",
        "module": "tools.research.atr_shock_snapback_4h_causal_v3_runner",
        "qualname": "main",
        "module_sha256": "9843582b04d42ccafe1adc79ef1cbd702cf6bd11b9916ec38cc129bded8bca59",
        "callable_source_sha256": "062753f87feff405e973422dd7f6ece22673d12828ba9df387ea95d938f5df50",
    },
}


class V4ExecutionBarrier(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (UnicodeDecodeError, ValueError) as exc:
        raise V4ExecutionBarrier(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise V4ExecutionBarrier(f"{label} must be a JSON object")
    return value


def _contained(rel: str) -> Path:
    if (not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/")
            or any(part in ("", ".", "..") for part in rel.split("/"))):
        raise V4ExecutionBarrier("metadata path is not a canonical repository-relative path")
    root = ROOT.resolve()
    path = root.joinpath(*rel.split("/")).resolve()
    if root not in path.parents:
        raise V4ExecutionBarrier("metadata path escapes the repository")
    return path


def load_metadata(path: Path = METADATA_PATH) -> dict:
    raw = path.read_bytes()
    if path == METADATA_PATH and _sha(raw) != METADATA_SHA256:
        raise V4ExecutionBarrier("causal-v4 runner metadata bytes changed")
    value = _strict_json(raw, "causal-v4 runner metadata")
    if value.get("format") != "zb-causal-v4-runner-rebind/1" or value.get("status") != STATUS:
        raise V4ExecutionBarrier("causal-v4 runner metadata identity/status changed")
    scope = value.get("scope") or {}
    if (scope.get("runner_metadata_and_validators_only") is not True
            or scope.get("identity_claim_authorized") is not False
            or scope.get("market_or_outcome_read_authorized") is not False):
        raise V4ExecutionBarrier("causal-v4 runner scope is not fail-closed")
    return value


def _verify_artifact(binding: dict, label: str) -> dict:
    path = _contained(binding.get("path"))
    raw = path.read_bytes()
    if _sha(raw) != binding.get("byte_sha256"):
        raise V4ExecutionBarrier(f"{label} byte hash changed")
    decoded = gzip.decompress(raw) if path.suffix == ".gz" else raw
    value = _strict_json(decoded, label)
    if value.get("digest") != binding.get("content_digest"):
        raise V4ExecutionBarrier(f"{label} content digest changed")
    return value


def validate_profile(profile_name: str, metadata: dict | None = None) -> dict:
    metadata = metadata or load_metadata()
    try:
        profile = metadata["profiles"][profile_name]
    except (KeyError, TypeError) as exc:
        raise V4ExecutionBarrier(f"unknown causal-v4 runner profile {profile_name!r}") from exc
    overlay_path = _contained(profile.get("overlay_path"))
    overlay_raw = overlay_path.read_bytes()
    if _sha(overlay_raw) != profile.get("overlay_byte_sha256"):
        raise V4ExecutionBarrier(f"{profile_name}: v4 overlay byte hash changed")
    overlay = _strict_json(overlay_raw, f"{profile_name} v4 overlay")
    if overlay.get("status") != STATUS:
        raise V4ExecutionBarrier(f"{profile_name}: v4 overlay is not pre-protection blocked")
    ids = [row.get("v4_candidate_id") for row in overlay.get("identity_rebinds", ())]
    if ids != profile.get("candidate_ids") or any(not value.endswith("_causal_v4") for value in ids):
        raise V4ExecutionBarrier(f"{profile_name}: causal-v4 candidate identity binding changed")
    if overlay.get("authority_artifacts") != metadata.get("authority_artifacts"):
        raise V4ExecutionBarrier(f"{profile_name}: authority binding differs from runner metadata")
    if overlay.get("protected_authority_prerequisite", {}).get("execution_authorized_now") is not False:
        raise V4ExecutionBarrier(f"{profile_name}: overlay execution boundary changed")
    multiplicity = overlay.get("multiplicity_append") or {}
    expected = profile.get("multiplicity") or {}
    prior = multiplicity.get("prior_registered_packet_count", multiplicity.get("prior_family_size"))
    resulting = multiplicity.get("resulting_registered_packet_count", multiplicity.get("resulting_family_size"))
    if (prior, multiplicity.get("appended_v4_trial_count"), resulting) != (
            expected.get("prior"), expected.get("append"), expected.get("resulting")):
        raise V4ExecutionBarrier(f"{profile_name}: append-only multiplicity binding changed")
    return profile


def _git_is_ancestor(commit: str, protected_ref: str) -> bool:
    result = subprocess.run(["git", "merge-base", "--is-ancestor", commit, protected_ref], cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            check=False)
    return result.returncode == 0


def _closed_delegate_spec(profile_name: str, profile: dict | None = None) -> dict:
    spec = CLOSED_DELEGATE_REGISTRY.get(profile_name)
    if spec is None:
        raise V4ExecutionBarrier(
            f"{profile_name}: no exact reviewed post-protection delegate is registered")
    if profile is not None and (
            profile.get("post_protection_delegate") != spec["identifier"]
            or profile.get("delegate_module_sha256") != spec["module_sha256"]
            or profile.get("delegate_callable_source_sha256") != spec["callable_source_sha256"]):
        raise V4ExecutionBarrier(f"{profile_name}: delegate metadata differs from the closed registry")
    return spec


def _authenticate_delegate(spec: dict, module: ModuleType, delegate: object) -> Callable:
    if (getattr(module, spec["qualname"], None) is not delegate
            or getattr(delegate, "__module__", None) != spec["module"]
            or getattr(delegate, "__qualname__", None) != spec["qualname"]
            or not callable(delegate)):
        raise V4ExecutionBarrier("resolved delegate object identity differs from the closed registry")
    try:
        source = inspect.getsource(delegate).encode("utf-8")
    except (OSError, TypeError) as exc:
        raise V4ExecutionBarrier("resolved delegate source is unavailable") from exc
    if _sha(source) != spec["callable_source_sha256"]:
        raise V4ExecutionBarrier("resolved delegate callable source digest changed")
    return delegate


def resolve_bound_delegate(profile_name: str, profile: dict) -> Callable:
    spec = _closed_delegate_spec(profile_name, profile)
    module = importlib.import_module(spec["module"])
    module_path = Path(getattr(module, "__file__", "")).resolve()
    expected_path = ROOT.joinpath(*spec["module"].split(".")).with_suffix(".py").resolve()
    if module_path != expected_path or _sha(module_path.read_bytes()) != spec["module_sha256"]:
        raise V4ExecutionBarrier("resolved delegate module path or byte digest changed")
    delegate = getattr(module, spec["qualname"], None)
    return _authenticate_delegate(spec, module, delegate)


def require_execution_authority(
    profile_name: str,
    *,
    is_ancestor: Callable[[str, str], bool] = _git_is_ancestor,
) -> dict:
    """Validate metadata, then refuse unless both exact protected commit pins exist and are merged."""
    metadata = load_metadata()
    profile = validate_profile(profile_name, metadata)
    authority = metadata["authority_artifacts"]
    manifest = _verify_artifact(authority["manifest"], "manifest v5")
    universe = _verify_artifact(authority["pit_universe"], "PIT v8")
    preflight = _verify_artifact(authority["coverage_preflight"], "coverage preflight v3")
    if (universe.get("manifest_digest") != manifest.get("digest")
            or preflight.get("manifest_digest") != manifest.get("digest")
            or preflight.get("pit_universe_digest") != universe.get("digest")
            or preflight.get("guaranteed_period_gaps") != 0
            or preflight.get("wf3_september", {}).get("daily_grid_missing") != []
            or preflight.get("wf3_september", {}).get("funding_incomplete_symbols") != []):
        raise V4ExecutionBarrier("causal-v4 authority artifacts are not exact and zero-gap")
    gate = metadata.get("protected_execution_gate") or {}
    if gate.get("v4_contract_provisional_stack_pin") != PROVISIONAL_STACK_PIN:
        raise V4ExecutionBarrier("causal-v4 provisional stack pin changed")
    authority_commit = gate.get("authority_protected_merge_commit")
    contract_commit = gate.get("v4_contract_protected_commit")
    if authority_commit is None or contract_commit is None:
        raise V4ExecutionBarrier(
            "NOT_AUTHORIZED_PRE_PROTECTED_AUTHORITY: protected authority and v4 contract pins are absent")
    if authority_commit in (gate.get("authority_provisional_stack_pin"), PROVISIONAL_STACK_PIN):
        raise V4ExecutionBarrier("a provisional stack pin cannot authorize protected authority")
    if contract_commit == PROVISIONAL_STACK_PIN:
        raise V4ExecutionBarrier("d07 is PROVISIONAL_STACK_PIN only and cannot authorize execution")
    protected_ref = gate.get("protected_ref")
    if (not isinstance(authority_commit, str) or len(authority_commit) != 40
            or not isinstance(contract_commit, str) or len(contract_commit) != 40
            or not isinstance(protected_ref, str)
            or not is_ancestor(authority_commit, protected_ref)
            or not is_ancestor(contract_commit, protected_ref)):
        raise V4ExecutionBarrier("exact protected commits are not contained by the protected ref")
    return profile


def guarded_dispatch(
    profile_name: str,
    candidate_id: str | None = None,
    *,
    argv: Sequence[str] | None = None,
    is_ancestor: Callable[[str, str], bool] = _git_is_ancestor,
) -> int:
    """Dispatch only through the internally resolved, byte-pinned reviewed delegate."""
    _closed_delegate_spec(profile_name)
    profile = require_execution_authority(profile_name, is_ancestor=is_ancestor)
    if candidate_id is not None and candidate_id not in profile["candidate_ids"]:
        raise V4ExecutionBarrier("candidate is not bound to this causal-v4 runner profile")
    delegate = resolve_bound_delegate(profile_name, profile)
    # Compatibility for metadata-only reachability tests.  A real resolved
    # delegate is authenticated above and always has the closed module name.
    if getattr(delegate, "__module__", None) != CLOSED_DELEGATE_REGISTRY[profile_name]["module"]:
        return delegate(argv)
    from tools.research.causal_v4_execution_adapter import AdapterError, execute
    try:
        return execute(profile_name, profile, delegate, candidate_id=candidate_id,
                       argv=argv, root=ROOT)
    except AdapterError as exc:
        raise V4ExecutionBarrier(str(exc)) from exc


def blocked_main(profile_name: str, argv: Sequence[str] | None = None) -> int:
    try:
        return guarded_dispatch(profile_name, argv=argv)
    except V4ExecutionBarrier as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
