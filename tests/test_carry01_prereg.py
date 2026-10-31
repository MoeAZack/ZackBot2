import json
from pathlib import Path

from tools.research import carry01 as C


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "research_evidence" / "prereg" / "carry01_cash_and_carry.v1.json"


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_prereg_is_draft_and_forbids_evaluation_activation_and_mainnet():
    p = load()
    assert p["format"] == "zb-carry01-prereg/1"
    assert p["signed"] is False
    assert p["status"].startswith("DRAFT")
    assert "forbidden" in p["paper"]["status"]
    assert p["mainnet"].startswith("forbidden")


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


def test_prereg_requires_cost_stress_cash_hurdle_and_independent_reproduction():
    p = load()
    gates = p["gates"]
    assert "6%" in gates["combined"]
    assert "cost_x1.5" in gates["cost_stress"] and "cost_x2" in gates["cost_stress"]
    assert "independent reproduction" in gates["promotion"]
    assert {"funding_zero", "funding_sign_reversal", "rebalance_latency_1h"} <= set(p["costs"]["stress"])
