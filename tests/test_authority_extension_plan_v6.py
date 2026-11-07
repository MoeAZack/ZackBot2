"""Hostile metadata-only checks for the pre-download authority-v6 freeze.

These tests read contract and authority metadata only.  They never open a market archive,
API response, strategy identity, evaluator packet, return, signal or outcome row.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
EXPECTED_TOP = {
    "format", "authority_id", "version", "status", "frozen_at_cairo",
    "protected_base_commit", "roadmap_fit", "scope", "cutoff", "base_authorities",
    "allowed_sources", "acquisition_sequence", "pit_v9_generation", "walk_forward_splits",
    "outputs", "serialization_and_identity", "forbidden", "deferred",
}


def _load() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate(plan: dict) -> None:
    assert set(plan) == EXPECTED_TOP
    assert (plan["format"], plan["authority_id"], plan["version"], plan["status"]) == (
        "zb-binance-usdm-authority-extension-plan/1",
        "BINANCE-UM-PRECUTOFF-AUTHORITY-V6", 6, "FROZEN_METADATA_ONLY_NOT_ACQUIRED")
    assert plan["protected_base_commit"] == "6754a336652496952aaab82e42fbe9bd6171430a"
    assert plan["cutoff"]["exclusive_utc"] == "2026-11-05T00:00:00Z"
    assert plan["cutoff"]["exclusive_ms"] == 1793836800000
    scope = plan["scope"]
    assert scope == {
        "metadata_only": True,
        "network_or_download_performed": False,
        "archive_or_api_market_rows_read": False,
        "strategy_identity_created": False,
        "candidate_signal_or_outcome_evaluated": False,
        "evaluator_or_runner_authorized": False,
        "paper_or_mainnet_authority": False,
    }

    base = plan["base_authorities"]
    assert base["manifest_v5"]["byte_sha256"] == "3de00d9a9961d04938e71cd58fb042c28bfeea48519d91d8fbac2d12da99987a"
    assert base["manifest_v5"]["content_digest"] == "30c345c1261f3fe0d48bf4defc448c9139a073aecb2eee4155ecca1e4b64f1a8"
    assert base["pit_v8"]["byte_sha256"] == "3fc2c1696a4f9ac753a140394e6ca6ba723a9364046dbb94bcb6310c48b68d41"
    assert base["pit_v8"]["content_digest"] == "48f99b4309ff286f5541bd51199e42d4144dcc38e9dda7aa5bc3520b4d244bcd"
    assert base["filter_v3"]["byte_sha256"] == "e11b42efe63accc6a4546ac29507356fb53cdcc88c403aec031ffa48b27f1ba9"
    assert base["instrument_classes_v1"]["byte_sha256"] == "b5d3858e3b090f8e6b8a3769a23ba409949f6edfeeefe896baf4bc5d3d7f6198"
    assert base["instrument_classes_v1"]["content_digest"] == "48a9d32c4299495d0c58b175755d8ebdab562f8e18a05196a7f361fb6a986845"

    sources = plan["allowed_sources"]
    assert sources["binance_vision"]["scheme_host"] == "https://data.binance.vision"
    api = sources["binance_usdm_funding_api"]
    assert api["scheme_host_path"] == "https://fapi.binance.com/fapi/v1/fundingRate"
    assert api["required_query_keys_exactly"] == ["symbol", "startTime", "endTime", "limit"]
    assert api["limit"] == 1000
    assert api["first_start_ms"] == 1793491200000
    assert api["last_end_ms"] == 1793836799999 < plan["cutoff"]["exclusive_ms"]

    steps = plan["acquisition_sequence"]
    assert [row["step"] for row in steps] == list(range(1, 8))
    assert [row["id"] for row in steps] == [
        "PIT_DAILY_SEED", "GENERATE_PIT_V9_FIRST_PASS", "PIT_UNION_INTRADAY_AUTHORITY",
        "BUILD_MANIFEST_V6", "REGENERATE_PIT_V9_FINAL", "REBIND_FILTER_V4",
        "BUILD_COVERAGE_V4"]
    seed = steps[0]["datasets"]
    assert seed[0]["periods"] == ["2026-09", "2026-10"]
    assert seed[1]["dates"] == ["2026-11-01"]
    assert steps[1]["new_weekly_cells"] == [
        "2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z", "2026-10-19T00:00:00Z",
        "2026-10-26T00:00:00Z", "2026-11-02T00:00:00Z"]
    intraday = {row["series"]: row for row in steps[2]["datasets"]}
    for series in ("klines", "markPriceKlines"):
        assert intraday[series]["monthly_periods"] == ["2026-09", "2026-10"]
        assert intraday[series]["daily_dates"] == [
            "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]
    assert intraday["fundingRate"]["monthly_periods"] == ["2026-09", "2026-10"]
    assert intraday["fundingRate"]["api_dates"] == [
        "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]
    bindings = steps[5]["fold_bindings"]
    assert bindings == {
        "development_eligible_segment": "2024-12-13T04:44:48Z",
        "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
        "wf3": "2026-02-17T04:13:28Z", "sealed_holdout": "2026-02-17T04:13:28Z"}
    assert steps[6]["required_result"] == "ZERO_GUARANTEED_PERIOD_GAPS"
    assert plan["outputs"]["coverage_v4"]["required_guaranteed_period_gaps"] == 0
    assert all(value["status_before_future_build"] == "ABSENT_EXPECTED"
               for value in plan["outputs"].values())
    splits = plan["walk_forward_splits"]
    assert splits["development_test_range"] == "2024-10-14T00:00:00Z/2025-04-01T00:00:00Z"
    assert splits["development_filter_eligible_segment"] == "2024-12-14T00:00:00Z/2025-04-01T00:00:00Z"
    assert splits["forward_folds"] == [
        "2025-04-01T00:00:00Z/2025-10-01T00:00:00Z",
        "2025-10-01T00:00:00Z/2026-04-01T00:00:00Z",
        "2026-04-01T00:00:00Z/2026-09-28T00:00:00Z"]
    assert splits["sealed_holdout"] == "2026-09-28T00:00:00Z/2026-11-05T00:00:00Z"


def test_frozen_contract_is_exact_and_base_metadata_bytes_are_bound():
    plan = _load()
    _validate(plan)
    for item in plan["base_authorities"].values():
        assert _sha(ROOT / item["path"]) == item["byte_sha256"]


def test_splits_are_exactly_c256_and_holdout_ends_at_exclusive_cutoff():
    splits = _load()["walk_forward_splits"]
    assert splits["development_test_range"] == "2024-10-14T00:00:00Z/2025-04-01T00:00:00Z"
    assert splits["development_filter_eligible_segment"] == "2024-12-14T00:00:00Z/2025-04-01T00:00:00Z"
    assert splits["forward_folds"] == [
        "2025-04-01T00:00:00Z/2025-10-01T00:00:00Z",
        "2025-10-01T00:00:00Z/2026-04-01T00:00:00Z",
        "2026-04-01T00:00:00Z/2026-09-28T00:00:00Z"]
    assert splits["sealed_holdout"] == "2026-09-28T00:00:00Z/2026-11-05T00:00:00Z"


@pytest.mark.parametrize("mutate", [
    lambda p: p["cutoff"].update(exclusive_utc="2026-11-06T00:00:00Z"),
    lambda p: p["scope"].update(archive_or_api_market_rows_read=True),
    lambda p: p["scope"].update(strategy_identity_created=True),
    lambda p: p["allowed_sources"]["binance_usdm_funding_api"].update(limit=1500),
    lambda p: p["allowed_sources"]["binance_usdm_funding_api"].update(last_end_ms=1793836800000),
    lambda p: p["acquisition_sequence"][0]["datasets"][0]["periods"].append("2026-11"),
    lambda p: p["acquisition_sequence"][0]["datasets"][1]["dates"].append("2026-11-05"),
    lambda p: p["acquisition_sequence"][2]["datasets"][0]["daily_dates"].append("2026-11-05"),
    lambda p: p["acquisition_sequence"][5]["fold_bindings"].update(wf3="2026-11-01T00:00:00Z"),
    lambda p: p["acquisition_sequence"][6].update(required_result="ALLOW_GAPS"),
    lambda p: p["outputs"]["coverage_v4"].update(required_guaranteed_period_gaps=1),
    lambda p: p["walk_forward_splits"].update(sealed_holdout="2026-09-28T00:00:00Z/2026-11-06T00:00:00Z"),
])
def test_hostile_contract_mutation_is_rejected(mutate):
    plan = copy.deepcopy(_load())
    mutate(plan)
    with pytest.raises(AssertionError):
        _validate(plan)


def test_no_strategy_or_evaluation_artifact_is_in_scope():
    raw = PLAN_PATH.read_text(encoding="utf-8")
    plan = json.loads(raw)
    assert plan["scope"]["strategy_identity_created"] is False
    assert plan["scope"]["candidate_signal_or_outcome_evaluated"] is False
    assert plan["scope"]["evaluator_or_runner_authorized"] is False
    assert "all downloads, API calls, parsing of market rows" in plan["deferred"]
    output_paths = [row["path"] for row in plan["outputs"].values()]
    assert not any(token in path.lower() for path in output_paths
                   for token in ("candidate", "strategy_outcome", "evaluation", "packet", "identity"))


def test_output_paths_are_unique_and_contract_does_not_precreate_them():
    plan = _load()
    paths = [row["path"] for row in plan["outputs"].values()]
    assert len(paths) == len(set(paths)) == 4
    assert all(not (ROOT / path).exists() for path in paths)
