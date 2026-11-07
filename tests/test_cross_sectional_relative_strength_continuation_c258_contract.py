import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "research_evidence/prereg/cross_sectional_relative_strength_continuation_c258_stagea.v1.json"
PLAN_PATH = ROOT / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
BASE = "8f63b5e8affd2d23a8c69d6d0185c502951aff78"


def load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def interval(raw):
    start, end = raw.split("/", 1)
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parse(start), parse(end)


def validate_frozen_contract(contract):
    assert contract["candidate_id"] == 258
    assert contract["candidate_key"] == "cross_sectional_relative_strength_continuation_1h"
    assert contract["base_commit"] == BASE
    assert contract["status"] == "FROZEN_NOT_EVALUATED_RESEARCH_ONLY"
    assert contract["default_off"] is True
    assert contract["evaluation_locked"] is True
    assert contract["paper_eligibility"] == "BLOCK_UNTIL_INDEPENDENT_OOS_PASS"
    assert contract["selection_provenance"]["chosen_without_outcomes"] is True
    assert "No C258 market row" in contract["selection_provenance"]["not_holdout_rescue"]

    authority = contract["authority"]
    assert authority["plan_sha256"] == hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()
    assert authority["plan_authority_id"] == "BINANCE-UM-PRECUTOFF-AUTHORITY-V6"
    assert "one mechanical amendment" in authority["future_pin_rule"]
    assert "Any other change creates a new numbered candidate" in authority["future_pin_rule"]
    assert "ZERO_GUARANTEED_PERIOD_GAPS" in authority["pre_market_gate"]
    assert "Current membership" in authority["membership"]
    assert "never forward-fill" in authority["availability"]

    splits = contract["splits"]
    assert splits["holdout_policy"] == "SEALED_AND_UNAVAILABLE_TO_STAGE_A"
    assert "can only reject C258" in splits["one_shot_rule"]
    assert "No threshold" in splits["one_shot_rule"]

    signal = contract["signal"]
    assert signal["timeframe"] == "1h"
    assert "d-24h" in signal["effective_set"]
    assert "at least 16" in signal["effective_set"]
    assert signal["tails"]["long"] == "z>=+1.5"
    assert signal["tails"]["short"] == "z<=-1.5"
    assert "never re-pair" in signal["pairing"]
    assert "50% long and 50% short" in signal["weights"]

    execution = contract["execution"]
    assert "strictly after decision close" in execution["entry"]
    assert "at least six hours" in execution["fixed_exit"]
    assert "two frozen ATR24" in execution["protective_stop"]
    assert "Stops never embed slippage" in execution["protective_stop"]
    assert "whole candidate INCOMPLETE" in execution["ambiguity"]
    assert "never at the same open" in execution["atomic_failure"]

    risk = contract["risk"]
    assert risk["max_concurrent_pairs"] == 3
    assert risk["max_open_risk_r"] == 3.0
    assert "simultaneous two-ATR stops" in risk["pair_risk"]
    assert "<=1R" in risk["pair_risk"]
    assert risk["capital_simulations_usd"] == [1000, 2000, 3000, 4000]

    costs = contract["costs"]
    assert costs["taker_fee_bps_per_fill_per_leg"] == 5.0
    assert costs["adverse_slippage_bps_per_fill_per_leg"] == 2.0
    assert costs["stress_adverse_slippage_bps_per_fill_per_leg"] == 10.0
    assert costs["fills_per_pair_round_trip"] == 4
    assert "exactly once" in costs["pnl_rule"]
    assert "last verified 1h mark close strictly before" in costs["funding"]
    assert "0.10%" in costs["impact"]
    assert "never improves a fill" in costs["stress"]

    gates = "\n".join(contract["gates"]["forward_pass_all_required"])
    assert "at least 1000 closed paired attempts" in gates
    assert "median at least 30 closed pairs per UTC week" in gates
    assert "long-leg and short-leg" in gates
    assert "p-value<=0.05/258" in gates
    assert contract["gates"]["drawdown_tiers"]["reject"] == "max drawdown>60R"
    assert contract["gates"]["undefined_statistics"] == "FAIL_CLOSED"

    multiplicity = contract["multiplicity"]
    assert multiplicity["numbered_candidate_family"] == 258
    assert multiplicity["candidate_familywise_alpha_ceiling"] == pytest.approx(0.05 / 258)
    assert multiplicity["promotable_primary_count"] == 1
    assert "cannot rescue" in multiplicity["no_rescue"]

    paper = contract["paper_admission"]
    assert paper["default_off"] is True
    assert paper["paper_testnet_only"] is True
    assert "0.25R" in paper["bounded_canary"]
    assert "100 complete measured paired closes" in paper["paper_transfer_gate"]
    assert "PAPER cannot rescue" in paper["paper_transfer_gate"]


def test_contract_is_frozen_default_off_and_byte_binds_authority_plan():
    contract = load_contract()
    validate_frozen_contract(contract)
    assert contract["authority"]["plan_path"] == str(PLAN_PATH.relative_to(ROOT)).replace("\\", "/")
    assert "market-row" in " ".join(contract["deferred"])
    assert "mainnet" in contract["deferred"]


def test_chronology_is_contiguous_and_holdout_is_sealed():
    splits = load_contract()["splits"]
    development = interval(splits["development"])
    folds = [interval(raw) for raw in splits["forward_folds"]]
    holdout = interval(splits["sealed_holdout"])
    assert development[1] == folds[0][0]
    assert all(left[1] == right[0] for left, right in zip(folds, folds[1:]))
    assert folds[-1][1] == holdout[0]
    assert holdout[1].isoformat() == "2026-11-05T00:00:00+00:00"
    assert splits["holdout_policy"] == "SEALED_AND_UNAVAILABLE_TO_STAGE_A"
    assert "No position" in splits["boundary_rule"]
    assert "can only reject" in splits["one_shot_rule"]
    assert "No threshold" in splits["one_shot_rule"]


def test_candidate_is_price_continuation_not_funding_or_prior_registered_families():
    provenance = load_contract()["selection_provenance"]
    distinctions = provenance["economic_distinction"]
    assert "never uses funding to form or rank a signal" in distinctions["from_c257"]
    assert "4h, long-only" in distinctions["from_rotation_long_h2"]
    assert "C258 is continuation" in distinctions["from_rank_breadth_reversal"]
    assert "simultaneous point-in-time cross-section" in distinctions["from_time_series_momentum"]
    assert "no preregistration" in provenance["duplicate_search"]


def test_signal_is_causal_robust_paired_and_not_repaired():
    signal = load_contract()["signal"]
    assert "completed canonical 1h candle close" in signal["decision_clock"]
    assert "exact canonical timestamps" in signal["return_24h"]
    assert "compressed time" in signal["return_24h"]
    assert "1.4826*mad" in signal["robust_score"]
    assert "Non-positive or non-finite scale" in signal["robust_score"]
    assert "i=1..3" in signal["pairing"]
    assert "never re-pair" in signal["pairing"]
    assert "cannot open another C258 leg" in signal["duplicate_rule"]


def test_cost_risk_frequency_regime_and_drawdown_gates_are_all_hard():
    contract = load_contract()
    dev = "\n".join(contract["gates"]["development_reject_if_any"])
    fwd = "\n".join(contract["gates"]["forward_pass_all_required"])
    assert "pre-cost gross expectancy<=0" in dev
    assert "fewer than 300" in dev
    assert "median fewer than 25" in dev
    assert "stress-cost mean net R<=0" in dev
    assert "aggregate mean after-cost net R>0.03" in fwd
    assert "at least 16 symbols" in fwd
    assert "BTC-above/on-EMA200" in fwd
    assert "stress-cost aggregate mean net R>0" in fwd
    high = contract["gates"]["drawdown_tiers"]["high"]
    assert "30R<max drawdown<=60R" in high
    assert "future PAPER pair-risk multiplier" in high


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("unlock", lambda d: d.update(evaluation_locked=False)),
        ("enable", lambda d: d.update(default_off=False)),
        ("authority_hash", lambda d: d["authority"].update(plan_sha256="0" * 64)),
        ("current_membership", lambda d: d["authority"].update(membership="use current top 40")),
        ("open_holdout", lambda d: d["splits"].update(holdout_policy="OPEN")),
        ("same_bar_entry", lambda d: d["execution"].update(entry="fill at decision close")),
        ("remove_stop", lambda d: d["execution"].update(protective_stop="none")),
        ("lower_tail", lambda d: d["signal"]["tails"].update(long="z>=+0.5")),
        ("adaptive_pair", lambda d: d["signal"].update(pairing="replace infeasible member")),
        ("fee_zero", lambda d: d["costs"].update(taker_fee_bps_per_fill_per_leg=0.0)),
        ("stress_zero", lambda d: d["costs"].update(stress_adverse_slippage_bps_per_fill_per_leg=0.0)),
        ("double_slippage", lambda d: d["costs"].update(pnl_rule="movement from execution price and subtract slippage again")),
        ("current_volume", lambda d: d["costs"].update(impact="use fill-bar quote volume")),
        ("risk_expand", lambda d: d["risk"].update(max_open_risk_r=9.0)),
        ("sample_relax", lambda d: d["gates"]["forward_pass_all_required"].__setitem__(2, "at least 10 closed attempts")),
        ("alpha_relax", lambda d: d["multiplicity"].update(candidate_familywise_alpha_ceiling=0.05)),
        ("rescue", lambda d: d["multiplicity"].update(no_rescue="diagnostics may rescue")),
        ("paper_risk", lambda d: d["paper_admission"].update(bounded_canary="start at 3R per pair")),
    ],
)
def test_hostile_mutations_fail_the_frozen_contract(case, mutate):
    hostile = copy.deepcopy(load_contract())
    mutate(hostile)
    with pytest.raises(AssertionError):
        validate_frozen_contract(hostile)
