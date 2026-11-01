import datetime as dt
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "contracts" / "oi_reclaim_01" / "funding_extreme_reclaim.v1.json"


def prereg():
    return json.loads(PATH.read_text(encoding="utf-8"))


def ts(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_identity_and_authority_are_result_blind_and_default_off():
    p = prereg()
    assert p["format"] == "zb-falsify-prereg/1"
    assert p["candidate_id"] == "funding_extreme_reclaim.v1"
    assert p["status"] == "DRAFT - RESULT BLIND - NO EVALUATION AUTHORITY"
    assert p["authority"] == {
        "research_only": True, "paper": False, "mainnet": False,
        "holdout_open": False, "requires_codex_signature": True,
        "requires_independent_review": True,
    }
    assert p["identity"]["manifest_digest"] is None
    assert p["identity"]["accepted_commit"] is None


def test_exact_universe_and_no_silent_shrink():
    u = prereg()["universe"]
    assert len(u["symbols"]) == len(set(u["symbols"])) == 20
    assert u["symbols"] == sorted(u["symbols"])
    assert u["minimum_daily_breadth"] == 20
    assert u["silent_shrink_forbidden"] is True


def test_windows_are_contiguous_and_holdout_ends_at_cutoff():
    w = prereg()["window"]
    folds = [(ts(a), ts(b)) for a, b in w["validation_folds"]]
    assert ts(w["start"]) < ts(w["development_end"]) == folds[0][0]
    assert all(a < b for a, b in folds)
    assert all(folds[i][1] == folds[i + 1][0] for i in range(len(folds) - 1))
    holdout = tuple(map(ts, w["sealed_holdout"]))
    assert folds[-1][1] == holdout[0] < holdout[1]
    assert holdout[1] == ts(w["hard_cutoff_exclusive"])


def test_causal_timing_forbids_same_bar_and_missing_data_repairs():
    c = prereg()["causality"]
    assert c["decision_grid"] == "hourly at HH:05:00Z"
    assert c["oi_available_at"] == "period_end + 5 minutes"
    assert c["funding_available_at"] == "funding event timestamp + 5 minutes"
    assert c["price_available_at"] == "kline close timestamp + 1 minute"
    assert c["execution"].startswith("next 15m bar open")
    assert c["same_bar_fill"] is c["forward_fill"] is c["interpolation"] is False


def test_all_three_input_families_require_immutable_fail_closed_evidence():
    inputs = prereg()["inputs"]
    assert set(inputs) == {"oi", "funding", "price", "common_window", "fail_closed"}
    assert "manifest digest" in inputs["oi"]
    assert "immutable source manifest" in inputs["funding"]
    assert "immutable source manifest" in inputs["price"]
    assert "breadth falls below 20" in inputs["fail_closed"]


def test_finite_signal_costs_and_capacity_are_fixed():
    p = prereg()
    s, port, costs = p["signal"], p["portfolio"], p["costs"]
    assert (s["funding_lookback_events"], s["funding_extreme_quantile"]) == (270, 0.10)
    assert (s["oi_flush_window_minutes"], s["oi_reclaim_from_window_minimum"]) == (360, 0.005)
    assert port["max_concurrent_positions"] == 8
    assert port["risk_per_trade_equity_fraction"] == 0.005
    assert port["gross_leverage_cap"] == 2.0
    assert costs["fees_per_fill_bps"] == costs["base_slippage_per_fill_bps"] == 5.0
    assert costs["stress_multipliers"] == [1.5, 2.0]


def test_validation_precedes_single_holdout_open_and_failure_stops():
    p = prereg()
    v, h, stop = p["validation_gates"], p["holdout_gate"], p["hard_stop"]
    assert v["minimum_completed_trades_combined"] >= 150
    assert v["minimum_completed_trades_each_side"] >= 40
    assert v["combined_max_drawdown_lte"] == h["max_drawdown_lte"] == 0.35
    assert h["open_only_if_all_validation_gates_pass"] and h["one_open_only"]
    assert h["no_parameter_change_after_open"]
    assert "do not open holdout" in stop["before_holdout"]
    assert "new candidate id" in stop["after_failure"]


def test_multiple_testing_concentration_and_directional_fail_closed_rules_exist():
    p = prereg()
    assert p["claims"]["families"] == ["combined", "long", "short"]
    assert "Holm-Bonferroni" in p["claims"]["multiplicity"]
    v = p["validation_gates"]
    assert v["top_symbol_profit_share_lte"] == 0.30
    assert v["remove_best_symbol_net_return_gt"] == 0.0
    assert "failing side stays disabled" in v["directional_promotion"]
