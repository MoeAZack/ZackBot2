import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "llm_leak_01" / "llm_leak_01_contract.v1.json"


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_llm_leak_contract_has_no_trading_or_alpha_authority():
    contract = _contract()

    assert contract["status"] == "DRAFT"
    assert all(value is False for value in contract["authority"].values())
    boundary = contract["implementation_boundary"]
    assert boundary["new_files_only"] is True
    assert boundary["shadow_analyst_edited"] is False
    assert boundary["runtime_files_edited"] is False
    assert boundary["remote_calls_in_contract_tests"] is False


def test_llm_leak_contract_blinds_identity_and_freezes_cutoff():
    contract = _contract()
    blinding = contract["blinding"]
    pit = contract["point_in_time"]

    for key, value in blinding.items():
        assert value is True, key
    assert pit["append_after_cutoff_request_bytes_unchanged"] is True
    assert pit["pre_cutoff_mutation_changes_digest"] is True
    assert pit["ambiguous_availability_fails_closed"] is True


def test_llm_leak_contract_requires_negative_and_positive_controls():
    contract = _contract()
    arms = set(contract["control_arms"])

    assert {
        "null_no_outcomes",
        "permuted_labels",
        "permuted_candidates",
        "equal_information_deterministic_baseline",
        "future_tainted_positive_control",
        "fixed_request_repeats",
    } <= arms
    assert contract["scoring"]["profitability_is_acceptance_gate"] is False
    assert contract["scoring"]["ranking_outcome_is_acceptance_gate"] is False
    assert contract["scoring"]["future_tainted_control_must_be_detected"] is True
    assert contract["result_packet"]["candidate_mapping_included"] is False
