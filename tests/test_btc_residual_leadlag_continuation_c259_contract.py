import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "research_evidence" / "prereg" / "btc_residual_leadlag_continuation_c259_stagea.v1.json"
PLAN_PATH = ROOT / "contracts" / "strategy_causal_pack_01" / "authority_extension_plan.v6.json"
BASE = "64adf3a41d7a521d2af480adc7b2f23a8fa6a0c3"
CANONICAL_CONTRACT_SHA256 = "408fb121a122b08643f43c2375f24350ef7c062c28bab7ea5b46aa640aee2b25"
EXPECTED_TOP_LEVEL = {
    "format", "candidate_id", "candidate_key", "base_commit", "frozen_at_cairo",
    "status", "default_off", "evaluation_locked", "paper_eligibility", "roadmap_fit",
    "deferred", "hypothesis", "selection_provenance", "authority", "splits", "signal",
    "execution", "sizing", "risk", "costs", "regime", "gates", "statistics",
    "multiplicity", "paper_admission",
}
EXPECTED_OUTPUTS = [
    "research_evidence/manifests/binance-um-archive-precutoff-v6.json.gz",
    "research_evidence/universe/pit-top40-qv30d-precutoff-v9.json",
    "contracts/strategy_causal_pack_01/exchange_filter_authority.v4.json",
    "contracts/strategy_causal_pack_01/precutoff_authority_coverage_preflight.v4.json",
]


def load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def canonical_hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def interval(value):
    start, end = value.split("/")
    parse = lambda text: datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parse(start), parse(end)


def validate_contract(c, *, check_hash=True):
    assert set(c) == EXPECTED_TOP_LEVEL
    if check_hash:
        assert canonical_hash(c) == CANONICAL_CONTRACT_SHA256
    assert c["format"] == "zb-strategy-preregistration/1"
    assert c["candidate_id"] == 259
    assert c["candidate_key"] == "btc_residual_leadlag_continuation_1h"
    assert c["base_commit"] == BASE
    assert c["status"] == "FROZEN_NOT_EVALUATED_RESEARCH_ONLY"
    assert c["default_off"] is True
    assert c["evaluation_locked"] is True
    assert c["paper_eligibility"] == "BLOCK_UNTIL_INDEPENDENT_OOS_PASS"
    assert "market-row" in " ".join(c["deferred"])
    assert "mainnet" in c["deferred"]

    provenance = c["selection_provenance"]
    assert provenance["chosen_without_outcomes"] is True
    assert "No C259 market row" in provenance["not_holdout_rescue"]
    distinctions = provenance["economic_distinction"]
    assert "aggressor imbalance" in distinctions["from_bol_1h"]
    assert "positive statistically identified" in distinctions["from_bol_1h"]
    assert "fades" in distinctions["from_dsr_1h"]
    assert "never uses BTC" in distinctions["from_c258"]
    assert "own lagged quote-volume" in distinctions["from_c52"]

    authority = c["authority"]
    assert authority["plan_path"] == str(PLAN_PATH.relative_to(ROOT)).replace("\\", "/")
    assert authority["plan_sha256"] == hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()
    assert authority["plan_authority_id"] == "BINANCE-UM-PRECUTOFF-AUTHORITY-V6"
    assert authority["required_future_outputs"] == EXPECTED_OUTPUTS
    assert authority["required_streams"] == ["klines:1h", "markPriceKlines:1h", "fundingRate:null"]
    assert "mechanical amendment" in authority["future_pin_rule"]
    assert "ZERO_GUARANTEED_PERIOD_GAPS" in authority["pre_market_gate"]
    assert "BTCUSDT is the external benchmark and cannot be traded" in authority["membership"]
    assert "Never forward-fill" in authority["availability"]
    assert authority["cutoff_exclusive"] == "2026-11-05T00:00:00Z"

    splits = c["splits"]
    development = interval(splits["development"])
    folds = [interval(value) for value in splits["forward_folds"]]
    holdout = interval(splits["sealed_holdout"])
    assert development[1] == folds[0][0]
    assert all(left[1] == right[0] for left, right in zip(folds, folds[1:]))
    assert folds[-1][1] == holdout[0]
    assert holdout[1].isoformat() == "2026-11-05T00:00:00+00:00"
    assert splits["holdout_policy"] == "SEALED_UNTIL_FORWARD_PASS_AND_INDEPENDENT_SPEND"
    assert "resets all beta, lag, ATR" in splits["boundary_rule"]
    assert "Development can only reject C259" in splits["one_shot_rule"]
    assert "No threshold" in splits["one_shot_rule"]

    signal = c["signal"]
    assert signal["timeframe"] == "1h"
    assert signal["side_cells"] == [
        "long_after_positive_btc_impulse", "short_after_negative_btc_impulse"
    ]
    assert "strictly after d" in signal["decision_clock"]
    assert "exact contiguous canonical timestamps" in signal["returns"]
    assert "exactly the 720" in signal["estimation_window"]
    assert "excluding d" in signal["estimation_window"]
    assert "beta0=math.fsum" in signal["contemporaneous_beta"]
    assert "gamma=math.fsum" in signal["lag_model"]
    assert "s=d-719h..d-1h" in signal["lag_model"]
    assert "sigma2=sse/718" in signal["lag_model"]
    assert "IEEE-754 binary64" in signal["lag_model"]
    assert "r_btc_d>=max(0.0075,3.0*scale)" in signal["btc_impulse"]
    assert "gamma>=0.10" in signal["unconsumed_forecast"]
    assert "t_gamma>=2.0" in signal["unconsumed_forecast"]
    assert "forecast>=0.0028" in signal["unconsumed_forecast"]
    assert "forecast<=-0.0028" in signal["unconsumed_forecast"]
    assert "preceding 24 completed hours excluding d" in signal["liquidity"]
    assert "at most three" in signal["ordering_and_capacity"]
    assert "d is excluded from every coefficient" in signal["anti_lookahead"]
    assert "included exactly once in causal ATR24" in signal["anti_lookahead"]

    execution = c["execution"]
    assert "strictly after d" in execution["entry"]
    assert "updated through completed decision bar d exactly once" in execution["atr"]
    assert "No entry-open or later value" in execution["atr"]
    assert "reduce-only 2ATR stop" in execution["protection"]
    assert "Stop wins" in execution["protection"]
    assert "at least 12 hours" in execution["exit"]
    assert "No partial" in execution["exit"]
    assert "include all actual cashflows as a closed loss-row" in execution["failure_accounting"]
    assert "whole candidate INCOMPLETE" in execution["failure_accounting"]
    assert "Floor quantity" in execution["exchange_rules"]

    sizing = c["sizing"]
    assert sizing["primary_capital_usd"] == 2000
    assert sizing["diagnostic_capitals_usd"] == [1000, 3000, 4000]
    assert "cannot veto, select, rescue" in sizing["capital_selection_rule"]
    assert "0.01*book_equity" in sizing["risk_cash"]
    assert "stressed nonnegative" in sizing["risk_cash"]
    assert "floor(continuous_quantity/stepSize)*stepSize" in sizing["rounding"]
    assert "Never increase" in sizing["rounding"]
    assert "only from the 2000 USD primary replay" in sizing["primary_gate"]

    risk = c["risk"]
    assert risk["risk_per_position_r"] == 1.0
    assert risk["max_concurrent_positions"] == 3
    assert risk["max_open_risk_r"] == 3.0
    assert risk["capital_simulations_usd"] == [1000, 2000, 3000, 4000]
    assert "cannot rescue" in risk["side_isolation"]

    costs = c["costs"]
    assert costs["taker_fee_bps_per_fill"] == 5.0
    assert costs["adverse_slippage_bps_per_fill"] == 2.0
    assert costs["stress_adverse_slippage_bps_per_fill"] == 10.0
    assert costs["base_round_trip_bps"] == 14.0
    assert "fee_drag and slippage_drag are finite nonnegative" in costs["pnl_rule"]
    assert "net_cash=movement_cash+funding_cash-fee_drag-slippage_drag" in costs["pnl_rule"]
    assert "never assume zero" in costs["funding"]
    assert "<=1%" in costs["impact"]
    assert "five times base 14 bp" in costs["cost_admission"]
    assert "identical admitted primary trade set" in costs["stress"]

    regime = c["regime"]
    assert "EMA200" in regime["definition"]
    assert "close_d>=EMA200_d" in regime["definition"]
    assert "no later close" in regime["causality"]
    assert "exactly one label" in regime["attribution"]

    gates = c["gates"]
    dev = "\n".join(gates["development_reject_if_any_per_side"])
    fwd = "\n".join(gates["forward_pass_all_required_per_side"])
    hold = "\n".join(gates["holdout_pass_all_required_per_side"])
    assert "pre-cost gross expectancy<=0" in dev
    assert "fewer than 120" in dev
    assert "at least 300" in fwd
    assert "median at least 12" in fwd
    assert "0.05/(259*2)" in fwd
    assert "strictly more than 55%" in fwd
    assert "at least 20" in hold
    assert gates["drawdown_tiers"]["reject"] == "max drawdown>60R"
    assert gates["undefined_statistics"] == "FAIL_CLOSED"

    stats = c["statistics"]
    assert stats["expected_block_length_trades"] == 21
    assert stats["resamples"] == 10000
    assert "PCG64 only" in stats["prng"]
    assert "exactly resamples*n*2 uniforms" in stats["fixed_draw"]
    assert len({stats["long_null_seed"], stats["long_ci_seed"], stats["short_null_seed"], stats["short_ci_seed"]}) == 4
    assert "10000+1" in stats["p_value"]
    assert "method='linear'" in stats["confidence_interval"]

    multiplicity = c["multiplicity"]
    assert multiplicity["numbered_candidate_family"] == 259
    assert multiplicity["promotable_primary_count"] == 2
    assert multiplicity["cell_alpha_ceiling"] == pytest.approx(0.05 / (259 * 2))
    assert "Pooling sides" in multiplicity["no_rescue"]

    paper = c["paper_admission"]
    assert paper["default_off"] is True
    assert paper["paper_testnet_only"] is True
    assert "0.25R" in paper["bounded_canary"]
    assert "50 complete measured closes" in paper["paper_transfer_gate"]
    assert "PAPER cannot rescue" in paper["paper_transfer_gate"]


def test_frozen_contract_binds_every_byte_and_authority_plan():
    validate_contract(load_contract())


def test_metadata_only_distinction_is_explicit_and_does_not_claim_results():
    c = load_contract()
    p = c["selection_provenance"]
    assert "No frozen candidate estimates" in p["duplicate_search"]
    assert "No C259 market row" in p["not_holdout_rescue"]
    assert c["status"] == "FROZEN_NOT_EVALUATED_RESEARCH_ONLY"
    assert c["paper_eligibility"].startswith("BLOCK")


def test_splits_are_contiguous_but_statistics_reset_and_holdout_stays_sealed():
    c = load_contract()["splits"]
    dev = interval(c["development"])
    folds = [interval(v) for v in c["forward_folds"]]
    holdout = interval(c["sealed_holdout"])
    assert dev[1] == folds[0][0]
    assert folds[-1][1] == holdout[0]
    assert "starts flat" in c["boundary_rule"]
    assert "Prior rows may not warm" in c["boundary_rule"]
    assert "spends the sealed holdout once" in c["one_shot_rule"]


def test_lag_model_is_strictly_predecision_and_impulse_response_is_unconsumed():
    s = load_contract()["signal"]
    assert "excluding d" in s["estimation_window"]
    assert "d-1h" in s["lag_model"]
    assert "positive BTC impulse" in s["unconsumed_forecast"]
    assert "negative BTC impulse" in s["unconsumed_forecast"]
    assert "residual_d<=0.25*forecast" in s["unconsumed_forecast"]
    assert "residual_d>=0.25*forecast" in s["unconsumed_forecast"]
    assert "strictly later open" in s["anti_lookahead"]


def test_primary_capital_is_only_gate_and_diagnostics_are_non_vetoing():
    c = load_contract()
    selection = c["sizing"]["capital_selection_rule"]
    assert "Only the complete 2000 USD replay" in selection
    assert "1000/3000/4000" in selection
    assert "cannot veto, select, rescue" in selection
    assert c["risk"]["capital_simulations_usd"] == [1000, 2000, 3000, 4000]


def test_both_sides_are_separate_cells_with_correct_multiple_testing_ceiling():
    c = load_contract()
    assert len(c["signal"]["side_cells"]) == 2
    assert "separate hypotheses" in c["signal"]["unconsumed_forecast"]
    assert "one side cannot rescue" in c["risk"]["side_isolation"]
    assert c["multiplicity"]["cell_alpha_ceiling"] == pytest.approx(0.05 / 518)


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("unlock", lambda d: d.update(evaluation_locked=False)),
        ("enable", lambda d: d.update(default_off=False)),
        ("authority_hash", lambda d: d["authority"].update(plan_sha256="0" * 64)),
        ("authority_output", lambda d: d["authority"]["required_future_outputs"].pop()),
        ("current_membership", lambda d: d["authority"].update(membership="current top 40")),
        ("open_holdout", lambda d: d["splits"].update(holdout_policy="OPEN")),
        ("cross_split_warmup", lambda d: d["splits"].update(boundary_rule="carry beta forward")),
        ("same_bar", lambda d: d["execution"].update(entry="enter at decision close")),
        ("no_stop", lambda d: d["execution"].update(protection="none")),
        ("use_d_in_fit", lambda d: d["signal"].update(estimation_window="include d")),
        ("nearest_bar", lambda d: d["signal"].update(returns="use nearest candle")),
        ("weak_impulse", lambda d: d["signal"].update(btc_impulse="any BTC return")),
        ("no_tstat", lambda d: d["signal"].update(unconsumed_forecast="gamma>0")),
        ("no_liquidity", lambda d: d["signal"].update(liquidity="always pass")),
        ("primary_changed", lambda d: d["sizing"].update(primary_capital_usd=4000)),
        ("diagnostic_veto", lambda d: d["sizing"].update(capital_selection_rule="all capitals must pass")),
        ("round_up", lambda d: d["sizing"].update(rounding="ceil quantity")),
        ("risk_expand", lambda d: d["risk"].update(max_open_risk_r=9.0)),
        ("pool_sides", lambda d: d["risk"].update(side_isolation="pool PnL")),
        ("fee_zero", lambda d: d["costs"].update(taker_fee_bps_per_fill=0.0)),
        ("stress_zero", lambda d: d["costs"].update(stress_adverse_slippage_bps_per_fill=0.0)),
        ("zero_funding", lambda d: d["costs"].update(funding="assume zero")),
        ("positive_impact", lambda d: d["costs"].update(impact="credit impact")),
        ("sample_relax", lambda d: d["gates"]["forward_pass_all_required_per_side"].__setitem__(2, "10 trades")),
        ("alpha_relax", lambda d: d["multiplicity"].update(cell_alpha_ceiling=0.05)),
        ("rescue", lambda d: d["multiplicity"].update(no_rescue="select best side")),
        ("paper_risk", lambda d: d["paper_admission"].update(bounded_canary="3R each")),
    ],
)
def test_hostile_mutations_fail_semantic_validator(name, mutate):
    hostile = copy.deepcopy(load_contract())
    mutate(hostile)
    with pytest.raises(AssertionError):
        validate_contract(hostile, check_hash=False)
