import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT / "contracts" / "analog_stress_01" / "analog_stress_01_contract.v1.json"
)


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_analog_stress_has_no_trading_or_selection_authority():
    contract = _contract()

    assert contract["status"] == "DRAFT"
    assert all(value is False for value in contract["authority"].values())
    assert contract["implementation_boundary"]["new_files_only"] is True
    assert contract["implementation_boundary"]["runtime_files_edited"] is False
    assert contract["implementation_boundary"]["paper_state_touched"] is False


def test_analog_stress_freezes_cairo_dst_and_joint_blocks():
    contract = _contract()
    time = contract["time"]
    estimator = contract["estimator"]

    assert time["report_zone"] == "Africa/Cairo"
    assert time["hardcoded_offset_allowed"] is False
    assert time["dst_boundary_tests_required"] is True
    assert time["block_unit"] == "cairo_day"
    assert estimator["primary"] == "contiguous_cairo_day_block_bootstrap"
    assert estimator["iid_primary"] is False
    assert estimator["recompute_costs_per_path"] is True
    assert estimator["report_observed_separately"] is True


def test_analog_stress_refuses_incomplete_paths_and_reports_bounds():
    contract = _contract()

    assert "missing_minute_path" in contract["fail_closed"]
    assert "cost_recompute_failure" in contract["fail_closed"]
    assert "autocorrelated_block_vs_iid_difference" in contract["required_tests"]
    assert contract["result_packet"]["immutable"] is True
    assert contract["result_packet"]["single_blended_path_claim"] is False
    assert contract["paths"] == [
        "observed",
        "stop_first",
        "target_first",
        "gap_open",
    ]


def test_analog_stress_contract_freezes_sessions_blocks_and_paths():
    contract = _contract()
    time = contract["time"]
    estimator = contract["estimator"]
    paths = contract["path_selection"]

    assert time["session_boundaries_half_open"] == [
        "[00:00,06:00)",
        "[06:00,12:00)",
        "[12:00,18:00)",
        "[18:00,24:00)",
    ]
    assert time["cell_assignment_timestamp"] == "decision_timestamp"
    assert time["spanning_trade_reassignment"] is False
    assert estimator["block_length_cairo_days"] == 3
    assert estimator["edge_policy"] == "circular_moving_blocks"
    assert estimator["resamples"] == 2000
    assert estimator["seed"] == 20261102
    assert estimator["percentiles"] == ["0.025", "0.500", "0.975"]
    assert estimator["minimum_distinct_cairo_days"] == 30
    assert estimator["minimum_session_observations"] == 20
    assert estimator["minimum_session_distinct_days"] == 10
    assert paths["missing_or_impossible_fallback"] is False
