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

    required_true = [
        "stable_one_way_aliases",
        "mapping_excluded_from_request",
        "mapping_excluded_from_logs",
        "mapping_excluded_from_score_report",
        "mapping_excluded_from_result_packet",
        "remove_strategy_and_family_names",
        "remove_symbols",
        "remove_absolute_timestamps",
        "remove_event_and_calendar_labels",
        "retain_preregistered_numeric_geometry",
        "deterministic_seeded_order",
    ]
    for key in required_true:
        assert blinding[key] is True, key
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


def test_llm_leak_contract_freezes_alias_controls_and_scoring():
    contract = _contract()
    blinding = contract["blinding"]
    pit = contract["point_in_time"]
    transforms = contract["control_transforms"]
    scoring = contract["scoring"]
    model = contract["model_request"]

    assert blinding["alias_key_bytes"] == 32
    assert blinding["alias_key_public_value"] is False
    assert blinding["alias_format"] == "C_[0-9A-F]{12}"
    assert blinding["allowed_relative_time_fields"] == [
        "window_index",
        "observation_age_bars",
        "bars_to_cutoff",
        "horizon_bars",
    ]
    assert pit["admission_rule"] == "event_at<=available_at<=cutoff"
    assert transforms["permutation_algorithm"] == "fisher_yates"
    assert transforms["preserve_counts_and_marginals"] is True
    assert transforms["deterministic_baseline_reads_candidate_metrics"] is False
    assert scoring["fixed_request_repeats"] == 5
    assert scoring["valid_alias_only_repeats_required"] == 5
    assert scoring["future_taint_detection_fraction_required"] == "1.0"
    assert scoring["forbidden_identity_matches_allowed"] == 0
    assert model == {
        "model": "claude-sonnet-5-5",
        "temperature": "0",
        "max_output_tokens": 1200,
        "identical_across_control_arms": True,
    }
