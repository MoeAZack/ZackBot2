from __future__ import annotations

import copy
import gzip
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.research.c257_successor_guard import (
    BASE_COMMIT,
    C256_BYTE_SHA256,
    C256_COMMIT,
    C256_PATH,
    C257GuardError,
    OUTPUT_NAMES,
    OUTPUT_PATHS,
    PREP_PATH,
    ROOT,
    SUCCESSOR_PATH,
    _resolve_c256,
    _strict_json,
    validate_scaffold,
    verify_pin_amendment,
    verify_scaffold_files,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _with_digest(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["digest"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _run(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout.decode("ascii").strip()


def _authority_repo(tmp_path: Path, *, coverage_gaps: int = 0,
                    mutate_plan: bool = False) -> tuple[Path, str, dict[str, dict[str, str]]]:
    repo = tmp_path / "authority"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(ROOT), str(repo)],
                   capture_output=True, check=True)
    # GitHub Actions checks out a synthetic merge commit shallowly.  The workflow
    # fetches BASE_COMMIT into the source object store, but a shared clone does not
    # necessarily advertise that detached object.  Fetch the exact local object
    # explicitly; this is still offline and keeps the hostile authority repository
    # anchored to the frozen protected base.
    subprocess.run(["git", "-C", str(repo), "fetch", "--no-tags", "--depth=1",
                    str(ROOT), BASE_COMMIT], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "fetch", "--no-tags", "--depth=1",
                    str(ROOT), C256_COMMIT], capture_output=True, check=True)
    _run(repo, "checkout", "--detach", BASE_COMMIT)
    _run(repo, "config", "user.name", "C257 Test")
    _run(repo, "config", "user.email", "c257@example.invalid")

    manifest = _with_digest({
        "format": "zb-binance-um-archive-manifest/6",
        "authority_id": "BINANCE-UM-PRECUTOFF-AUTHORITY-V6",
        "cutoff_exclusive_ms": 1793836800000,
        "active_base_files_v5": [],
        "files_v6": [],
    })
    manifest_raw = gzip.compress(_canonical(manifest), mtime=0)
    cursor, end, weeks = datetime(2024,10,14,tzinfo=timezone.utc), datetime(2026,11,2,tzinfo=timezone.utc), []
    while cursor <= end:
        weeks.append({"monday_ms": int(cursor.timestamp()*1000), "books": {"crypto": {"members":["AAAUSDT"]}}})
        cursor += timedelta(days=7)
    pit = _with_digest({
        "format": "zb-pit-universe/2", "universe_id": "PIT-TOP40-QV30D-V9",
        "manifest_digest": manifest["digest"], "weeks": weeks,
    })
    pit_raw = _canonical(pit)
    filter_v4 = _with_digest({
        "authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY",
        "version": 4, "status": "FROZEN", "pit_universe_path": OUTPUT_PATHS["pit_v9"],
        "pit_universe_sha256": hashlib.sha256(pit_raw).hexdigest(),
        "pit_universe_digest": pit["digest"],
        "fold_bindings": {"development_eligible_segment":"2024-12-13T04:44:48Z",
            "wf1":"2024-12-13T04:44:48Z","wf2":"2024-12-13T04:44:48Z",
            "wf3":"2026-02-17T04:13:28Z","sealed_holdout":"2026-02-17T04:13:28Z"},
    })
    filter_raw = _canonical(filter_v4)
    streams = {name: 0 for name in (
        "klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null")}
    if coverage_gaps:
        streams["klines:1h"] = coverage_gaps
    coverage = _with_digest({
        "format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
        "authority_version": 4, "manifest_digest": manifest["digest"],
        "pit_universe_digest": pit["digest"], "filter_authority_digest": filter_v4["digest"],
        "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
        "cutoff_exclusive_ms":1793836800000,
        "required_streams": list(streams),
        "guaranteed_period_gaps_by_stream": streams,
        "guaranteed_period_gaps": coverage_gaps,
        "gaps": ([{"stream": "klines:1h", "synthetic": True}] if coverage_gaps else []),
    })
    raw_by_name = {
        "manifest_v6": manifest_raw, "pit_v9": pit_raw,
        "filter_v4": filter_raw, "coverage_v4": _canonical(coverage),
    }
    documents = {"manifest_v6": manifest, "pit_v9": pit,
                 "filter_v4": filter_v4, "coverage_v4": coverage}
    for name, raw in raw_by_name.items():
        path = repo / OUTPUT_PATHS[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if mutate_plan:
        plan_path = repo / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["status"] = "FORGED"
        plan_path.write_bytes(_canonical(plan))
    _run(repo, "add", "--all")
    _run(repo, "commit", "-m", "synthetic authority outputs")
    head = _run(repo, "rev-parse", "HEAD")
    _run(repo, "update-ref", "refs/remotes/origin/master", head)
    pins = {
        name: {"path": OUTPUT_PATHS[name],
               "byte_sha256": hashlib.sha256(raw_by_name[name]).hexdigest(),
               "content_digest": documents[name]["digest"]}
        for name in OUTPUT_NAMES
    }
    return repo, head, pins


def _amend(scaffold: dict, head: str, pins: dict) -> dict:
    amended = copy.deepcopy(scaffold)
    amended["status"] = "FROZEN_AUTHORITY_PINNED_NOT_EVALUATED"
    amended["authority_v6"]["protected_authority_head"] = head
    amended["authority_v6"]["output_pins"] = copy.deepcopy(pins)
    return amended


@pytest.fixture(scope="module")
def valid_authority(tmp_path_factory):
    return _authority_repo(tmp_path_factory.mktemp("c257-authority"))


def test_scaffold_is_exact_locked_and_metadata_only() -> None:
    result = verify_scaffold_files()
    assert result == {
        "status": "LOCKED",
        "successor_sha256": "768a4f6b6a748a11cf9ab0ccc7226e7269f5bb63e90f27d652bc54e1758d931f",
        "prep_sha256": "d6a6c8607b819dde4d35644ca39ca251a5a1a94b93f02a94798ed4ec0110cae3",
    }


def test_guard_resolves_exact_c256_commit_path_blob_and_multiplicity() -> None:
    contract = _resolve_c256(_load(SUCCESSOR_PATH), ROOT)
    assert _run(ROOT, "rev-parse", f"{C256_COMMIT}:{C256_PATH}") == "1a12852c5d77ddb3c946f068c4c23bda88cf7ac9"
    raw = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob", f"{C256_COMMIT}:{C256_PATH}"],
                         capture_output=True, check=True).stdout
    assert hashlib.sha256(raw).hexdigest() == C256_BYTE_SHA256
    assert contract["multiplicity"]["primary_count"] == 1
    assert contract["multiplicity"]["candidate_familywise_alpha_ceiling"] == 0.0001953125
    assert "0.05/256" in contract["multiplicity"]["rule"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("status", "FROZEN_AUTHORITY_PINNED_NOT_EVALUATED"),
        lambda value: value.__setitem__("default_off", False),
        lambda value: value.__setitem__("evaluation_locked", False),
        lambda value: value.__setitem__("paper_eligibility", "ALLOW"),
        lambda value: value["economic_inheritance"].__setitem__("exact_rule", "same enough"),
        lambda value: value["economic_inheritance"].__setitem__("multiplicity_primary_count", True),
        lambda value: value["economic_inheritance"].__setitem__("multiplicity_alpha_ceiling", 0.05),
        lambda value: value["economic_inheritance"]["unchanged_sections"].remove("costs"),
        lambda value: value["authority_v6"].__setitem__("forbidden_before_pin", []),
        lambda value: value.__setitem__("evaluation_preconditions", []),
    ],
)
def test_scaffold_schema_and_canonical_hash_reject_every_relaxation(mutation) -> None:
    successor, prep = _load(SUCCESSOR_PATH), _load(PREP_PATH)
    mutation(successor)
    with pytest.raises(C257GuardError, match="scaffold canonical hash mismatch"):
        validate_scaffold(successor, prep)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("status", "READY"),
        lambda value: value.__setitem__("allowed_cli", ["--evaluate"]),
        lambda value: value.__setitem__("evaluator", "forged.py"),
        lambda value: value["capability_policy"].__setitem__("mainnet", "ALLOW"),
        lambda value: value.__setitem__("required_independent_reviews", []),
        lambda value: value.pop("failure_policy"),
    ],
)
def test_prep_schema_and_canonical_hash_reject_every_relaxation(mutation) -> None:
    successor, prep = _load(SUCCESSOR_PATH), _load(PREP_PATH)
    mutation(prep)
    with pytest.raises(C257GuardError, match="prep canonical hash mismatch"):
        validate_scaffold(successor, prep)


def test_strict_json_rejects_duplicate_key_bypass() -> None:
    with pytest.raises(C257GuardError, match="duplicate key"):
        _strict_json(b'{"default_off":true,"default_off":false}', "hostile")


def test_exact_protected_tree_and_four_real_pins_are_accepted(valid_authority) -> None:
    repo, head, pins = valid_authority
    scaffold = _load(SUCCESSOR_PATH)
    verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)


def test_invented_commit_is_rejected(valid_authority) -> None:
    repo, _head, pins = valid_authority
    scaffold = _load(SUCCESSOR_PATH)
    invented = "a" * 40
    with pytest.raises(C257GuardError, match="rev-parse"):
        verify_pin_amendment(scaffold, _amend(scaffold, invented, pins), repo=repo)


def test_real_but_unprotected_head_is_rejected(tmp_path: Path) -> None:
    repo, head, pins = _authority_repo(tmp_path)
    _run(repo, "update-ref", "refs/remotes/origin/master", BASE_COMMIT)
    scaffold = _load(SUCCESSOR_PATH)
    with pytest.raises(C257GuardError, match="not protected ancestry"):
        verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)


@pytest.mark.parametrize("name", OUTPUT_NAMES)
def test_invented_byte_hash_for_each_output_is_rejected(valid_authority, name: str) -> None:
    repo, head, original_pins = valid_authority
    pins = copy.deepcopy(original_pins)
    pins[name]["byte_sha256"] = "f" * 64
    scaffold = _load(SUCCESSOR_PATH)
    with pytest.raises(C257GuardError, match=f"{name} byte hash mismatch"):
        verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)


@pytest.mark.parametrize("name", OUTPUT_NAMES)
def test_invented_content_digest_for_each_output_is_rejected(valid_authority, name: str) -> None:
    repo, head, original_pins = valid_authority
    pins = copy.deepcopy(original_pins)
    pins[name]["content_digest"] = "e" * 64
    scaffold = _load(SUCCESSOR_PATH)
    with pytest.raises(C257GuardError, match=f"{name} content digest mismatch"):
        verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)


def test_changed_frozen_plan_in_protected_tree_is_rejected(tmp_path: Path) -> None:
    repo, head, pins = _authority_repo(tmp_path, mutate_plan=True)
    scaffold = _load(SUCCESSOR_PATH)
    with pytest.raises(C257GuardError, match="plan bytes changed"):
        verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)


def test_coverage_claim_with_real_nonzero_gaps_is_rejected(tmp_path: Path) -> None:
    repo, head, pins = _authority_repo(tmp_path, coverage_gaps=1)
    scaffold = _load(SUCCESSOR_PATH)
    with pytest.raises(C257GuardError, match="has guaranteed-period gaps"):
        verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)


def test_missing_pin_and_extra_pin_fields_are_rejected(valid_authority) -> None:
    repo, head, original_pins = valid_authority
    pins = copy.deepcopy(original_pins)
    scaffold = _load(SUCCESSOR_PATH)
    pins.pop("coverage_v4")
    with pytest.raises(C257GuardError, match="output pin set mismatch"):
        verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)
    pins = copy.deepcopy(original_pins)
    pins["manifest_v6"]["trusted"] = True
    with pytest.raises(C257GuardError, match="manifest_v6 pin fields invalid"):
        verify_pin_amendment(scaffold, _amend(scaffold, head, pins), repo=repo)


def test_non_authority_amendment_is_rejected_after_real_pin_verification(valid_authority) -> None:
    repo, head, pins = valid_authority
    scaffold = _load(SUCCESSOR_PATH)
    amended = _amend(scaffold, head, pins)
    amended["default_off"] = False
    with pytest.raises(C257GuardError, match="changed a non-authority field"):
        verify_pin_amendment(scaffold, amended, repo=repo)
