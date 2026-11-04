from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tools.research import strategy_correlation_cap as C


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[1]


def position(name: str, opened: int, closed: int | None, net: str | None,
             *, strategy: str = "s", direction: str = "LONG", issues=()) -> C.Position:
    return C.Position(
        ref=name, source_id=name, strategy_key=strategy, symbol=f"{name}USDT", direction=direction,
        opened=T0 + timedelta(seconds=opened),
        closed=None if closed is None else T0 + timedelta(seconds=closed),
        initial_risk_r=Decimal("1"), net_r=None if net is None else Decimal(net),
        entry_turnover=Decimal("100"), exit_turnover=Decimal("110"), quote_asset="USDT",
        complete=not issues and closed is not None and net is not None, issues=tuple(issues),
    )


def by_id(rows, variant):
    return next(row for row in rows if row["variant"] == variant)


def test_preregistered_variants_are_exact_and_ordered() -> None:
    assert C.VARIANTS == (
        ("baseline", None, None),
        ("cap_1_r2", 1, Decimal("2")),
        ("cap_2_r2", 2, Decimal("2")),
        ("cap_3_r2", 3, Decimal("2")),
    )


def test_blocked_entry_is_zero_and_never_occupies_a_future_slot() -> None:
    rows = (
        position("a", 0, 2, "-1"),
        position("blocked", 1, 8, "99"),
        position("later", 3, 4, "2"),
    )
    result = C.replay(rows, C.VARIANTS[1])
    assert result["sample_counts"] == {
        "candidate_entries": 3, "accepted_entries": 2, "blocked_entries": 1,
        "terminal_closed_positions": 2, "accepted_open_at_end": 0,
    }
    assert result["net_r"] == "1.000000"
    assert result["blocked_refs"] == ["blocked"]
    assert result["turnover_by_quote"] == {"USDT": "420.000000"}


def test_position_reason_has_frozen_priority_over_simultaneous_risk_hit() -> None:
    rows = (position("a", 0, 10, "1"), position("b", 1, 11, "1"), position("c", 2, 12, "1"))
    cap2 = C.replay(rows, C.VARIANTS[2])
    cap3 = C.replay(rows, C.VARIANTS[3])
    assert cap2["blocked_by_reason"] == {
        "same_strategy_direction_position_cap": 1,
        "strategy_open_risk_cap": 0,
        "both_conditions_true": 1,
    }
    assert cap3["blocked_by_reason"] == {
        "same_strategy_direction_position_cap": 0,
        "strategy_open_risk_cap": 1,
        "both_conditions_true": 0,
    }


def test_position_cap_is_strategy_and_direction_specific_but_risk_cap_is_strategy_wide() -> None:
    rows = (
        position("long", 0, 10, "1"),
        position("short", 1, 11, "1", direction="SHORT"),
        position("third", 2, 12, "1"),
        position("other", 3, 13, "1", strategy="other"),
    )
    cap2 = C.replay(rows, C.VARIANTS[2])
    assert cap2["sample_counts"]["accepted_entries"] == 3
    assert cap2["blocked_refs"] == ["third"]
    assert cap2["blocked_by_reason"]["strategy_open_risk_cap"] == 1


def test_metrics_use_terminal_exit_order_and_preserve_recorded_net() -> None:
    rows = (
        position("late_win", 0, 9, "3"),
        position("loss", 1, 3, "-2"),
        position("early_win", 2, 4, "1"),
    )
    result = C.replay(rows, C.VARIANTS[0])
    assert result["net_r"] == "2.000000"
    assert result["profit_factor"] == "2.000000"
    assert result["max_drawdown_r"] == "2.000000"
    assert result["turnover_by_quote"] == {"USDT": "630.000000"}


def test_seed_exposure_counts_for_admission_but_not_cluster_metrics() -> None:
    seed = position("seed", 0, 5, "100")
    first = position("first", 1, 6, "1")
    second = position("second", 2, 7, "1")
    result = C.replay((seed, first, second), C.VARIANTS[2],
                      candidate_refs={"first", "second"}, seed_refs={"seed"})
    assert result["sample_counts"]["candidate_entries"] == 2
    assert result["sample_counts"]["accepted_entries"] == 1
    assert result["net_r"] == "1.000000"


def test_seed_alone_fills_cap_and_is_still_published_as_peak_exposure() -> None:
    seed = position("seed", 0, 10, "100")
    first = position("first", 1, 11, "1")
    second = position("second", 2, 12, "1")
    result = C.replay((seed, first, second), C.VARIANTS[1],
                      candidate_refs={"first", "second"}, seed_refs={"seed"})
    assert result["sample_counts"] == {
        "candidate_entries": 2, "accepted_entries": 0, "blocked_entries": 2,
        "terminal_closed_positions": 0, "accepted_open_at_end": 0,
    }
    assert result["blocked_by_reason"]["same_strategy_direction_position_cap"] == 2
    assert result["peak_positions"] == {"s|LONG": 1}
    assert result["peak_open_r"] == {"s": "1.000000"}
    assert result["net_r"] == "0.000000" and result["turnover_by_quote"] == {}


def test_multiple_seeds_publish_their_combined_current_exposure() -> None:
    long = position("long", 0, 10, "1")
    short = position("short", 1, 11, "1", direction="SHORT")
    candidate = position("candidate", 2, 12, "1")
    result = C.replay((long, short, candidate), C.VARIANTS[3],
                      candidate_refs={"candidate"}, seed_refs={"long", "short"})
    assert result["peak_positions"] == {"s|LONG": 1, "s|SHORT": 1}
    assert result["peak_open_r"] == {"s": "2.000000"}
    assert result["blocked_by_reason"]["strategy_open_risk_cap"] == 1


def test_latest_cluster_uses_entry_timestamps_only() -> None:
    rows = (
        position("old1", 0, 1, "100", strategy="exp_vwaprev"),
        position("old2", 30, 40, "-100", strategy="exp_vwaprev"),
        position("new1", 1000, 1100, "-1", strategy="exp_vwaprev"),
        position("new2", 1015, 1110, "-1", strategy="exp_vwaprev"),
        position("new3", 1020, 1120, "-1", strategy="exp_vwaprev"),
    )
    assert [row.ref for row in C.latest_cluster(rows)] == ["new1", "new2", "new3"]


def test_equal_lifecycle_timestamps_refuse_instead_of_inventing_tie_order() -> None:
    rows = (position("a", 0, 3, "1"), position("b", 0, 4, "1"))
    with pytest.raises(C.StudyError, match="source.ambiguous_event_order"):
        C.events_of(rows)


def test_open_and_window_censored_positions_make_primary_metrics_null_but_keep_diagnostic() -> None:
    closed = position("closed", 0, 2, "1")
    opened = position("open", 3, None, None, issues=("right_censored_open",))
    result = C.replay((closed, opened), C.VARIANTS[0], through=T0 + timedelta(seconds=5))
    assert result["status"] == "insufficient"
    assert result["net_r"] is None and result["profit_factor"] is None and result["max_drawdown_r"] is None
    assert result["closed_only_diagnostic"]["net_r"] == "1.000000"
    assert result["closed_only_diagnostic"]["not_causal_when_status_insufficient"]

    future_close = position("future", 0, 10, "2")
    censored = C.replay((future_close,), C.VARIANTS[0], through=T0 + timedelta(seconds=5))
    assert "right_censored_at_window" in censored["insufficiency_reasons"]


def test_risk_change_after_entry_fails_closed() -> None:
    row = position("dca", 0, 4, "2", issues=("risk_changes_after_entry",))
    result = C.replay((row,), C.VARIANTS[0])
    assert result["status"] == "insufficient"
    assert result["net_r"] is None
    assert result["closed_only_diagnostic"]["net_r"] == "2.000000"


def test_closed_row_uses_measured_cost_and_funding_net() -> None:
    row = {
        "id": "s|BTCUSDT|LONG|1", "strategy_key": "s", "symbol": "BTCUSDT", "side": "LONG",
        "risk_usd": 10, "manual": False, "adds": 0, "dca": 0,
        "opened": "2026-01-01T00:00:00+00:00", "closed": "2026-01-01T01:00:01+00:00",
        "fills": [
            ["2026-01-01T00:00:00+00:00", "entry", 1, 100],
            ["2026-01-01T01:00:00+00:00", "stop", 1, 110],
        ],
        "pnl_gross": 10, "pnl_fill_adj": 0, "fees_actual": 1, "funding_actual": -0.5,
        "fill_px_partial": False, "fees_actual_partial": False,
        "fill_refs": [[0, "a"], [1, "b"]], "px_actual": {"a": 100, "b": 110},
    }
    normalized = C.closed_position(row)
    assert normalized.net_r == Decimal("0.85")
    assert normalized.entry_turnover == Decimal("100")
    assert normalized.exit_turnover == Decimal("110")
    assert normalized.closed == T0 + timedelta(hours=1)


def test_incomplete_measured_costs_are_never_zero_filled() -> None:
    row = {
        "id": "s|BTCUSDT|LONG|1", "strategy_key": "s", "symbol": "BTCUSDT", "side": "LONG",
        "risk_usd": 10, "manual": False, "adds": 0, "dca": 0,
        "opened": "2026-01-01T00:00:00+00:00", "closed": "2026-01-01T01:00:00+00:00",
        "fills": [
            ["2026-01-01T00:00:00+00:00", "entry", 1, 100],
            ["2026-01-01T01:00:00+00:00", "stop", 1, 90],
        ],
        "pnl_gross": -10, "fill_refs": [[0, "a"], [1, "b"]],
        "fill_px_partial": ["fill_px_pending"], "fees_actual": None,
    }
    normalized = C.closed_position(row)
    assert normalized.net_r is None and not normalized.complete
    assert any(issue.startswith("cost:") for issue in normalized.issues)


def test_canonical_output_is_ordered_finite_ascii() -> None:
    payload = C.canonical({"z": 1, "a": [True, None]})
    assert payload == b'{"a":[true,null],"z":1}\n'
    with pytest.raises(C.StudyError, match="result.noncanonical"):
        C.canonical({"bad": float("nan")})
    sealed = C.seal({"schema": "x", "value": 1})
    assert sealed["artifact_sha256"] == C.hashlib.sha256(
        C.canonical({"schema": "x", "value": 1})).hexdigest()
    with pytest.raises(C.StudyError, match="result.already_sealed"):
        C.seal(sealed)


def test_preregistration_is_valid_and_collector_has_no_runtime_network_or_paper_writes() -> None:
    prereg = json.loads((ROOT / C.PREREG).read_text(encoding="utf-8"))
    assert prereg["registered_before_result_access"] is True
    assert [row["id"] for row in prereg["variants"]] == [row[0] for row in C.VARIANTS]
    source = (ROOT / "tools" / "research" / "strategy_correlation_cap.py").read_text(encoding="utf-8")
    forbidden = ("requests", "urllib", "socket", "binance_client", "import engine", "paper_books")
    assert all(f"import {name}" not in source and f"from {name}" not in source for name in forbidden)


def test_committed_result_is_digest_bound_and_privacy_safe() -> None:
    path = ROOT / "research_evidence" / "runs" / "strategy-correlation-cap-shadow-v1" / "result.json"
    result = json.loads(path.read_text(encoding="ascii"))
    digest = result.pop("artifact_sha256")
    assert digest == C.hashlib.sha256(C.canonical(result)).hexdigest()
    wire = C.canonical(result)
    for forbidden in (b"source_id", b"orderId", b"clientOrderId", b"tradeId"):
        assert forbidden not in wire
