from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.research.c257_successor_guard import (
    C257GuardError,
    OUTPUT_NAMES,
    OUTPUT_PATHS,
    PREP_PATH,
    SUCCESSOR_PATH,
    validate_scaffold,
    verify_pin_amendment,
    verify_scaffold_files,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _valid_amendment(scaffold: dict) -> dict:
    amended = copy.deepcopy(scaffold)
    amended["status"] = "FROZEN_AUTHORITY_PINNED_NOT_EVALUATED"
    amended["authority_v6"]["protected_authority_head"] = "a" * 40
    amended["authority_v6"]["output_pins"] = {
        name: {"path": OUTPUT_PATHS[name], "byte_sha256": "b" * 64, "content_digest": "c" * 64}
        for name in OUTPUT_NAMES
    }
    return amended


def test_scaffold_is_locked_and_metadata_only() -> None:
    result = verify_scaffold_files()
    assert result["status"] == "LOCKED"
    assert len(result["successor_sha256"]) == 64
    assert len(result["prep_sha256"]) == 64


def test_scaffold_binds_exact_c256_contract_and_zero_reads() -> None:
    successor = _load(SUCCESSOR_PATH)
    rejection = successor["c256_terminal_pre_market_rejection"]
    assert rejection["contract_commit"] == "6cc7148a453be054f5c02ecf1eecc8369fcfc533"
    assert rejection["contract_byte_sha256"] == "88294e16a44c4e2219284abeb26f4085123b9fd9de0c5120d0a19d5b84a94b54"
    assert {rejection[key] for key in ("market_rows_read", "holdout_rows_read", "outcomes_read", "evaluation_identities_created")} == {0}


@pytest.mark.parametrize("field", ["market_rows_read", "holdout_rows_read", "outcomes_read", "evaluation_identities_created"])
def test_scaffold_rejects_any_claim_that_c256_was_read(field: str) -> None:
    successor, prep = _load(SUCCESSOR_PATH), _load(PREP_PATH)
    successor["c256_terminal_pre_market_rejection"][field] = 1
    with pytest.raises(C257GuardError, match="pre-market proof changed"):
        validate_scaffold(successor, prep)


def test_scaffold_rejects_evaluator_or_adapter_binding() -> None:
    successor, prep = _load(SUCCESSOR_PATH), _load(PREP_PATH)
    prep["evaluator"] = "tools/research/forbidden.py"
    with pytest.raises(C257GuardError, match="evaluation path must not exist"):
        validate_scaffold(successor, prep)


def test_scaffold_rejects_any_change_to_inherited_section_set() -> None:
    successor, prep = _load(SUCCESSOR_PATH), _load(PREP_PATH)
    successor["economic_inheritance"]["unchanged_sections"].remove("costs")
    with pytest.raises(C257GuardError, match="economic inheritance incomplete"):
        validate_scaffold(successor, prep)


def test_scaffold_rejects_early_authority_pin() -> None:
    successor, prep = _load(SUCCESSOR_PATH), _load(PREP_PATH)
    successor["authority_v6"]["protected_authority_head"] = "a" * 40
    with pytest.raises(C257GuardError, match="unexpectedly authority-pinned"):
        validate_scaffold(successor, prep)


def test_exact_four_pin_amendment_is_accepted() -> None:
    scaffold = _load(SUCCESSOR_PATH)
    verify_pin_amendment(scaffold, _valid_amendment(scaffold))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["economic_inheritance"].__setitem__("exact_rule", "tune"), "non-authority"),
        (lambda value: value.__setitem__("default_off", False), "non-authority"),
        (lambda value: value.__setitem__("paper_eligibility", "ALLOW"), "non-authority"),
        (lambda value: value["authority_v6"]["output_pins"].pop("coverage_v4"), "output pin set"),
        (lambda value: value["authority_v6"]["output_pins"]["manifest_v6"].__setitem__("path", "wrong"), "path mismatch"),
        (lambda value: value["authority_v6"]["output_pins"]["pit_v9"].__setitem__("byte_sha256", "short"), "byte hash invalid"),
    ],
)
def test_hostile_pin_amendment_fails_closed(mutation, message: str) -> None:
    scaffold = _load(SUCCESSOR_PATH)
    amended = _valid_amendment(scaffold)
    mutation(amended)
    with pytest.raises(C257GuardError, match=message):
        verify_pin_amendment(scaffold, amended)
