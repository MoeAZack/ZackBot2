import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "research_evidence/prereg/cointegrated_residual_reversion_c255_stagea.v1.json"


def load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interval(raw):
    start, end = raw.split("/", 1)
    return datetime.fromisoformat(start.replace("Z", "+00:00")), datetime.fromisoformat(
        end.replace("Z", "+00:00")
    )


def test_contract_is_frozen_research_only_and_not_activated():
    contract = load_contract()
    assert contract["candidate_id"] == 255
    assert contract["base_commit"] == "80892fb1337a954720ab16e00e0608f72ae9f894"
    assert contract["status"] == "FROZEN_NOT_EVALUATED_RESEARCH_ONLY"
    assert contract["default_off"] is True
    assert contract["paper_eligibility"] == "BLOCK_UNTIL_INDEPENDENT_OOS_PASS"
    assert "outcome access" in contract["deferred"]
    assert "mainnet" in contract["deferred"]


def test_contract_binds_exact_point_in_time_authorities():
    contract = load_contract()["data_authority"]
    universe = ROOT / contract["universe_path"]
    manifest = ROOT / contract["archive_manifest_path"]
    filters = ROOT / contract["exchange_filter_authority_path"]
    assert sha256(universe) == contract["universe_sha256"]
    assert sha256(manifest) == contract["archive_manifest_sha256"]
    assert sha256(filters) == contract["exchange_filter_authority_sha256"]
    universe_doc = json.loads(universe.read_text(encoding="utf-8"))
    assert universe_doc["digest"] == contract["universe_digest"]
    filter_doc = json.loads(filters.read_text(encoding="utf-8"))
    assert filter_doc["pit_universe_path"] == contract["universe_path"]
    assert filter_doc["pit_universe_sha256"] == contract["universe_sha256"]
    assert filter_doc["pit_universe_digest"] == contract["universe_digest"]
    assert "fail closed" in contract["exchange_filter_requirement"].lower()


def test_walk_forward_chronology_is_disjoint_and_holdout_stays_sealed():
    walk = load_contract()["walk_forward"]
    dev = interval(walk["development_test_range"])
    folds = [interval(value) for value in walk["forward_folds"]]
    holdout = interval(walk["sealed_holdout"])
    assert dev[1] == folds[0][0]
    assert all(left[1] == right[0] for left, right in zip(folds, folds[1:]))
    assert folds[-1][1] == holdout[0]
    assert all(start < end for start, end in [dev, *folds, holdout])
    assert walk["training_end"] == "strictly_before_selection_instant"
    assert walk["holdout_policy"] == "SEALED_AND_UNAVAILABLE_TO_STAGE_A"


def test_cost_arithmetic_charges_all_four_leg_fills_and_stress():
    costs = load_contract()["costs"]
    fills = costs["base_pair_round_trip_fills"]
    assert fills == 4
    assert costs["reference_base_pair_round_trip_bps_of_initial_gross"] == 2 * (
        costs["taker_fee_bps_per_fill_per_leg"] + costs["adverse_slippage_bps_per_fill_per_leg"])
    assert costs["reference_stress_pair_round_trip_bps_of_initial_gross"] == 2 * (
        costs["taker_fee_bps_per_fill_per_leg"]
        + costs["stress_adverse_slippage_bps_per_fill_per_leg"])
    assert "actual_fill_notional_dollars" in costs["cost_denominator"]
    assert "both legs" in costs["funding"]


def test_selection_and_signal_rules_are_frozen_and_causal():
    contract = load_contract()
    selection = contract["pair_selection"]
    signals = contract["signals"]
    assert contract["walk_forward"]["training_window_days"] == 180
    assert "each unordered eligible symbol pair exactly once" in selection["pair_enumeration"]
    assert "Never test or select the reverse orientation" in selection["pair_enumeration"]
    assert "regression='n'" in selection["residual_stationarity"]
    assert "maxlag=24" in selection["residual_stationarity"]
    assert "autolag='BIC'" in selection["residual_stationarity"]
    assert selection["frozen_monthly_model"].endswith("test month.")
    assert "Holm-Bonferroni" in selection["residual_stationarity"]
    assert "next 1h open" in signals["long_spread"]
    assert "next 1h open" in signals["short_spread"]
    assert "updating model statistics with test-month observations" in signals["forbidden"]
    assert "same-bar close fills" in signals["forbidden"]


def test_gates_require_economic_breadth_stress_and_both_directions():
    gates = load_contract()["gates"]
    joined = "\n".join(gates["forward_final_pass_all_required"])
    assert "at least 200 closed pair trades" in joined
    assert "12 symbols" in joined
    assert "2 of 3 forward folds" in joined
    assert "long-spread and short-spread" in joined
    assert "stress-cost aggregate mean net R>0" in joined
    assert "p-value<=0.05/255" in joined
    assert gates["undefined_statistics"] == "FAIL_CLOSED"


def test_bootstrap_atomicity_alignment_and_portfolio_rules_are_exact():
    contract = load_contract()
    bootstrap = contract["bootstrap_specification"]
    assert bootstrap["expected_block_length_attempts"] == 24
    assert bootstrap["resamples"] == 10000
    assert bootstrap["seed"] == 25520261106
    assert "atomicity failures" in bootstrap["ordered_observations"]
    assert "inner-join only exact completed 1h" in contract["data_authority"]["pair_bar_alignment"]
    assert "monday_ms<=selection_ms" in contract["data_authority"]["membership_cell_rule"]
    assert "next available 1h open" in contract["signals"]["time_exit_execution"]
    assert "Future exit prices/notionals may never qualify" in contract["signals"]["cost_floor"]
    assert "Wilder RMA" in contract["signals"]["per_leg_atr"]
    assert "alpha=1/24" in contract["signals"]["per_leg_atr"]
    assert "counts as one closed pair attempt" in contract["position_and_portfolio"]["atomicity"]
    assert contract["position_and_portfolio"]["max_aggregate_pair_gross_leverage"] == 1.5


def test_multiplicity_and_falsification_forbid_rescue():
    contract = load_contract()
    multiplicity = contract["multiplicity"]
    assert multiplicity["numbered_candidate_family"] == 255
    assert multiplicity["candidate_familywise_alpha_ceiling"] == pytest.approx(0.05 / 255)
    assert any("do not tune" in rule for rule in contract["falsification"])
    assert any("rejection is final" in rule.lower() for rule in contract["falsification"])
