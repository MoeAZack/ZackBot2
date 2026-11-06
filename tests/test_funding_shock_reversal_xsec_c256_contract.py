import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "research_evidence/prereg/funding_shock_reversal_xsec_c256_stagea.v1.json"
BASE = "1437dc48e70cb108abe10df74750d29961f01ad7"


def load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interval(raw):
    start, end = raw.split("/", 1)
    return datetime.fromisoformat(start.replace("Z", "+00:00")), datetime.fromisoformat(
        end.replace("Z", "+00:00")
    )


def validate_immutable_contract(contract):
    assert contract["candidate_id"] == 256
    assert contract["candidate_key"] == "funding_shock_reversal_xsec_8h"
    assert contract["base_commit"] == BASE
    assert contract["status"] == "FROZEN_NOT_EVALUATED_RESEARCH_ONLY"
    assert contract["default_off"] is True
    assert contract["evaluation_locked"] is True
    assert contract["paper_eligibility"] == "BLOCK_UNTIL_INDEPENDENT_OOS_PASS"
    assert contract["data_authority"]["universe_sha256"] == (
        "3fc2c1696a4f9ac753a140394e6ca6ba723a9364046dbb94bcb6310c48b68d41"
    )
    assert contract["data_authority"]["archive_manifest_sha256"] == (
        "3de00d9a9961d04938e71cd58fb042c28bfeea48519d91d8fbac2d12da99987a"
    )
    assert contract["data_authority"]["exchange_filter_authority_sha256"] == (
        "e11b42efe63accc6a4546ac29507356fb53cdcc88c403aec031ffa48b27f1ba9"
    )
    assert contract["walk_forward"]["holdout_policy"] == "SEALED_AND_UNAVAILABLE_TO_STAGE_A"
    assert contract["funding_surprise"]["thresholds"] == {
        "long_tail": "z<=-2.0",
        "short_tail": "z>=+2.0",
        "rationale": (
            "A fixed robust two-standard-deviation shock threshold is an ex-ante crowding definition, "
            "not selected from C256 or EDGE05 outcomes."
        ),
    }
    assert contract["costs"]["taker_fee_bps_per_fill_per_leg"] == 5.0
    assert contract["costs"]["adverse_slippage_bps_per_fill_per_leg"] == 2.0
    assert contract["costs"]["stress_adverse_slippage_bps_per_fill_per_leg"] == 10.0
    assert contract["gates"]["drawdown_disclosure_tiers"]["reject"].startswith("max drawdown>60R")
    assert contract["multiplicity"]["candidate_familywise_alpha_ceiling"] == pytest.approx(0.05 / 256)
    assert contract["multiplicity"]["primary_count"] == 1
    assert "cannot rescue" in contract["trial_budget"]["rescue_rule"]
    authority = contract["data_authority"]
    requirement = authority["exchange_filter_requirement"]
    assert "strictly before" in requirement
    assert "freeze it for the whole" in requirement
    assert "Current exchangeInfo" in requirement
    assert authority["exchange_filter_fold_bindings"] == {
        "development_pre_authority": None,
        "development_eligible_segment": "2024-12-13T04:44:48Z",
        "wf1": "2024-12-13T04:44:48Z",
        "wf2": "2024-12-13T04:44:48Z",
        "wf3": "2026-02-17T04:13:28Z",
    }
    selection = contract["selection_and_pairing"]
    assert "skip pair i" in selection["infeasible_pair_rule"]
    assert "never re-pair" in selection["infeasible_pair_rule"]
    execution = contract["execution"]
    assert "open_time>=T+300 seconds" in execution["entry"]
    assert "Same-hour T open" in execution["entry"]
    assert "both legs trigger" in execution["dual_stop_sequence"]
    assert "neither fill changes the other's" in execution["dual_stop_sequence"]
    assert "following available 1h open" in execution["atomicity"]
    assert "never the same open" in execution["atomicity"]
    assert "A stop touched during the entry hour fills first" in execution["atomicity"]
    assert "reference_price=min(stop_price,bar_open)" in execution["protective_stop"]
    assert "reference_price=max(stop_price,bar_open)" in execution["protective_stop"]
    assert "reference prices never embed slippage" in execution["protective_stop"]
    assert "fill_timestamp=bar.open_time" in execution["protective_stop"]
    assert "processed before any funding settlement at that same timestamp" in execution["protective_stop"]
    assert "fill_timestamp=bar.close_time" in execution["protective_stop"]
    assert "nominal_time<=that close timestamp is processed first" in execution["protective_stop"]
    assert "fill timestamp is that bar's open_time" in execution["fixed_exit"]
    assert "before any funding event with the same nominal timestamp" in execution["fixed_exit"]
    assert "Slippage is not embedded here" in execution["atomicity"]
    position = contract["position_and_portfolio"]
    assert position["max_concurrent_pairs"] == 3
    assert position["max_open_risk_r"] == 3.0
    assert "aggregate_modeled_pair_loss" in position["risk_unit"]
    assert "simultaneous two-leg stops" in position["risk_unit"]
    assert "aggregate_modeled_pair_loss<=1R" in position["risk_unit"]
    assert "Sum pair risks" in position["risk_aggregation"]
    costs = contract["costs"]
    ledger = costs["fill_ledger_and_pnl"]
    assert "raw reference_price" in ledger
    assert "Movement P&L is computed only from raw entry/exit reference_price" in ledger
    assert "slippage_dollars=abs(quantity)*abs(execution_price-reference_price)" in ledger
    assert "subtracted exactly once" in ledger
    assert "must never replace reference_price in movement P&L" in ledger
    assert "Total net P&L equals raw-reference movement P&L minus explicit slippage_dollars" in ledger
    assert "Sum explicit fee_dollars and slippage_dollars exactly once" in costs["cost_denominator"]
    assert "Never use execution-price movement P&L" in costs["cost_denominator"]
    assert "funding_pnl=-side_sign*abs(actual_qty)*causal_mark_price*funding_rate" in costs["funding"]
    assert "strictly before nominal_time" in costs["funding"]
    assert "nominal_time==entry_time is INCLUDED" in costs["funding"]
    assert "nominal_time==exit_fill_timestamp is EXCLUDED" in costs["funding"]
    assert "entry_time<=nominal_time<=exit_fill_timestamp" in costs["funding"]
    assert "entry_time<=nominal_time<exit_fill_timestamp" in costs["funding"]
    assert "no funding debit or credit may be selectively omitted" in costs["funding"]
    assert "if funding_pnl<0 multiply its magnitude by 1.5" in costs["stress"]
    assert "if funding_pnl>0 divide it by 1.5" in costs["stress"]
    impact = costs["market_impact"]
    assert "close_time is strictly before the proposed fill timestamp" in impact
    assert "never use the fill bar, a still-open/current bar" in impact
    assert "Missing, non-finite or <=0" in impact
    assert "vetoes a proposed entry pair before submission" in impact
    assert "INCOMPLETE_IMPACT_AUTHORITY" in impact
    assert "gap stops at bar.open_time and non-gap range stops at bar.close_time" in impact
    assert "base_or_stress_adverse_slippage_bps*max(1,impact_ratio/0.001)" in impact
    assert "INCOMPLETE_IMPACT_LIMIT" in impact
    assert "reject C256" in impact
    assert "over-cap fill cannot fall through" in impact
    expected = contract["expected_test_count"]
    assert "sorted union" in expected["actual_schedule_formula"]
    assert "do not assume three events per day" in expected["actual_schedule_formula"]
    assert expected["eight_hour_reference_only"]["label"].startswith("non-gating reference")
    assert expected["absolute_24_per_day_upper_bounds"] == {
        "development_filter_eligible_event_hours": 2592,
        "development_pairs_at_three_per_event": 7776,
        "development_leg_round_trips": 15552,
        "forward_event_hours": 13080,
        "forward_pairs_at_three_per_event": 39240,
        "forward_leg_round_trips": 78480,
    }


def test_contract_is_locked_default_off_and_allows_only_one_mechanical_pin_refresh():
    contract = load_contract()
    validate_immutable_contract(contract)
    assert "exactly one mechanical refresh" in contract["pin_refresh_policy"]
    assert "outcome or identity access" in contract["deferred"]
    assert "sealed holdout" in contract["deferred"]
    assert "mainnet" in contract["deferred"]


def test_exact_point_in_time_authorities_are_byte_bound_and_mutually_bound():
    authority = load_contract()["data_authority"]
    universe = ROOT / authority["universe_path"]
    manifest = ROOT / authority["archive_manifest_path"]
    filters = ROOT / authority["exchange_filter_authority_path"]
    assert sha256(universe) == authority["universe_sha256"]
    assert sha256(manifest) == authority["archive_manifest_sha256"]
    assert sha256(filters) == authority["exchange_filter_authority_sha256"]
    universe_doc = json.loads(universe.read_text(encoding="utf-8"))
    filter_doc = json.loads(filters.read_text(encoding="utf-8"))
    assert universe_doc["digest"] == authority["universe_digest"]
    assert filter_doc["pit_universe_path"] == authority["universe_path"]
    assert filter_doc["pit_universe_sha256"] == authority["universe_sha256"]
    assert filter_doc["pit_universe_digest"] == authority["universe_digest"]
    assert "monday_ms<=selection_ms" in authority["membership_cell_rule"]
    assert "never forward-fill" in authority["availability_and_alignment"]


def test_chronology_is_contiguous_and_holdout_stays_sealed():
    walk = load_contract()["walk_forward"]
    dev = interval(walk["development_test_range"])
    folds = [interval(value) for value in walk["forward_folds"]]
    holdout = interval(walk["sealed_holdout"])
    assert dev[1] == folds[0][0]
    assert all(left[1] == right[0] for left, right in zip(folds, folds[1:]))
    assert folds[-1][1] == holdout[0]
    assert all(start < end for start, end in [dev, *folds, holdout])
    assert "No position" in walk["boundary_policy"]
    assert "can only reject C256" in walk["parameter_policy"]
    assert "tuned or rescued" in walk["parameter_policy"]
    eligible = interval(walk["development_filter_eligible_segment"])
    assert eligible == (
        datetime.fromisoformat("2024-12-14T00:00:00+00:00"),
        datetime.fromisoformat("2025-04-01T00:00:00+00:00"),
    )
    assert (eligible[1] - eligible[0]).days == 108
    assert "FILTER_AUTHORITY_UNAVAILABLE" in walk["development_pre_authority_policy"]
    assert "before any market/outcome read" in walk["development_pre_authority_policy"]
    assert "neither contribute P&L nor reject C256" in walk["development_pre_authority_policy"]


def test_funding_shock_is_strictly_prior_robust_and_event_causal():
    contract = load_contract()
    signal = contract["funding_surprise"]
    selection = contract["selection_and_pairing"]
    assert "T+300 seconds" in signal["decision_clock"]
    assert "at least 12" in signal["decision_clock"]
    assert "strictly before T" in signal["history"]
    assert "exactly the 30" in signal["history"]
    assert "1.4826*mad" in signal["robust_center"]
    assert signal["score"].startswith("z=(current_r8-m)/scale")
    assert selection["long_order"].startswith("ascending z")
    assert selection["short_order"].startswith("descending z")
    assert "min(long_count, short_count, 3)" in selection["paired_primary"]
    assert "never traded" in selection["paired_primary"]
    assert "No beta" in selection["weights"]
    assert "skip pair i" in selection["infeasible_pair_rule"]
    assert "never re-pair" in selection["infeasible_pair_rule"]


def test_execution_forbids_same_bar_fills_and_preserves_pair_protection():
    execution = load_contract()["execution"]
    assert "open_time>=T+300 seconds" in execution["entry"]
    assert "Same-hour T open" in execution["entry"]
    assert "at least eight hours after actual entry" in execution["fixed_exit"]
    assert "No signal exit" in execution["fixed_exit"]
    assert "Wilder RMA alpha=1/24" in execution["atr"]
    assert "three frozen ATR24" in execution["protective_stop"]
    assert "reference_price=min(stop_price,bar_open)" in execution["protective_stop"]
    assert "reference prices never embed slippage" in execution["protective_stop"]
    assert "fill_timestamp=bar.open_time" in execution["protective_stop"]
    assert "fill_timestamp=bar.close_time" in execution["protective_stop"]
    assert "before any funding settlement at that same timestamp" in execution["protective_stop"]
    assert "nominal_time<=that close timestamp" in execution["protective_stop"]
    assert "mate remains protected" in execution["protective_stop"]
    assert "both legs trigger" in execution["dual_stop_sequence"]
    assert "lexicographic symbol order" in execution["dual_stop_sequence"]
    assert "following available 1h open" in execution["atomicity"]
    assert "A stop touched during the entry hour fills first" in execution["atomicity"]
    assert "count in C256 P&L, drawdown and sample size" in execution["atomicity"]


def test_costs_charge_four_actual_fills_funding_impact_and_adverse_stress():
    costs = load_contract()["costs"]
    assert costs["base_pair_round_trip_fills"] == 4
    assert costs["reference_base_pair_round_trip_bps_of_initial_gross"] == 2 * (
        costs["taker_fee_bps_per_fill_per_leg"] + costs["adverse_slippage_bps_per_fill_per_leg"]
    )
    assert costs["reference_stress_pair_round_trip_bps_of_initial_gross"] == 2 * (
        costs["taker_fee_bps_per_fill_per_leg"] + costs["stress_adverse_slippage_bps_per_fill_per_leg"]
    )
    ledger = costs["fill_ledger_and_pnl"]
    assert "Movement P&L is computed only from raw entry/exit reference_price" in ledger
    assert "slippage_dollars=abs(quantity)*abs(execution_price-reference_price)" in ledger
    assert "fee_dollars=abs(quantity*execution_price)*fee_bps/10000" in ledger
    assert "subtracted exactly once" in ledger
    assert "Never use execution-price movement P&L" in costs["cost_denominator"]
    assert "funding_pnl=-side_sign*abs(actual_qty)*causal_mark_price*funding_rate" in costs["funding"]
    assert "side_sign=+1 for long and -1 for short" in costs["funding"]
    assert "strictly before nominal_time" in costs["funding"]
    assert "entry_time<=nominal_time<=exit_fill_timestamp" in costs["funding"]
    assert "entry_time<=nominal_time<exit_fill_timestamp" in costs["funding"]
    assert "nominal_time==entry_time is INCLUDED" in costs["funding"]
    assert "nominal_time==exit_fill_timestamp is EXCLUDED" in costs["funding"]
    assert "0.10%" in costs["market_impact"]
    assert "close_time is strictly before the proposed fill timestamp" in costs["market_impact"]
    assert "never use the fill bar, a still-open/current bar" in costs["market_impact"]
    assert "Missing, non-finite or <=0" in costs["market_impact"]
    assert "INCOMPLETE_IMPACT_AUTHORITY" in costs["market_impact"]
    assert "gap stops at bar.open_time and non-gap range stops at bar.close_time" in costs["market_impact"]
    assert "base_or_stress_adverse_slippage_bps*max(1,impact_ratio/0.001)" in costs["market_impact"]
    assert "INCOMPLETE_IMPACT_LIMIT" in costs["market_impact"]
    assert "over-cap fill cannot fall through" in costs["market_impact"]
    assert "funding_pnl<0 multiply its magnitude by 1.5" in costs["stress"]
    assert "funding_pnl>0 divide it by 1.5" in costs["stress"]
    assert "never flips a sign" in costs["stress"]


def test_pair_risk_is_aggregate_dual_stop_and_portfolio_ceiling_is_honest():
    position = load_contract()["position_and_portfolio"]
    assert "long leg's raw reference-price move" in position["risk_unit"]
    assert "short leg's raw reference-price move" in position["risk_unit"]
    assert "both entry fills and both stop exits" in position["risk_unit"]
    assert "raw reference-price move" in position["risk_unit"]
    assert "slippage is never embedded" in position["risk_unit"]
    assert "exactly once" in position["risk_unit"]
    assert "simultaneous two-leg stops" in position["risk_unit"]
    assert "aggregate_modeled_pair_loss<=1R" in position["risk_unit"]
    assert position["max_concurrent_pairs"] == 3
    assert position["max_open_risk_r"] == 3.0
    assert "aggregate modeled ceiling" in position["risk_aggregation"]
    assert "gap losses cannot exceed" in position["risk_aggregation"]


def test_gates_preserve_frequency_profitability_both_sides_and_high_dd_disclosure():
    contract = load_contract()
    gates = contract["gates"]
    joined = "\n".join(gates["forward_final_pass_all_required"])
    tiers = gates["drawdown_disclosure_tiers"]
    assert "at least 600 closed paired attempts" in joined
    assert "12 symbols" in joined
    assert "2 of 3 forward folds" in joined
    assert "long-leg and short-leg" in joined
    assert "stress-cost aggregate mean net R>0" in joined
    assert "p-value<=0.05/256" in joined
    assert "15R<max drawdown<=30R" in tiers["elevated"]
    assert "30R<max drawdown<=60R" in tiers["high"]
    assert "min(1,15/max_drawdown_R)" in tiers["high"]
    assert gates["undefined_statistics"] == "FAIL_CLOSED"


def test_trial_budget_bootstrap_expected_counts_and_no_rescue_are_frozen():
    contract = load_contract()
    expected = contract["expected_test_count"]
    walk = contract["walk_forward"]
    bootstrap = contract["bootstrap_specification"]
    budget = contract["trial_budget"]
    dev_start, dev_end = interval(walk["development_test_range"])
    fold_days = sum((end - start).days for start, end in map(interval, walk["forward_folds"]))
    assert expected["development_calendar_days"] == (dev_end - dev_start).days
    assert expected["forward_calendar_days"] == fold_days
    assert expected["development_filter_eligible_days"] == 108
    assert expected["forward_calendar_days"] == 545
    assert "sorted union" in expected["actual_schedule_formula"]
    assert "do not assume three events per day" in expected["actual_schedule_formula"]
    assert expected["eight_hour_reference_only"] == {
        "label": "non-gating reference if every eligible symbol remained on an 8h cadence",
        "development_filter_eligible_event_hours": 324,
        "forward_event_hours": 1635,
    }
    upper = expected["absolute_24_per_day_upper_bounds"]
    assert upper["development_filter_eligible_event_hours"] == 108 * 24
    assert upper["development_pairs_at_three_per_event"] == 108 * 24 * 3
    assert upper["development_leg_round_trips"] == 108 * 24 * 3 * 2
    assert upper["forward_event_hours"] == 545 * 24
    assert upper["forward_pairs_at_three_per_event"] == 545 * 24 * 3
    assert upper["forward_leg_round_trips"] == 545 * 24 * 3 * 2
    assert "1/2/4/8h" in expected["note"]
    assert bootstrap["expected_block_length_attempts"] == 21
    assert bootstrap["resamples"] == 10000
    assert bootstrap["seed"] == 25620261107
    assert budget["promotable_primary"] == 1
    assert budget["ledger_records_expected"] == 8
    assert "capital slice cannot rescue" in budget["rescue_rule"]
    assert any("rejection is final" in rule.lower() for rule in contract["falsification"])


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("unlock", lambda d: d.update(evaluation_locked=False)),
        ("authority", lambda d: d["data_authority"].update(universe_sha256="0" * 64)),
        ("holdout", lambda d: d["walk_forward"].update(holdout_policy="OPEN")),
        ("threshold", lambda d: d["funding_surprise"]["thresholds"].update(long_tail="z<=-1.0")),
        ("cost", lambda d: d["costs"].update(adverse_slippage_bps_per_fill_per_leg=0.0)),
        ("dd", lambda d: d["gates"]["drawdown_disclosure_tiers"].update(reject="max drawdown>100R")),
        ("alpha", lambda d: d["multiplicity"].update(candidate_familywise_alpha_ceiling=0.05)),
        ("rescue", lambda d: d["trial_budget"].update(rescue_rule="diagnostics may rescue")),
        ("lookahead_entry", lambda d: d["execution"].update(entry="enter at T open")),
        ("remove_risk_cap", lambda d: d["position_and_portfolio"].update(max_open_risk_r=99.0)),
        ("single_leg_risk", lambda d: d["position_and_portfolio"].update(risk_unit="use larger single-leg loss")),
        ("remove_aggregation", lambda d: d["position_and_portfolio"].update(risk_aggregation="none")),
        ("funding_credits", lambda d: d["costs"].update(funding="credit both legs at every settlement")),
        ("funding_stress_flip", lambda d: d["costs"].update(stress="turn debits into credits")),
        ("funding_boundary_inclusive_for_open_exit", lambda d: d["costs"].update(funding="include funding at every exit timestamp")),
        ("funding_boundary_omitted_for_range_stop", lambda d: d["costs"].update(funding="exclude funding when nominal_time equals range-stop close")),
        ("funding_strict_entry_boundary", lambda d: d["costs"].update(funding="include settlements only when entry_time<nominal_time<exit_fill_timestamp")),
        ("double_charge_slippage", lambda d: d["costs"].update(fill_ledger_and_pnl="compute movement from execution_price and subtract slippage_dollars again")),
        ("missing_slippage_charge", lambda d: d["costs"].update(fill_ledger_and_pnl="movement uses reference_price; omit slippage_dollars")),
        ("embedded_stop_slippage", lambda d: d["execution"].update(protective_stop="stop fill=stop_price*(1-slip)")),
        ("stop_timestamp_omitted", lambda d: d["execution"].update(protective_stop="stop at stop_price with no timestamp")),
        ("gap_stop_at_close", lambda d: d["execution"].update(protective_stop="gap stop fill_timestamp=bar.close_time")),
        ("current_filters", lambda d: d["data_authority"].update(exchange_filter_requirement="use current exchangeInfo")),
        ("filter_binding", lambda d: d["data_authority"]["exchange_filter_fold_bindings"].update(wf1="2026-02-17T04:13:28Z")),
        ("false_counts", lambda d: d["expected_test_count"].update(actual_schedule_formula="three per day")),
        ("false_upper_bound", lambda d: d["expected_test_count"]["absolute_24_per_day_upper_bounds"].update(forward_event_hours=1635)),
        ("remove_dual_stop", lambda d: d["execution"].update(dual_stop_sequence="undefined")),
        ("same_open_flatten", lambda d: d["execution"].update(atomicity="flatten immediately at same open")),
        ("adaptive_repair", lambda d: d["selection_and_pairing"].update(infeasible_pair_rule="re-pair with next symbol")),
        ("fill_bar_volume", lambda d: d["costs"].update(market_impact="use the fill bar quote volume")),
        ("current_volume", lambda d: d["costs"].update(market_impact="use current quote volume")),
        ("over_cap_required_exit_fallthrough", lambda d: d["costs"].update(market_impact="required exits over 0.10% complete normally")),
    ],
)
def test_hostile_contract_mutations_are_rejected(case, mutate):
    hostile = copy.deepcopy(load_contract())
    mutate(hostile)
    with pytest.raises(AssertionError):
        validate_immutable_contract(hostile)
