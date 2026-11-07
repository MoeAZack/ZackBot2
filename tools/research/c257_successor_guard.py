from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SUCCESSOR_PATH = ROOT / "research_evidence/prereg/funding_shock_reversal_xsec_c257_successor.v1.json"
PREP_PATH = ROOT / "contracts/strategy_causal_pack_01/c257_successor_adapter_prep.v1.json"
PLAN_PATH = ROOT / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
BASE_COMMIT = "a02c33f3cc343ee2af09bffcf35adb5a26fc0fd8"
PLAN_COMMIT = "64adf3a41d7a521d2af480adc7b2f23a8fa6a0c3"
PLAN_SHA256 = "30080d16132348fabceb91bffe110f43ee33824c4496622f736ee6a6c4d7af60"
SCAFFOLD_BYTE_SHA256 = "768a4f6b6a748a11cf9ab0ccc7226e7269f5bb63e90f27d652bc54e1758d931f"
SCAFFOLD_CANONICAL_SHA256 = "0c0bc19b603bbc57ad086136a55858edd580659a70d8589711b01d6155bc5696"
PREP_BYTE_SHA256 = "d6a6c8607b819dde4d35644ca39ca251a5a1a94b93f02a94798ed4ec0110cae3"
PREP_CANONICAL_SHA256 = "a07318e1e4eb6bb22e4546d0d02a10cee0c6a39d14e253807fd70aa2ccff8b53"
C256_COMMIT = "6cc7148a453be054f5c02ecf1eecc8369fcfc533"
C256_PATH = "research_evidence/prereg/funding_shock_reversal_xsec_c256_stagea.v1.json"
C256_BLOB_OID = "1a12852c5d77ddb3c946f068c4c23bda88cf7ac9"
C256_BYTE_SHA256 = "88294e16a44c4e2219284abeb26f4085123b9fd9de0c5120d0a19d5b84a94b54"
PAPER_ELIGIBILITY = "BLOCK_UNTIL_AUTHORITY_V6_EXACT_PIN_AND_INDEPENDENT_OOS_PASS"
EXACT_ECONOMIC_RULE = (
    "Every value in every unchanged section is inherited byte-semantically from the exact C256 "
    "contract. Signal, thresholds, side construction, pairing, execution, risk, costs, split "
    "boundaries, gates, multiplicity and PAPER terms may not change."
)
EXACT_MULTIPLICITY_RULE = (
    "Only the combined paired book is promotable. Test its performance p-value at 0.05/256; "
    "diagnostics and baselines cannot be selected as alternatives."
)
OUTPUT_NAMES = ("manifest_v6", "pit_v9", "filter_v4", "coverage_v4")
UNCHANGED_SECTIONS = (
    "hypothesis", "economic_distinction", "walk_forward", "funding_surprise",
    "selection_and_pairing", "execution", "position_and_portfolio", "costs", "gates",
    "bootstrap_specification", "expected_test_count", "trial_budget", "multiplicity",
    "falsification",
)
OUTPUT_PATHS = {
    "manifest_v6": "research_evidence/manifests/binance-um-archive-precutoff-v6.json.gz",
    "pit_v9": "research_evidence/universe/pit-top40-qv30d-precutoff-v9.json",
    "filter_v4": "contracts/strategy_causal_pack_01/exchange_filter_authority.v4.json",
    "coverage_v4": "contracts/strategy_causal_pack_01/precutoff_authority_coverage_preflight.v4.json",
}
PIN_FIELDS = frozenset({"path", "byte_sha256", "content_digest"})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


class C257GuardError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise C257GuardError(message)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                          allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise C257GuardError("value is not canonical JSON") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise C257GuardError(f"{label} has duplicate key {key}")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except C257GuardError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise C257GuardError(f"{label} is not strict UTF-8 JSON") from exc
    _require(isinstance(value, dict), f"{label} must be an object")
    return value


def _load(path: Path) -> dict[str, Any]:
    return _strict_json(path.read_bytes(), str(path))


def _git(repo: Path, *args: str, raw: bool = False) -> bytes | str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise C257GuardError(f"git {' '.join(args)} failed: {message}")
    return result.stdout if raw else result.stdout.decode("ascii").strip()


def _git_commit(repo: Path, ref: str) -> str:
    resolved = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    _require(isinstance(resolved, str) and HEX40.fullmatch(resolved) is not None,
             f"invalid commit ref {ref}")
    return resolved


def _tree_bytes(repo: Path, commit: str, path: str) -> bytes:
    _require(not Path(path).is_absolute() and ".." not in Path(path).parts, "tree path invalid")
    value = _git(repo, "cat-file", "blob", f"{commit}:{path}", raw=True)
    assert isinstance(value, bytes)
    return value


def _content_digest(value: Mapping[str, Any]) -> str:
    return _digest({key: item for key, item in value.items() if key != "digest"})


def _resolve_c256(successor: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    rejection = successor["c256_terminal_pre_market_rejection"]
    _require(rejection["contract_commit"] == C256_COMMIT, "C256 source commit mismatch")
    _require(rejection["contract_path"] == C256_PATH, "C256 source path mismatch")
    _require(rejection["contract_git_blob_oid"] == C256_BLOB_OID, "C256 declared blob mismatch")
    _require(rejection["contract_byte_sha256"] == C256_BYTE_SHA256, "C256 source bytes mismatch")
    _require(_git_commit(repo, C256_COMMIT) == C256_COMMIT, "C256 commit did not resolve exactly")
    blob = _git(repo, "rev-parse", f"{C256_COMMIT}:{C256_PATH}")
    _require(blob == C256_BLOB_OID, "C256 tree blob OID mismatch")
    raw = _tree_bytes(repo, C256_COMMIT, C256_PATH)
    _require(hashlib.sha256(raw).hexdigest() == C256_BYTE_SHA256, "C256 blob byte hash mismatch")
    contract = _strict_json(raw, "C256 contract")
    _require(all(name in contract for name in UNCHANGED_SECTIONS), "C256 inherited section missing")
    multiplicity = contract.get("multiplicity")
    _require(isinstance(multiplicity, Mapping), "C256 multiplicity missing")
    _require(type(multiplicity.get("primary_count")) is int and multiplicity["primary_count"] == 1,
             "C256 multiplicity primary count changed")
    _require(type(multiplicity.get("candidate_familywise_alpha_ceiling")) is float
             and multiplicity["candidate_familywise_alpha_ceiling"] == 0.0001953125,
             "C256 multiplicity alpha changed")
    _require(multiplicity.get("rule") == EXACT_MULTIPLICITY_RULE, "C256 multiplicity rule changed")
    return contract


def validate_scaffold(successor: Mapping[str, Any], prep: Mapping[str, Any], *,
                      repo: Path = ROOT) -> None:
    _require(_digest(successor) == SCAFFOLD_CANONICAL_SHA256, "scaffold canonical hash mismatch")
    _require(_digest(prep) == PREP_CANONICAL_SHA256, "prep canonical hash mismatch")
    _require(successor.get("candidate_id") == 257, "candidate identity mismatch")
    _require(successor.get("candidate_key") == "funding_shock_reversal_xsec_8h", "candidate key mismatch")
    _require(successor.get("base_commit") == BASE_COMMIT, "base mismatch")
    _require(successor.get("status") == "FROZEN_SUCCESSOR_SCAFFOLD_AUTHORITY_PINS_ABSENT",
             "scaffold status mismatch")
    _require(successor.get("default_off") is True, "candidate must be default-OFF")
    _require(successor.get("evaluation_locked") is True, "evaluation must remain locked")
    _require(successor.get("paper_eligibility") == PAPER_ELIGIBILITY, "PAPER block changed")
    rejection = successor["c256_terminal_pre_market_rejection"]
    for field in ("market_rows_read", "holdout_rows_read", "outcomes_read",
                  "evaluation_identities_created"):
        _require(type(rejection.get(field)) is int and rejection[field] == 0,
                 f"C256 pre-market proof changed: {field}")
    _require(rejection.get("tuning_or_rescue_permitted") is False, "C256 rescue must be forbidden")
    inheritance = successor["economic_inheritance"]
    _require(inheritance.get("unchanged_sections") == list(UNCHANGED_SECTIONS),
             "economic inheritance incomplete")
    _require(inheritance.get("exact_rule") == EXACT_ECONOMIC_RULE, "economic exact rule changed")
    _require(type(inheritance.get("multiplicity_primary_count")) is int
             and inheritance["multiplicity_primary_count"] == 1, "multiplicity primary count changed")
    _require(type(inheritance.get("multiplicity_alpha_ceiling")) is float
             and inheritance["multiplicity_alpha_ceiling"] == 0.0001953125,
             "multiplicity alpha changed")
    _require(inheritance.get("multiplicity_rule") == (
        "Retain C256's already-frozen 0.05/256 ceiling unchanged; C257 may not use its new "
        "number to relax that gate."), "multiplicity inheritance rule changed")
    _resolve_c256(successor, repo)
    authority = successor["authority_v6"]
    _require(authority.get("plan_path") == PLAN_PATH.relative_to(ROOT).as_posix(),
             "authority plan path mismatch")
    _require(authority.get("plan_byte_sha256") == PLAN_SHA256, "authority plan bytes mismatch")
    _require(authority.get("protected_plan_commit") == PLAN_COMMIT, "authority plan commit mismatch")
    _require(authority.get("protected_authority_head") is None, "scaffold unexpectedly authority-pinned")
    _require(authority.get("required_result") == "ZERO_GUARANTEED_PERIOD_GAPS", "coverage gate mismatch")
    _require(authority.get("output_pins") == {name: None for name in OUTPUT_NAMES},
             "scaffold output pins must be null")
    _require(isinstance(authority.get("forbidden_before_pin"), list)
             and len(authority["forbidden_before_pin"]) >= 6, "authority forbidden list weakened")
    _require(isinstance(successor.get("evaluation_preconditions"), list)
             and len(successor["evaluation_preconditions"]) >= 6, "evaluation preconditions weakened")
    _require(prep.get("candidate_id") == 257 and prep.get("status") == "PREP_ONLY_FAIL_CLOSED",
             "prep identity/status mismatch")
    _require(prep.get("base_commit") == BASE_COMMIT, "prep base mismatch")
    _require(prep.get("successor_contract_path") == SUCCESSOR_PATH.relative_to(ROOT).as_posix(),
             "prep successor path mismatch")
    _require(prep.get("guard_path") == Path(__file__).resolve().relative_to(ROOT).as_posix(),
             "prep guard path mismatch")
    _require(prep.get("evaluator") is None and prep.get("adapter") is None
             and prep.get("evaluation_cli") is None, "evaluation path must not exist")
    _require(prep.get("allowed_cli") == ["--verify-scaffold"], "unexpected CLI capability")
    _require(prep.get("required_authority_outputs") == list(OUTPUT_NAMES), "prep output set mismatch")
    policy = prep.get("capability_policy")
    _require(isinstance(policy, Mapping) and set(policy) == {
        "network", "market_files", "holdout", "outcome_store", "evaluation_claim", "paper",
        "mainnet"}, "capability policy schema changed")
    _require(all(value == "DENY" for value in policy.values()) and policy["mainnet"] == "DENY",
             "all prep capabilities including mainnet must be DENY")
    reviews = prep.get("required_independent_reviews")
    _require(isinstance(reviews, list) and len(reviews) == 4, "independent-review gates weakened")


def _parse_output(name: str, raw: bytes) -> dict[str, Any]:
    if name == "manifest_v6":
        _require(len(raw) >= 10 and raw[:2] == b"\x1f\x8b", "manifest-v6 is not gzip")
        _require(raw[4:8] == b"\x00\x00\x00\x00" and not (raw[3] & 0x08),
                 "manifest-v6 gzip is not deterministic")
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as exc:
            raise C257GuardError("manifest-v6 gzip is invalid") from exc
    value = _strict_json(raw, name)
    _require(value.get("digest") == _content_digest(value), f"{name} content digest invalid")
    return value


def _verify_output_structure(documents: Mapping[str, Mapping[str, Any]],
                             byte_hashes: Mapping[str, str]) -> None:
    manifest = documents["manifest_v6"]
    _require((manifest.get("format"), manifest.get("authority_id"),
              manifest.get("cutoff_exclusive_ms")) == (
        "zb-binance-um-archive-manifest/6", "BINANCE-UM-PRECUTOFF-AUTHORITY-V6",
        1793836800000), "manifest-v6 identity/boundary mismatch")
    _require(manifest.get("cutoff_exclusive_utc") == "2026-11-05T00:00:00Z"
             and manifest.get("timezone") == "UTC", "manifest-v6 UTC cutoff mismatch")
    pit = documents["pit_v9"]
    _require((pit.get("format"), pit.get("universe_id"), pit.get("manifest_digest")) == (
        "zb-pit-universe/2", "PIT-TOP40-QV30D-V9", manifest["digest"]),
        "PIT-v9 identity/manifest binding mismatch")
    weeks = pit.get("weeks")
    start = datetime(2024, 10, 14, tzinfo=timezone.utc)
    end = datetime(2026, 11, 2, tzinfo=timezone.utc)
    expected_mondays: list[int] = []
    cursor = start
    while cursor <= end:
        expected_mondays.append(int(cursor.timestamp()*1000))
        cursor += timedelta(days=7)
    _require(isinstance(weeks, list)
             and [cell.get("monday_ms") for cell in weeks] == expected_mondays,
             "PIT-v9 weeks are not the exact frozen Monday sequence")
    for cell in weeks:
        books = cell.get("books")
        _require(isinstance(books, Mapping) and books
                 and all(isinstance(members, list) and members
                         and all(isinstance(symbol, str) and symbol for symbol in members)
                         and len(members) == len(set(members)) for members in books.values()),
                 "PIT-v9 weekly membership is empty/malformed")
    filter_v4 = documents["filter_v4"]
    _require((filter_v4.get("authority"), filter_v4.get("version"), filter_v4.get("status")) == (
        "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", 4, "FROZEN"),
        "filter-v4 identity/status mismatch")
    _require(filter_v4.get("pit_universe_path") == OUTPUT_PATHS["pit_v9"]
             and filter_v4.get("pit_universe_sha256") == byte_hashes["pit_v9"]
             and filter_v4.get("pit_universe_digest") == pit["digest"],
             "filter-v4 PIT binding mismatch")
    _require(filter_v4.get("fold_bindings") == {
        "development_eligible_segment": "2024-12-13T04:44:48Z",
        "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
        "wf3": "2026-02-17T04:13:28Z", "sealed_holdout": "2026-02-17T04:13:28Z",
    }, "filter-v4 fold bindings changed")
    coverage = documents["coverage_v4"]
    _require((coverage.get("format"), coverage.get("authority_version"),
              coverage.get("required_result")) == (
        "zb-strategy-causal-pack-01-authority-coverage-preflight/1", 4,
        "ZERO_GUARANTEED_PERIOD_GAPS"), "coverage-v4 identity/result mismatch")
    _require(coverage.get("manifest_digest") == manifest["digest"]
             and coverage.get("pit_universe_digest") == pit["digest"]
             and coverage.get("filter_authority_digest") == filter_v4["digest"],
             "coverage-v4 authority binding mismatch")
    by_stream = coverage.get("guaranteed_period_gaps_by_stream")
    required_streams = {"klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null"}
    _require(type(coverage.get("guaranteed_period_gaps")) is int
             and coverage["guaranteed_period_gaps"] == 0 and coverage.get("gaps") == []
             and isinstance(by_stream, Mapping) and set(by_stream) == required_streams
             and all(type(value) is int and value == 0 for value in by_stream.values()),
             "coverage-v4 has guaranteed-period gaps")
    _require(coverage.get("cutoff_exclusive_ms") == 1793836800000
             and coverage.get("cutoff_exclusive_utc") == "2026-11-05T00:00:00Z"
             and coverage.get("timezone") == "UTC",
             "coverage-v4 cutoff/timezone mismatch")


def verify_pin_amendment(scaffold: Mapping[str, Any], amended: Mapping[str, Any], *,
                         repo: Path = ROOT,
                         protected_ref: str = "refs/remotes/origin/master") -> None:
    # The protected checkout legitimately contains the mechanical amendment;
    # authenticate the supplied scaffold semantically and resolve its exact
    # frozen C256 object rather than requiring the working-tree file to remain null-pinned.
    validate_scaffold(scaffold, _load(PREP_PATH), repo=repo)
    expected = copy.deepcopy(scaffold)
    authority = amended.get("authority_v6")
    _require(isinstance(authority, Mapping), "authority-v6 missing")
    head = authority.get("protected_authority_head")
    _require(isinstance(head, str) and HEX40.fullmatch(head) is not None,
             "protected authority head invalid")
    _require(_git_commit(repo, head) == head, "authority head does not resolve exactly")
    _require(_git_commit(repo, protected_ref) == head, "authority head is not the protected ref")
    ancestry = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                               BASE_COMMIT, head], capture_output=True, check=False)
    _require(ancestry.returncode == 0, "authority head does not descend from frozen base")
    plan_raw = _tree_bytes(repo, head, PLAN_PATH.relative_to(ROOT).as_posix())
    _require(hashlib.sha256(plan_raw).hexdigest() == PLAN_SHA256,
             "protected authority plan bytes changed")
    plan = _strict_json(plan_raw, "authority-v6 plan")
    _require((plan.get("authority_id"), plan.get("version"), plan.get("status")) == (
        "BINANCE-UM-PRECUTOFF-AUTHORITY-V6", 6, "FROZEN_METADATA_ONLY_NOT_ACQUIRED"),
        "authority-v6 plan identity/status mismatch")
    _require({name: plan.get("outputs", {}).get(name, {}).get("path") for name in OUTPUT_NAMES}
             == OUTPUT_PATHS, "authority-v6 plan output paths changed")
    pins = authority.get("output_pins")
    _require(isinstance(pins, Mapping) and set(pins) == set(OUTPUT_NAMES), "output pin set mismatch")
    documents: dict[str, Mapping[str, Any]] = {}
    byte_hashes: dict[str, str] = {}
    for name in OUTPUT_NAMES:
        pin = pins[name]
        _require(isinstance(pin, Mapping) and set(pin) == PIN_FIELDS, f"{name} pin fields invalid")
        _require(pin.get("path") == OUTPUT_PATHS[name], f"{name} path mismatch")
        raw = _tree_bytes(repo, head, OUTPUT_PATHS[name])
        actual_byte_hash = hashlib.sha256(raw).hexdigest()
        _require(pin.get("byte_sha256") == actual_byte_hash, f"{name} byte hash mismatch")
        document = _parse_output(name, raw)
        _require(pin.get("content_digest") == document["digest"],
                 f"{name} content digest mismatch")
        documents[name] = document
        byte_hashes[name] = actual_byte_hash
    _verify_output_structure(documents, byte_hashes)
    expected["status"] = "FROZEN_AUTHORITY_PINNED_NOT_EVALUATED"
    expected["authority_v6"]["protected_authority_head"] = head
    expected["authority_v6"]["output_pins"] = copy.deepcopy(pins)
    _require(amended == expected, "pin amendment changed a non-authority field")


def verify_scaffold_files() -> dict[str, str]:
    successor_raw, prep_raw = SUCCESSOR_PATH.read_bytes(), PREP_PATH.read_bytes()
    _require(hashlib.sha256(successor_raw).hexdigest() == SCAFFOLD_BYTE_SHA256,
             "scaffold file byte hash mismatch")
    _require(hashlib.sha256(prep_raw).hexdigest() == PREP_BYTE_SHA256,
             "prep file byte hash mismatch")
    successor, prep = _strict_json(successor_raw, "C257 scaffold"), _strict_json(prep_raw, "C257 prep")
    validate_scaffold(successor, prep)
    _require(hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest() == PLAN_SHA256,
             "authority plan file bytes mismatch")
    return {"successor_sha256": SCAFFOLD_BYTE_SHA256, "prep_sha256": PREP_BYTE_SHA256,
            "status": "LOCKED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the metadata-only C257 successor scaffold")
    parser.add_argument("--verify-scaffold", action="store_true")
    args = parser.parse_args(argv)
    if not args.verify_scaffold:
        parser.error("only --verify-scaffold is allowed; evaluation is not implemented")
    print(json.dumps(verify_scaffold_files(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
