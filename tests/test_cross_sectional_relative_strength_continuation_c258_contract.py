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
CANONICAL_CONTRACT_SHA256 = "487eaf27ee8c49c16a76c1b792dbef87759524c69cf723af07c3173672a2f658"
EXPECTED_TOP_LEVEL = {
    "candidate_id", "candidate_key", "format", "base_commit", "frozen_at_cairo", "status",
    "default_off", "evaluation_locked", "paper_eligibility", "roadmap_fit", "deferred",
    "hypothesis", "selection_provenance", "authority", "splits", "signal", "execution",
    "sizing", "risk", "regime", "costs", "gates", "statistics", "multiplicity",
    "paper_admission", "falsification",
}
EXPECTED_FUTURE_OUTPUTS = [
    "research_evidence/manifests/binance-um-archive-precutoff-v6.json.gz",
    "research_evidence/universe/pit-top40-qv30d-precutoff-v9.json",
    "contracts/strategy_causal_pack_01/exchange_filter_authority.v4.json",
    "contracts/strategy_causal_pack_01/precutoff_authority_coverage_preflight.v4.json",
]
EXPECTED_PIN_RULE = (
    "After all four authority-v6 outputs independently pass and protected-merge, and before any market "
    "or outcome read, one mechanical amendment may add their exact byte hashes/content digests and the "
    "accepted protected head. It may change no hypothesis, signal, side, universe rule, execution, risk, "
    "cost, split, gate, multiplicity or PAPER term. Any other change creates a new numbered candidate."
)


def load_contract():
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def interval(raw):
    start, end = raw.split("/", 1)
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parse(start), parse(end)


def canonical_hash(contract):
    payload = json.dumps(
        contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_frozen_contract(contract):
    assert set(contract) == EXPECTED_TOP_LEVEL
    assert canonical_hash(contract) == CANONICAL_CONTRACT_SHA256
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
    assert set(authority) == {
        "plan_path", "plan_sha256", "plan_authority_id", "plan_status_required_at_freeze",
        "required_future_outputs", "required_streams", "future_pin_rule", "pre_market_gate",
        "membership", "availability", "cutoff_exclusive",
    }
    assert authority["plan_sha256"] == hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest()
    assert authority["plan_authority_id"] == "BINANCE-UM-PRECUTOFF-AUTHORITY-V6"
    assert authority["required_future_outputs"] == EXPECTED_FUTURE_OUTPUTS
    assert authority["future_pin_rule"] == EXPECTED_PIN_RULE
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
    assert "target equal raw notional" in signal["weights"]
    assert "actual rounded raw notionals need not be exactly equal" in signal["weights"]
    assert "<=0.01" in signal["weights"]

    execution = contract["execution"]
    assert "strictly after decision close" in execution["entry"]
    assert "at least six hours" in execution["fixed_exit"]
    assert "two frozen ATR24" in execution["protective_stop"]
    assert "Stops never embed slippage" in execution["protective_stop"]
    assert "whole candidate INCOMPLETE" in execution["ambiguity"]
    assert "never at the same open" in execution["atomic_failure"]
    assert "ATOMIC_ONE_LEG_LOSS" in execution["atomic_failure"]
    assert "verdict_net_cash=min(actual_net_cash" in execution["atomic_failure"]
    assert "fee_drag" in execution["atomic_failure"]
    assert "slippage_drag" in execution["atomic_failure"]
    assert "actual_net_cash=movement_cash+funding_cash-fee_drag-slippage_drag" in execution["atomic_failure"]
    assert "conservative_adjustment_cash=verdict_net_cash-actual_net_cash" in execution["atomic_failure"]
    assert "included without exception" in execution["atomic_failure"]
    assert "every development, forward and PAPER transfer gate" in execution["atomic_failure"]

    sizing = contract["sizing"]
    assert set(sizing) == {
        "primary_capital_usd", "diagnostic_capitals_usd", "capital_selection_rule",
        "book_equity", "r_cash", "continuous_start", "step_floor_and_search", "neutrality",
        "post_rounding_checks", "primary_gate",
    }
    assert sizing["primary_capital_usd"] == 2000
    assert sizing["diagnostic_capitals_usd"] == [1000, 3000, 4000]
    assert "Only the complete 2000 USD replay" in sizing["capital_selection_rule"]
    assert "cannot veto, select, rescue" in sizing["capital_selection_rule"]
    assert "alter an admission" in sizing["capital_selection_rule"]
    assert "0.01*book_equity" in sizing["r_cash"]
    assert "loss_rate_per_raw_notional_j" in sizing["continuous_start"]
    assert "stressed_entry_fee_drag_per_unit_j" in sizing["continuous_start"]
    assert "every *_drag_per_unit is finite and nonnegative" in sizing["continuous_start"]
    assert "equal_raw_notional_start=min" in sizing["continuous_start"]
    assert "decrement exactly one MARKET_LOT_SIZE step" in sizing["step_floor_and_search"]
    assert "100000 decrements" in sizing["step_floor_and_search"]
    assert "<=0.01" in sizing["neutrality"]
    assert "first passing state" in sizing["neutrality"]
    assert "exact equality is not required" in sizing["neutrality"]
    assert "0<initial_pair_risk_cash<=requested_pair_risk_cash" in sizing["post_rounding_checks"]
    assert "symbol sorts lexicographically later" in sizing["post_rounding_checks"]
    assert "only from the 2000 USD primary replay" in sizing["primary_gate"]

    risk = contract["risk"]
    assert risk["max_concurrent_pairs"] == 3
    assert risk["max_open_risk_r"] == 3.0
    assert "defines requested and actual pair risk mechanically" in risk["pair_risk"]
    assert "aggregate stressed simultaneous-stop loss" in sizing["post_rounding_checks"]
    assert risk["capital_simulations_usd"] == [1000, 2000, 3000, 4000]

    regime = contract["regime"]
    assert set(regime) == {
        "stream", "fold_reset_and_warmup", "ema_formula", "as_of", "boundary"
    }
    assert regime["stream"].startswith("BTCUSDT 1h klines from the exact authority-v6 manifest")
    assert "first 200 binary64 closes" in regime["fold_reset_and_warmup"]
    assert "EMA200_d=(2/201)*close_d+(199/201)*EMA200_previous" in regime["ema_formula"]
    assert "close_d>=EMA200_d is BTC_ABOVE_OR_ON_EMA200" in regime["as_of"]
    assert "No EMA seed or state crosses" in regime["boundary"]

    costs = contract["costs"]
    assert costs["taker_fee_bps_per_fill_per_leg"] == 5.0
    assert costs["adverse_slippage_bps_per_fill_per_leg"] == 2.0
    assert costs["stress_adverse_slippage_bps_per_fill_per_leg"] == 10.0
    assert costs["fills_per_pair_round_trip"] == 4
    assert "fee_drag" in costs["pnl_rule"]
    assert "slippage_drag" in costs["pnl_rule"]
    assert "finite and nonnegative" in costs["pnl_rule"]
    assert "actual_net_cash=movement_cash+funding_cash-sum(fee_drag)-sum(slippage_drag)" in costs["pnl_rule"]
    assert "never encode a cost as signed negative drag" in costs["pnl_rule"]
    assert "signed funding_cash=" in costs["funding"]
    assert "funding credits positive and debits negative" in costs["funding"]
    assert "last verified 1h mark close strictly before" in costs["funding"]
    assert "0.10%" in costs["impact"]
    assert "never improves a fill" in costs["stress"]

    gates = "\n".join(contract["gates"]["forward_pass_all_required"])
    assert "at least 1000 closed paired attempts" in gates
    assert "median at least 30 closed pairs per UTC week" in gates
    assert "long-leg and short-leg" in gates
    assert "p-value<=0.05/258" in gates
    assert "strictly more than 55%" in gates
    assert "zero-trade month remains in the denominator" in gates
    assert "including atomic-failure rows" in gates
    assert "BTC_ABOVE_OR_ON_EMA200" in gates
    assert contract["gates"]["drawdown_tiers"]["reject"] == "max drawdown>60R"
    assert contract["gates"]["undefined_statistics"] == "FAIL_CLOSED"

    statistics = contract["statistics"]
    assert set(statistics) == {
        "ordered_unit", "statistic", "method", "expected_block_length_attempts", "resamples",
        "prng", "fixed_draw_stationary_resample", "null_seed", "ci_seed", "p_value",
        "confidence_interval",
    }
    assert statistics["expected_block_length_attempts"] == 21
    assert statistics["resamples"] == 10000
    assert statistics["prng"] == (
        "numpy 2.5.3 numpy.random.Generator(numpy.random.PCG64(seed)); "
        "Generator.random(size=(resamples,n,2),dtype=float64) in C order. PCG64, not "
        "PCG64DXSM or default_rng substitution, is mandatory."
    )
    assert "exactly resamples*n*2 uniforms" in statistics["fixed_draw_stationary_resample"]
    assert statistics["null_seed"] == 25820261107
    assert statistics["ci_seed"] == 25820261108
    assert "(10000+1), inclusive upper tail" in statistics["p_value"]
    assert "Atomic-failure verdict_R observations" in statistics["confidence_interval"]

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


def test_sizing_is_single_primary_deterministic_neutral_and_fail_closed():
    sizing = load_contract()["sizing"]
    assert sizing["primary_capital_usd"] == 2000
    assert set(sizing["diagnostic_capitals_usd"]) == {1000, 3000, 4000}
    assert "cannot veto, select, rescue" in sizing["capital_selection_rule"]
    assert "latest verified 1h mark close whose close_time<t" in sizing["book_equity"]
    assert "requested_pair_risk_cash=1.0*R_cash_at_attempt" in sizing["r_cash"]
    assert "floor(continuous_qty_j/stepSize_j)*stepSize_j" in sizing["step_floor_and_search"]
    assert "Never increase a leg" in sizing["step_floor_and_search"]
    assert "PAIR_SIZE_INFEASIBLE" in sizing["step_floor_and_search"]
    assert "abs(long_raw_notional-short_raw_notional)" in sizing["neutrality"]
    assert "existing_remaining_stressed_stop_loss" in sizing["post_rounding_checks"]


def test_atomic_one_leg_failure_is_a_conservative_loss_in_every_gate():
    atomic = load_contract()["execution"]["atomic_failure"]
    assert "exactly one ATOMIC_ONE_LEG_LOSS" in atomic
    assert "actual_net_cash=movement_cash+funding_cash-fee_drag-slippage_drag" in atomic
    assert "verdict_net_cash=min(actual_net_cash" in atomic
    assert "fee_drag=sum" in atomic
    assert "slippage_drag=sum" in atomic
    assert "execution_failure_penalty_drag=0.0" in atomic
    assert "actual_net_cash=movement_cash+funding_cash-fee_drag-slippage_drag" in atomic
    assert "conservative_adjustment_cash=verdict_net_cash-actual_net_cash" in atomic
    assert "atomic_trade_R=verdict_net_cash/atomic_initial_risk_cash" in atomic
    assert "included without exception" in atomic
    assert "bootstrap" in atomic
    assert "Zero accepted legs is a pre-entry indexed skip" in atomic
    assert "cannot exclude the row" in atomic


def test_rounded_neutrality_is_tolerance_based_not_exact_equality():
    contract = load_contract()
    weights = contract["signal"]["weights"]
    neutrality = contract["sizing"]["neutrality"]
    assert "target equal raw notional" in weights
    assert "need not be exactly equal" in weights
    assert "inclusive tolerance" in neutrality
    assert "exact equality is not required" in neutrality
    assert "abs(long_raw_notional-short_raw_notional)" in neutrality


def test_diagnostic_capitals_are_replay_local_and_cannot_veto_primary():
    contract = load_contract()
    selection = contract["sizing"]["capital_selection_rule"]
    feasibility = contract["risk"]["feasibility"]
    assert "Only the complete 2000 USD replay can admit/skip primary attempts" in selection
    assert "1000/3000/4000 USD replays are replay-local feasibility diagnostics" in selection
    assert "cannot veto, select, rescue" in selection
    assert "Only the 2000 USD replay's result admits or skips" in feasibility
    assert "skip only in that diagnostic" in feasibility
    assert "cannot veto, select, rescue or alter" in feasibility


def test_btc_regime_and_bootstrap_are_fully_reproducible():
    contract = load_contract()
    regime = contract["regime"]
    stats = contract["statistics"]
    assert "BTCUSDT 1h klines" in regime["stream"]
    assert "Reset at each development or forward-fold start" in regime["fold_reset_and_warmup"]
    assert "first 199 completed closes" in regime["fold_reset_and_warmup"]
    assert "The decision candle close is included once" in regime["ema_formula"]
    assert "Equality belongs only to ABOVE_OR_ON" in regime["as_of"]
    assert "Regime attribution is frozen at the pair's entry decision" in regime["boundary"]
    assert "numpy.random.PCG64(seed)" in stats["prng"]
    assert "size=(resamples,n,2)" in stats["prng"]
    assert "At j=0" in stats["fixed_draw_stationary_resample"]
    assert "u_restart<1/21" in stats["fixed_draw_stationary_resample"]
    assert "inclusive upper tail" in stats["p_value"]
    assert "method='linear'" in stats["confidence_interval"]


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
    assert "BTC_ABOVE_OR_ON_EMA200" in fwd
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
        ("authority_output_removed", lambda d: d["authority"]["required_future_outputs"].pop()),
        ("pin_rule_weakened", lambda d: d["authority"].update(future_pin_rule="pin anything later")),
        ("current_membership", lambda d: d["authority"].update(membership="use current top 40")),
        ("open_holdout", lambda d: d["splits"].update(holdout_policy="OPEN")),
        ("same_bar_entry", lambda d: d["execution"].update(entry="fill at decision close")),
        ("remove_stop", lambda d: d["execution"].update(protective_stop="none")),
        ("lower_tail", lambda d: d["signal"]["tails"].update(long="z>=+0.5")),
        ("adaptive_pair", lambda d: d["signal"].update(pairing="replace infeasible member")),
        ("primary_capital_changed", lambda d: d["sizing"].update(primary_capital_usd=4000)),
        ("sizing_search_changed", lambda d: d["sizing"].update(step_floor_and_search="find closest neutral quantities")),
        ("neutrality_relaxed", lambda d: d["sizing"].update(neutrality="allow 10 percent mismatch")),
        ("exact_equality_required", lambda d: d["signal"].update(weights="require exactly equal rounded notionals")),
        ("atomic_loss_excluded", lambda d: d["execution"].update(atomic_failure="drop partial admissions from gates")),
        ("signed_cost_rescue", lambda d: d["execution"].update(atomic_failure="actual_net_cash=movement_cash+funding_cash+fee_drag+slippage_drag")),
        ("signed_drag_cost_rescue", lambda d: d["costs"].update(pnl_rule="actual_net_cash=movement_cash+funding_cash+fee_drag+slippage_drag")),
        ("diagnostic_veto", lambda d: d["risk"].update(feasibility="all capital replays must pass or primary skips")),
        ("regime_current_btc", lambda d: d["regime"].update(stream="current BTC websocket")),
        ("regime_seed_crosses_fold", lambda d: d["regime"].update(boundary="carry EMA across folds")),
        ("bootstrap_default_rng", lambda d: d["statistics"].update(prng="numpy default_rng")),
        ("bootstrap_draws_changed", lambda d: d["statistics"].update(fixed_draw_stationary_resample="draw until enough blocks")),
        ("fee_zero", lambda d: d["costs"].update(taker_fee_bps_per_fill_per_leg=0.0)),
        ("stress_zero", lambda d: d["costs"].update(stress_adverse_slippage_bps_per_fill_per_leg=0.0)),
        ("double_slippage", lambda d: d["costs"].update(pnl_rule="movement from execution price and subtract slippage again")),
        ("current_volume", lambda d: d["costs"].update(impact="use fill-bar quote volume")),
        ("risk_expand", lambda d: d["risk"].update(max_open_risk_r=9.0)),
        ("sample_relax", lambda d: d["gates"]["forward_pass_all_required"].__setitem__(2, "at least 10 closed attempts")),
        ("positive_months_relaxed", lambda d: d["gates"]["forward_pass_all_required"].__setitem__(7, "report positive months")),
        ("regime_gate_relaxed", lambda d: d["gates"]["forward_pass_all_required"].__setitem__(8, "report regimes")),
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
