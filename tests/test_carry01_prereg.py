import json
import hashlib
from pathlib import Path

from tools.research import carry01 as C


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "research_evidence" / "prereg" / "carry01_cash_and_carry.v1.json"


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def digest(p):
    material = dict(p)
    material.pop("contract_digest_sha256")
    canonical = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_prereg_is_signed_for_history_but_forbids_activation_and_mainnet():
    p = load()
    assert p["format"] == "zb-carry01-prereg/1"
    assert p["signed"] is True
    assert p["status"].startswith("SIGNED")
    assert "authorized only for this digest" in p["historical_evaluation"]
    assert "forbidden" in p["paper"]["status"]
    assert p["mainnet"].startswith("forbidden")
    assert p["contract_digest_sha256"] == digest(p)


def test_prereg_matches_the_merged_replay_defaults():
    p, c = load(), C.CarryConfig()
    portfolio, costs = p["portfolio"], p["costs"]
    assert portfolio["start_equity"] == c.capital
    assert portfolio["spot_fraction"] == c.spot_fraction
    assert portfolio["target_futures_leverage"] == c.target_leverage
    assert portfolio["rebalance_band"] == [c.min_leverage, c.max_leverage]
    assert portfolio["maintenance_margin_rate_proxy"] == c.maintenance_margin_rate
    assert costs["spot_taker_bps_per_fill"] == c.spot_fee_bps
    assert costs["perpetual_taker_bps_per_fill"] == c.perp_fee_bps
    assert costs["spot_slippage_bps_per_fill"] == c.spot_slippage_bps
    assert costs["perpetual_slippage_bps_per_fill"] == c.perp_slippage_bps
    assert costs["hurdle_apr"] == c.cash_benchmark_apr + c.safety_margin_apr


def test_prereg_has_point_in_time_data_and_walk_forward_boundaries():
    p = load()
    assert p["data"]["sealed_development_end_exclusive"] == "2026-09-28T00:00:00Z"
    assert "300 seconds" in p["data"]["availability"]["funding"]
    assert len(p["windows"]["walk_forward"]) == 5
    assert p["windows"]["walk_forward"][-1][1] == p["data"]["sealed_development_end_exclusive"]
    assert p["trial_budget"]["primary"] == 1
    assert "cannot rescue" in p["trial_budget"]["multiplicity"]
    assert "daily" in p["data"]["series"]["spot"]
    assert "daily" in p["data"]["series"]["perpetual_mark"]
    assert "daily" in p["data"]["series"]["funding"]

    windows = [
        p["windows"]["warmup_observation_only"],
        p["windows"]["pre_walk_forward_observation_only"],
        *p["windows"]["walk_forward"],
    ]
    assert all(left[1] == right[0] for left, right in zip(windows, windows[1:]))


def test_prereg_requires_cost_stress_cash_hurdle_and_independent_reproduction():
    p = load()
    gates = p["gates"]
    assert "6%" in gates["combined"]
    assert "cost_x1.5" in gates["cost_stress"] and "cost_x2" in gates["cost_stress"]
    assert "independent reproduction" in gates["promotion"]
    assert {"funding_zero", "funding_sign_reversal", "rebalance_latency_1h"} <= set(p["costs"]["stress"])


def test_prereg_pins_numeric_gates_and_bootstrap_degrees_of_freedom():
    v = load()["gate_values"]
    assert v["walk_forward_fold_count"] == 5
    assert v["minimum_positive_cash_excess_folds"] == 4
    assert v["minimum_funding_events_per_primary_asset_per_fold"] == 300
    assert v["minimum_days_per_fold"] == 30
    assert v["annualized_hurdle"] == 0.06
    assert v["base_max_drawdown"] == 0.15
    assert v["stress_max_drawdown"] == 0.25
    assert v["maximum_recovery_days"] == 365
    assert v["maximum_single_asset_absolute_pnl_share"] == 0.75
    assert v["required_cost_stresses_positive"] == ["cost_x1.5", "cost_x2"]
    assert "mark-kline high" in v["liquidation"]
    assert v["bootstrap"] == {
        "input": "the hurdle uses chronologically concatenated daily portfolio returns; the bootstrap LCB uses chronologically concatenated daily arithmetic cash-excess returns on the same dates from the five fresh-start walk-forward folds",
        "combined_annualization": "compound only the included daily portfolio returns in chronological order and annualize with 365.25 divided by their included calendar-day span; cross-boundary intervals are excluded entirely and add no return or day",
        "method": "circular moving-block bootstrap",
        "block_days": 7,
        "resamples": 10000,
        "seed": 25701,
        "lower_percentile": 2.5,
        "required_lcb": 0.0,
    }


def test_prereg_pins_funding_price_weights_and_timestamp_rules():
    p = load()
    assert "strictly earlier" in p["costs"]["funding"]
    assert "rebalance the whole portfolio" in p["portfolio"]["weight_rule"]
    assert "funding timestamps are not forced to an 8h grid" in p["data"]["timestamps"]
    assert "13 digits (milliseconds) or 16 digits (microseconds)" in p["data"]["timestamps"]
    assert "integer floor division by 1000" in p["data"]["timestamps"]
    assert "no archive containing a row on or after the seal" in p["data"]["read_definition"]
    assert "no forward fill or synthetic repair" in p["data"]["missing"]
    assert "returns never bridge an incomplete hour" in p["data"]["missing"]
    assert "at least 99.0%" in p["data"]["coverage_gate"]
    assert "longer than 24 hours refuses evaluation" in p["data"]["coverage_gate"]
    assert "no older mark is substituted" in p["data"]["funding_missing_mark"]
    assert "does not count toward the minimum-funding-events gate" in p["data"]["funding_missing_mark"]
    assert "only exact monthly-ledger timestamps may be added" in p["data"]["daily_recovery"]
    assert "before any return, PnL, signal, score or selection metric" in p["data"]["daily_recovery"]
