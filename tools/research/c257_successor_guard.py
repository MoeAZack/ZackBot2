from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SUCCESSOR_PATH = ROOT / "research_evidence/prereg/funding_shock_reversal_xsec_c257_successor.v1.json"
PREP_PATH = ROOT / "contracts/strategy_causal_pack_01/c257_successor_adapter_prep.v1.json"
PLAN_PATH = ROOT / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
OUTPUT_NAMES = ("manifest_v6", "pit_v9", "filter_v4", "coverage_v4")
BASE_COMMIT = "64adf3a41d7a521d2af480adc7b2f23a8fa6a0c3"
PLAN_SHA256 = "30080d16132348fabceb91bffe110f43ee33824c4496622f736ee6a6c4d7af60"
C256_CONTRACT_SHA256 = "88294e16a44c4e2219284abeb26f4085123b9fd9de0c5120d0a19d5b84a94b54"
UNCHANGED_SECTIONS = (
    "hypothesis",
    "economic_distinction",
    "walk_forward",
    "funding_surprise",
    "selection_and_pairing",
    "execution",
    "position_and_portfolio",
    "costs",
    "gates",
    "bootstrap_specification",
    "expected_test_count",
    "trial_budget",
    "multiplicity",
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


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise C257GuardError(f"not an object: {path}")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise C257GuardError(message)


def validate_scaffold(successor: Mapping[str, Any], prep: Mapping[str, Any]) -> None:
    _require(successor.get("candidate_id") == 257, "candidate identity mismatch")
    _require(successor.get("candidate_key") == "funding_shock_reversal_xsec_8h", "candidate key mismatch")
    _require(successor.get("base_commit") == BASE_COMMIT, "base mismatch")
    _require(successor.get("default_off") is True, "candidate must be default-OFF")
    _require(successor.get("evaluation_locked") is True, "evaluation must remain locked")
    _require("BLOCK" in str(successor.get("paper_eligibility")), "PAPER must remain blocked")

    rejection = successor.get("c256_terminal_pre_market_rejection", {})
    _require(rejection.get("contract_commit") == "6cc7148a453be054f5c02ecf1eecc8369fcfc533", "C256 source commit mismatch")
    _require(rejection.get("contract_byte_sha256") == C256_CONTRACT_SHA256, "C256 source bytes mismatch")
    for field in ("market_rows_read", "holdout_rows_read", "outcomes_read", "evaluation_identities_created"):
        _require(rejection.get(field) == 0, f"C256 pre-market proof changed: {field}")
    _require(rejection.get("tuning_or_rescue_permitted") is False, "C256 rescue must be forbidden")

    inheritance = successor.get("economic_inheritance", {})
    _require(inheritance.get("unchanged_sections") == list(UNCHANGED_SECTIONS), "economic inheritance incomplete")
    _require("0.05/256" in str(inheritance.get("multiplicity_rule")), "multiplicity gate changed")

    authority = successor.get("authority_v6", {})
    _require(authority.get("plan_path") == str(PLAN_PATH.relative_to(ROOT)).replace("\\", "/"), "authority plan path mismatch")
    _require(authority.get("plan_byte_sha256") == PLAN_SHA256, "authority plan bytes mismatch")
    _require(authority.get("protected_plan_commit") == BASE_COMMIT, "authority plan commit mismatch")
    _require(authority.get("protected_authority_head") is None, "scaffold unexpectedly authority-pinned")
    _require(authority.get("required_result") == "ZERO_GUARANTEED_PERIOD_GAPS", "coverage gate mismatch")
    _require(authority.get("output_pins") == {name: None for name in OUTPUT_NAMES}, "scaffold output pins must be null")

    _require(prep.get("candidate_id") == 257, "prep candidate mismatch")
    _require(prep.get("status") == "PREP_ONLY_FAIL_CLOSED", "prep status mismatch")
    _require(prep.get("base_commit") == BASE_COMMIT, "prep base mismatch")
    _require(prep.get("successor_contract_path") == str(SUCCESSOR_PATH.relative_to(ROOT)).replace("\\", "/"), "prep successor path mismatch")
    _require(prep.get("guard_path") == str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"), "prep guard path mismatch")
    _require(prep.get("evaluator") is None and prep.get("adapter") is None and prep.get("evaluation_cli") is None, "evaluation path must not exist")
    _require(prep.get("allowed_cli") == ["--verify-scaffold"], "unexpected CLI capability")
    _require(prep.get("required_authority_outputs") == list(OUTPUT_NAMES), "prep output set mismatch")
    _require(set(prep.get("capability_policy", {}).values()) == {"DENY"}, "all prep capabilities must be denied")


def verify_pin_amendment(scaffold: Mapping[str, Any], amended: Mapping[str, Any]) -> None:
    expected = copy.deepcopy(scaffold)
    authority = amended.get("authority_v6")
    _require(isinstance(authority, Mapping), "authority-v6 missing")
    head = authority.get("protected_authority_head")
    _require(isinstance(head, str) and HEX40.fullmatch(head) is not None, "protected authority head invalid")
    pins = authority.get("output_pins")
    _require(isinstance(pins, Mapping) and set(pins) == set(OUTPUT_NAMES), "output pin set mismatch")
    for name in OUTPUT_NAMES:
        pin = pins[name]
        _require(isinstance(pin, Mapping) and set(pin) == PIN_FIELDS, f"{name} pin fields invalid")
        _require(pin.get("path") == OUTPUT_PATHS[name], f"{name} path mismatch")
        _require(isinstance(pin.get("byte_sha256"), str) and HEX64.fullmatch(pin["byte_sha256"]) is not None, f"{name} byte hash invalid")
        _require(isinstance(pin.get("content_digest"), str) and HEX64.fullmatch(pin["content_digest"]) is not None, f"{name} content digest invalid")

    expected["status"] = "FROZEN_AUTHORITY_PINNED_NOT_EVALUATED"
    expected["authority_v6"]["protected_authority_head"] = head
    expected["authority_v6"]["output_pins"] = copy.deepcopy(pins)
    _require(amended == expected, "pin amendment changed a non-authority field")


def verify_scaffold_files() -> dict[str, str]:
    successor = _load(SUCCESSOR_PATH)
    prep = _load(PREP_PATH)
    validate_scaffold(successor, prep)
    _require(hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest() == PLAN_SHA256, "authority plan file bytes mismatch")
    return {
        "successor_sha256": hashlib.sha256(SUCCESSOR_PATH.read_bytes()).hexdigest(),
        "prep_sha256": hashlib.sha256(PREP_PATH.read_bytes()).hexdigest(),
        "status": "LOCKED",
    }


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
