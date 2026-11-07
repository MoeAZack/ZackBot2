"""Hostile synthetic-only tests for C258.

No test opens a market archive, outcome, candidate packet, evaluation identity,
or sealed-holdout artifact.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import inspect
import subprocess
from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C


def replace_dict(source, **changes):
    return {**source, **changes}


def tape(symbol_index: int, r24: float, *, start: int = 0, quote_volume: float = 1_000_000) -> list[C.Bar]:
    base = 100 + symbol_index
    rows = []
    for i in range(25):
        close = base if i == 0 else base * (1 + r24 * i / 24)
        rows.append(C.Bar(start + i * C.HOUR_MS, close, close + 1, close - 1, close, quote_volume))
    return rows


def leg(symbol: str, side: int, entry: str, stop: str, *, step: str = "0.001",
        min_qty: str = "0.001", max_qty: str = "1000", min_notional: str = "5") -> C.LegSpec:
    return C.LegSpec(symbol, side, Decimal(entry), Decimal(stop),
                     C.Filters(Decimal(step), Decimal(min_qty), Decimal(max_qty), Decimal(min_notional)))


def accepted_pair(long_symbol="LONG", short_symbol="SHORT", long_qty="1", short_qty="1",
                  long_entry="100", short_entry="100", stop_delta="2") -> C.AcceptedPair:
    long_leg = leg(long_symbol, 1, long_entry, str(Decimal(long_entry) - Decimal(stop_delta)))
    short_leg = leg(short_symbol, -1, short_entry, str(Decimal(short_entry) + Decimal(stop_delta)))
    lq, sq = Decimal(long_qty), Decimal(short_qty)
    risk = lq * C.stressed_loss_per_unit(long_leg) + sq * C.stressed_loss_per_unit(short_leg)
    sized = C.SizedPair(lq, sq, lq * Decimal(long_entry), sq * Decimal(short_entry),
                        risk, Decimal("20"), Decimal("2000"), 0)
    return C.AcceptedPair(long_leg, short_leg, sized)


def fill_bar_authority(fills):
    baselines = {fill.symbol: fill.reference_price for fill in fills if fill.phase == "entry"}
    result = {}
    for fill in fills:
        rows = result.setdefault(fill.symbol, {})
        prior_ms = fill.timestamp_ms - C.HOUR_MS
        baseline = baselines[fill.symbol]
        rows.setdefault(prior_ms, C.Bar(prior_ms, baseline, baseline + .01,
                                       baseline - .01, baseline, fill.prior_quote_volume))
        rows[fill.timestamp_ms] = C.Bar(fill.timestamp_ms, fill.reference_price,
                                        fill.reference_price + .01,
                                        max(.0001, fill.reference_price - .01), fill.reference_price,
                                        fill.prior_quote_volume)
    for symbol, rows in result.items():
        baseline = baselines[symbol]
        first, last = min(rows), max(max(rows), min(rows)+8*C.HOUR_MS)
        for timestamp in range(first, last+C.HOUR_MS, C.HOUR_MS):
            rows.setdefault(timestamp, C.Bar(timestamp, baseline, baseline+.01,
                                              baseline-.01, baseline, 1_000_000_000.0))
    return {symbol: tuple(rows[key] for key in sorted(rows)) for symbol, rows in result.items()}


def attempt(identifier: str, r_value: float, *, regime: str = "BTC_ABOVE_OR_ON_EMA200") -> C.ClosedAttempt:
    long_symbol, short_symbol = f"L{identifier}", f"S{identifier}"
    fills = [
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol=long_symbol, side=1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol=short_symbol, side=-1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol=long_symbol, side=-1, quantity=1,
                    reference_price=100 + 0.7 * r_value, phase="exit", movement_from=100),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol=short_symbol, side=1, quantity=1,
                    reference_price=100 - 0.3 * r_value, phase="exit", movement_from=100),
    ]
    return C.close_attempt(attempt_id=identifier, entry_ms=C.HOUR_MS, close_ms=7*C.HOUR_MS,
                           long_symbol=long_symbol, short_symbol=short_symbol, regime=regime,
                           fills=fills, symbol_bars=fill_bar_authority(fills),
                           accepted_pair=accepted_pair(long_symbol, short_symbol,
                                                                   stop_delta="0.1"))


def marks_for_attempts(rows, *, long_price=100.0, short_price=100.0):
    return tuple(C.PortfolioMark(timestamp, leg.symbol,
                                 long_price if leg.position_side == 1 else short_price)
                 for row in rows for leg in row.legs
                 for timestamp in range(row.entry_ms, row.close_ms + C.HOUR_MS, C.HOUR_MS))


def test_contract_is_byte_bound_default_off_and_real_loader_is_locked():
    contract = C.load_frozen_contract()
    assert contract["candidate_id"] == 258
    assert contract["default_off"] is True
    assert contract["paper_admission"]["paper_testnet_only"] is True
    with pytest.raises(C.EvaluationLocked, match="no loader was invoked"):
        C.assert_real_evaluation_authorized()


def test_authority_gate_refuses_before_loader_and_checks_every_exact_pin(tmp_path, monkeypatch):
    called = False

    def loader(_):
        nonlocal called
        called = True

    pins = C.AuthorityPins("e" * 40, *[
        C.ArtifactPin(path, "0" * 64, "1" * 64) for path in C.EXPECTED_AUTHORITY_PATHS.values()
    ])
    with pytest.raises(C.IncompleteAuthority, match="Git authority resolution failed"):
        C.run_with_authority(pins, loader)
    assert called is False


def test_authority_api_has_no_caller_ref_or_ancestry_controls_and_schema_decoys_fail(tmp_path, monkeypatch):
    called = False

    def loader(_):
        nonlocal called
        called = True

    parameters = inspect.signature(C.run_with_authority).parameters
    assert "required_ancestor" not in parameters and "protected_ref" not in parameters
    assert "protected_ref" not in {field.name for field in fields(C.AuthorityPins)}
    with pytest.raises(C.IncompleteAuthority, match="absent/empty"):
        C._validate_full_authority_schema({"weekly_cells": []}, {"fold_bindings": {}})
    cells = [{"monday_ms": timestamp, "symbols": ["AAAUSDT"]}
             for timestamp in C._expected_weekly_cells()]
    with pytest.raises(C.IncompleteAuthority, match="fold bindings"):
        C._validate_full_authority_schema({"weekly_cells": cells}, {
            "fold_bindings": {"wf1": "decoy"}})
    with pytest.raises(C.IncompleteAuthority, match="exactly cover"):
        C._validate_full_authority_schema({"weekly_cells": cells[:-1]}, {
            "fold_bindings": {"development_eligible_segment": "2024-12-13T04:44:48Z",
                              "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
                              "wf3": "2026-02-17T04:13:28Z",
                              "sealed_holdout": "2026-02-17T04:13:28Z"}})
    with pytest.raises(C.IncompleteAuthority, match="exactly cover"):
        C._validate_full_authority_schema({"weekly_cells": cells + [{
            "monday_ms": cells[-1]["monday_ms"] + 7*24*C.HOUR_MS, "symbols": ["DECOY"]}]}, {
            "fold_bindings": {"development_eligible_segment": "2024-12-13T04:44:48Z",
                              "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
                              "wf3": "2026-02-17T04:13:28Z",
                              "sealed_holdout": "2026-02-17T04:13:28Z"}})

    def materialize(name, body):
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        doc = {**body, "content_digest": digest}
        path_string = C.EXPECTED_AUTHORITY_PATHS[name]
        path = tmp_path / path_string
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
        raw = gzip.compress(payload, mtime=0) if path.suffix == ".gz" else payload
        path.write_bytes(raw)
        return C.ArtifactPin(path_string, hashlib.sha256(raw).hexdigest(), digest)

    manifest = materialize("manifest_v6", {"format": "zb-binance-um-archive-manifest/6",
                           "cutoff_exclusive_utc": "2026-11-05T00:00:00Z",
                           "cutoff_exclusive_ms": 1793836800000, "timezone": "UTC"})
    pit = materialize("pit_v9", {"format": "zb-pit-universe/2",
        "weekly_cells": [{"monday_ms": timestamp, "symbols": ["AAAUSDT"]}
                         for timestamp in C._expected_weekly_cells()], "bindings": {
        "manifest_v6_byte_sha256": manifest.byte_sha256,
        "manifest_v6_content_digest": manifest.content_digest}})
    filters = materialize("filter_v4", {
        "authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", "version": 4,
        "fold_bindings": {"development_eligible_segment": "2024-12-13T04:44:48Z",
                          "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
                          "wf3": "2026-02-17T04:13:28Z", "sealed_holdout": "2026-02-17T04:13:28Z"},
        "bindings": {"pit_v9_byte_sha256": pit.byte_sha256, "pit_v9_content_digest": pit.content_digest}})
    coverage = materialize("coverage_v4", {
        "format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
        "required_result": "ZERO_GUARANTEED_PERIOD_GAPS", "gaps": [],
        "required_streams": [{"series": "klines", "interval": "1d"},
                             {"series": "klines", "interval": "1h"},
                             {"series": "markPriceKlines", "interval": "1h"},
                             {"series": "fundingRate", "interval": None}],
        "coverage_start_ms": 1728864000000, "cutoff_exclusive_ms": 1793836800000,
        "guaranteed_period_gaps": 0, "result": "ZERO_GUARANTEED_PERIOD_GAPS",
        "guaranteed_period_gaps_by_stream": {stream: 0 for stream in
            ("klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null")},
        "bindings": {key: value for name, pin in (("manifest_v6", manifest), ("pit_v9", pit),
                                                    ("filter_v4", filters))
                     for key, value in ((f"{name}_byte_sha256", pin.byte_sha256),
                                        (f"{name}_content_digest", pin.content_digest))}})
    for source in (C.CONTRACT_PATH, C.PLAN_PATH):
        destination = tmp_path / source.relative_to(C.ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "synthetic@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Synthetic Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "synthetic authority"], cwd=tmp_path, check=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    monkeypatch.setattr(C, "AUTHORITY_EXPECTED_ANCESTORS", (head,))
    monkeypatch.setattr(C, "AUTHORITY_PROTECTED_REF", "refs/heads/master")
    good = C.AuthorityPins(head, manifest, pit, filters, coverage)
    assert C.run_with_authority(good, lambda authorities: set(authorities), root=tmp_path) == set(C.EXPECTED_AUTHORITY_PATHS)
    # Working-tree substitution is irrelevant: validation rereads the exact protected tree blob.
    (tmp_path / coverage.path).write_text("{}", encoding="utf-8")
    assert C.run_with_authority(good, lambda authorities: set(authorities), root=tmp_path) == set(C.EXPECTED_AUTHORITY_PATHS)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "protected ref drift"], cwd=tmp_path, check=True)
    drift = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    with pytest.raises(C.IncompleteAuthority, match="exact equality"):
        C.run_with_authority(good, loader, root=tmp_path)
    assert called is False
    subprocess.run(["git", "update-ref", "refs/heads/master", head], cwd=tmp_path, check=True)
    with pytest.raises(C.IncompleteAuthority, match="Git authority resolution failed"):
        monkeypatch.setattr(C, "AUTHORITY_EXPECTED_ANCESTORS", ("f" * 40,))
        C.run_with_authority(good, loader, root=tmp_path)
    assert called is False
    with pytest.raises(C.IncompleteAuthority, match="cross-binding"):
        C._require_binding({"bindings": {"manifest_v6_content_digest": "wrong"}},
                           "manifest_v6_content_digest", manifest.content_digest)
    bad = replace(good, coverage_v4=replace(good.coverage_v4, content_digest="f" * 64))
    called = False
    with pytest.raises(C.IncompleteAuthority, match="content digest mismatch"):
        monkeypatch.setattr(C, "AUTHORITY_EXPECTED_ANCESTORS", (head,))
        C.run_with_authority(bad, loader, root=tmp_path)
    assert called is False


def test_authority_coverage_and_manifest_decoys_fail_exact_schema():
    manifest = {"cutoff_exclusive_utc": "2026-11-05T00:00:00Z",
                "cutoff_exclusive_ms": 1793836800000, "timezone": "UTC"}
    coverage = {"format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
                "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
                "result": "ZERO_GUARANTEED_PERIOD_GAPS", "gaps": [],
                "required_streams": [{"series": "klines", "interval": "1d"},
                                     {"series": "klines", "interval": "1h"},
                                     {"series": "markPriceKlines", "interval": "1h"},
                                     {"series": "fundingRate", "interval": None}],
                "coverage_start_ms": 1728864000000, "cutoff_exclusive_ms": 1793836800000,
                "guaranteed_period_gaps": 0,
                "guaranteed_period_gaps_by_stream": {stream: 0 for stream in
                    ("klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null")}}
    C._validate_coverage_and_manifest(manifest, coverage)
    for hostile in (replace_dict(coverage, required_result="PASS"),
                    replace_dict(coverage, gaps=[{"decoy": True}]),
                    replace_dict(coverage, required_streams=coverage["required_streams"] + [
                        {"series": "klines", "interval": "4h"}]),
                    replace_dict(coverage, guaranteed_period_gaps_by_stream={"klines:1h": 0})):
        with pytest.raises(C.IncompleteAuthority, match="ZERO_GUARANTEED"):
            C._validate_coverage_and_manifest(manifest, hostile)
    for hostile in (replace_dict(manifest, cutoff_exclusive_ms=1793836800001),
                    replace_dict(manifest, timezone="Etc/UTC")):
        with pytest.raises(C.IncompleteAuthority, match="cutoff"):
            C._validate_coverage_and_manifest(hostile, coverage)


def test_cross_section_uses_exact_24h_robust_score_tails_and_indexed_pairing():
    returns = [-0.30, -0.25, -0.20, -0.15, -0.02, -0.015, -0.01, -0.005,
               0, 0.002, 0.004, 0.006, 0.008, 0.01, 0.012, 0.015,
               0.02, 0.03, 0.15, 0.20, 0.25]
    symbols = [f"S{i:02d}" for i in range(len(returns))]
    bars = {symbol: tape(i, value) for i, (symbol, value) in enumerate(zip(symbols, returns))}
    decision = 25 * C.HOUR_MS
    result = C.form_cross_section(bars, decision, symbols)
    assert result is not None
    assert len(result.scores) == len(symbols)
    assert 1 <= len(result.pairs) <= 3
    assert [pair.index for pair in result.pairs] == list(range(1, len(result.pairs) + 1))
    assert all(result.pairs[i].long.z >= result.pairs[min(i + 1, len(result.pairs) - 1)].long.z
               for i in range(len(result.pairs)))
    assert all(pair.long.z >= 1.5 and pair.short.z <= -1.5 for pair in result.pairs)

    # A missing d-24h bar excludes one symbol; it is never replaced by its nearest earlier close.
    bars[symbols[0]] = bars[symbols[0]][1:]
    result2 = C.form_cross_section(bars, decision, symbols)
    assert result2 is not None and symbols[0] not in {row.symbol for row in result2.scores}


def test_pit_membership_uses_latest_past_cell_preserves_order_and_filter_never_promotes():
    cells = [
        {"monday_ms": 100, "symbols": ["B", "A", "C"]},
        {"monday_ms": 200, "symbols": ["D", "C", "A"]},
        {"monday_ms": 300, "symbols": ["FUTURE"]},
    ]
    pit = C.latest_pit_membership(cells, 250)
    assert pit == ("D", "C", "A")
    assert C.apply_filter_snapshot(pit, ["A", "C", "NOT_IN_PIT"]) == ("C", "A")
    with pytest.raises(C.IncompleteAuthority, match="duplicate PIT"):
        C.latest_pit_membership(cells + [{"monday_ms": 200, "symbols": ["X"]}], 250)


def test_cross_section_fails_closed_under_16_or_zero_mad_and_orders_ties_by_symbol():
    decision = 25 * C.HOUR_MS
    identical = {f"S{i:02d}": tape(i, 0.01) for i in range(16)}
    assert C.form_cross_section(identical, decision, list(identical)) is None
    short = dict(list(identical.items())[:15])
    assert C.form_cross_section(short, decision, list(short)) is None

    returns = [-0.2, -0.2, -0.1, -0.08, -0.04, -0.03, -0.02, -0.01,
               0.01, 0.02, 0.03, 0.04, 0.08, 0.1, 0.2, 0.2]
    symbols = list(reversed([f"S{i:02d}" for i in range(16)]))
    result = C.form_cross_section(
        {symbol: tape(i, value) for i, (symbol, value) in enumerate(zip(symbols, returns))},
        decision, symbols)
    assert result is not None
    long_ties = [row.symbol for row in sorted((r for r in result.scores if r.r24 == 0.2),
                                              key=lambda r: (-r.z, r.symbol))]
    assert long_ties == sorted(long_ties)


def test_atr_wilder_is_contiguous_and_ema_resets_on_gap_with_equality_above():
    rows = [C.Bar(i * C.HOUR_MS, 100, 102, 98, 100, 1_000_000) for i in range(25)]
    assert C.atr24_wilder(rows, 25 * C.HOUR_MS) == 4
    with pytest.raises(C.IncompleteAuthority, match="contiguous"):
        C.atr24_wilder(rows[:12] + rows[13:], 25 * C.HOUR_MS)

    state = C.EMA200State()
    assert all(state.update(C.Bar(i * C.HOUR_MS, 100, 101, 99, 100, 1_000_000)) is None
               for i in range(199))
    assert state.update(C.Bar(199 * C.HOUR_MS, 100, 101, 99, 100, 1_000_000)) == "BTC_ABOVE_OR_ON_EMA200"
    assert state.update(C.Bar(201 * C.HOUR_MS, 90, 91, 89, 90, 1_000_000)) is None
    assert state.ema is None and len(state.closes) == 1


def test_next_open_requires_exact_boundary_and_continuity_never_bridges():
    rows = [C.Bar(i * C.HOUR_MS, 100, 101, 99, 100, 1_000_000) for i in range(4)]
    assert C.next_open(rows, C.HOUR_MS).open_ms == 2 * C.HOUR_MS
    with pytest.raises(C.IncompleteAuthority, match="exact next"):
        C.next_open([rows[0], rows[1], rows[3]], C.HOUR_MS)
    with pytest.raises(C.IncompleteAuthority, match="canonical"):
        C.next_open(rows, C.HOUR_MS + 1)


def test_decimal_sizing_is_one_percent_equal_notional_floor_only_and_neutral():
    sized = C.size_pair(leg("AAA", 1, "100", "98"), leg("BBB", -1, "50", "51"), book_equity=2000)
    assert sized is not None
    assert sized.requested_pair_risk_cash == Decimal("20.00")
    assert 0 < sized.initial_pair_risk_cash <= sized.requested_pair_risk_cash
    assert sized.gross <= Decimal("1000")
    assert abs(sized.long_raw_notional - sized.short_raw_notional) / max(
        sized.long_raw_notional, sized.short_raw_notional) <= Decimal("0.01")
    assert sized.long_qty % Decimal("0.001") == 0 and sized.short_qty % Decimal("0.001") == 0

    # No upward/alternative search can rescue a floor below minQty/minNotional.
    infeasible = C.size_pair(
        leg("AAA", 1, "100", "98", step="1", min_qty="10", min_notional="1000"),
        leg("BBB", -1, "100", "102", step="1", min_qty="10", min_notional="1000"),
        book_equity=1000)
    assert infeasible is None


def test_stop_gap_precedes_range_fixed_exit_and_impact_never_uses_fill_bar_volume():
    gap = C.Bar(7 * C.HOUR_MS, 90, 95, 85, 92, 1_000_000)
    assert C.stop_reference(position_side=1, entry_reference=100, atr24=2, bar=gap) == 90
    touch = C.Bar(7 * C.HOUR_MS, 100, 103, 95, 99, 1_000_000)
    assert C.stop_reference(position_side=1, entry_reference=100, atr24=2, bar=touch) == 96
    rows = [C.Bar(i * C.HOUR_MS, 100, 101, 99, 100, 1_000_000) for i in range(8)]
    marks = [C.MarkClose(i * C.HOUR_MS, 100) for i in range(7)]
    assert C.fixed_exit_open(rows, C.HOUR_MS, marks=marks, funding_points=[],
                             symbol="AAA").open_ms == 7 * C.HOUR_MS
    bridged = [rows[0], rows[1], *rows[3:], C.Bar(20 * C.HOUR_MS, 100, 101, 99, 100, 1_000_000)]
    with pytest.raises(C.IncompleteEpisode, match="contiguous"):
        C.fixed_exit_open(bridged, C.HOUR_MS, marks=marks, funding_points=[], symbol="AAA")
    long_rows = [C.Bar(i * C.HOUR_MS, 100, 101, 99, 100, 1_000_000) for i in range(7, 14)]
    long_marks = [C.MarkClose(i * C.HOUR_MS, 100) for i in range(6, 13)]
    with pytest.raises(C.IncompleteEpisode, match="funding authority"):
        C.fixed_exit_open(long_rows, 7 * C.HOUR_MS, marks=long_marks,
                          funding_points=[], symbol="AAA")
    assert C.impact_ratio(1, 100, 200_000) == pytest.approx(0.0005)
    with pytest.raises(C.IncompleteAuthority, match="impact authority"):
        C.impact_ratio(1, 100, 0)


def test_cost_signs_funding_and_atomic_one_leg_verdict_cannot_be_rescued():
    entry_ms, close_ms = 8 * C.HOUR_MS, 9 * C.HOUR_MS
    entry = C.make_fill(timestamp_ms=entry_ms, symbol="AAA", side=1, quantity=1, reference_price=100,
                        phase="entry", movement_from=None)
    cleanup = C.make_fill(timestamp_ms=close_ms, symbol="AAA", side=-1, quantity=1, reference_price=120,
                          phase="cleanup", movement_from=100)
    assert entry.fee_drag >= 0 and entry.slippage_drag >= 0
    assert cleanup.movement_cash == 20
    funding = C.signed_funding_cash(position_side=1, quantity=1, causal_mark=110, funding_rate=-0.001)
    assert funding > 0
    stressed = C.signed_funding_cash(position_side=1, quantity=1, causal_mark=110,
                                     funding_rate=-0.001, stress=True)
    assert stressed == pytest.approx(funding / 1.5)
    fp = C.FundingPoint("AAA", entry_ms, -0.001)
    bars = fill_bar_authority([entry, cleanup])
    closed = C.close_attempt(attempt_id="a", entry_ms=entry_ms, close_ms=close_ms, long_symbol="AAA",
                             short_symbol="BBB", regime="BTC_ABOVE_OR_ON_EMA200",
                             fills=[entry, cleanup], symbol_bars=bars,
                             accepted_atomic_leg=leg("AAA", 1, "100", "98"),
                             funding_points=[fp], funding_authority=[fp],
                             funding_marks={"AAA": [C.MarkClose(entry_ms-C.HOUR_MS, 110)]},
                             atomic_one_leg=True)
    assert closed.actual_net_cash > 0
    assert closed.verdict_net_cash == pytest.approx(-(closed.fee_drag + closed.slippage_drag))
    assert closed.verdict_net_cash <= 0 and closed.conservative_adjustment_cash <= 0
    assert closed.verdict_R == pytest.approx(closed.verdict_net_cash / closed.initial_risk_cash)

    atomic = C.account_atomic_one_leg(
        attempt_id="atomic", expected_long_symbol="AAA", expected_short_symbol="BBB",
        accepted_leg=leg("AAA", 1, "100", "98"),
        fills=[entry, cleanup], symbol_bars=bars, funding_points=[fp], funding_authority=[fp],
        funding_marks={"AAA": [C.MarkClose(entry_ms-C.HOUR_MS, 110)]},
        regime="BTC_ABOVE_OR_ON_EMA200")
    assert atomic.atomic_one_leg_loss and atomic.initial_risk_cash > 2
    same_open = replace(cleanup, timestamp_ms=entry_ms)
    with pytest.raises(C.IncompleteEpisode, match="after the entry"):
        C.account_atomic_one_leg(
            attempt_id="atomic", expected_long_symbol="AAA", expected_short_symbol="BBB",
            accepted_leg=leg("AAA", 1, "100", "98"),
            fills=[entry, same_open], symbol_bars=fill_bar_authority([entry, same_open]),
            regime="BTC_ABOVE_OR_ON_EMA200")
    partial = C.make_fill(timestamp_ms=close_ms, symbol="AAA", side=-1, quantity=0.1,
                          reference_price=120, phase="cleanup", movement_from=100)
    with pytest.raises(C.IncompleteEpisode, match="conserve|deterministic lifecycle"):
        C.account_atomic_one_leg(
            attempt_id="partial", expected_long_symbol="AAA", expected_short_symbol="BBB",
            accepted_leg=leg("AAA", 1, "100", "98"),
            fills=[entry, partial], symbol_bars=fill_bar_authority([entry, partial]),
            funding_points=[fp], funding_authority=[fp],
            funding_marks={"AAA": [C.MarkClose(entry_ms-C.HOUR_MS, 110)]},
            regime="BTC_ABOVE_OR_ON_EMA200")


def test_normal_pair_binds_exact_sides_quantities_and_full_flat_lifecycle():
    fills = [
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol="LONG", side=1, quantity=1, reference_price=100,
                    phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol="SHORT", side=-1, quantity=2, reference_price=50,
                    phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="LONG", side=-1, quantity=1, reference_price=101,
                    phase="exit", movement_from=100),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="SHORT", side=1, quantity=2, reference_price=49,
                    phase="exit", movement_from=50),
    ]
    closed = C.close_attempt(
        attempt_id="normal", entry_ms=C.HOUR_MS, close_ms=7*C.HOUR_MS,
        long_symbol="LONG", short_symbol="SHORT",
        regime="BTC_ABOVE_OR_ON_EMA200", fills=fills, symbol_bars=fill_bar_authority(fills),
        accepted_pair=accepted_pair(short_qty="2", short_entry="50"))
    assert len(closed.lifecycle_identity) == 4
    with pytest.raises(C.IncompleteEpisode, match="conserve|deterministic lifecycle"):
        partial_long = C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="LONG", side=-1, quantity=0.1,
                                   reference_price=101, phase="exit", movement_from=100)
        C.close_attempt(
            attempt_id="partial", entry_ms=C.HOUR_MS, close_ms=7*C.HOUR_MS,
            long_symbol="LONG", short_symbol="SHORT",
            regime="BTC_ABOVE_OR_ON_EMA200", fills=fills[:2] + [partial_long, fills[3]],
            symbol_bars=fill_bar_authority(fills[:2] + [partial_long, fills[3]]),
            accepted_pair=accepted_pair(short_qty="2", short_entry="50"))
    with pytest.raises(C.IncompleteEpisode, match="side|deterministic lifecycle"):
        wrong_side = C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="LONG", side=1, quantity=1,
                                 reference_price=101, phase="exit", movement_from=100)
        C.close_attempt(
            attempt_id="wrong-side", entry_ms=C.HOUR_MS, close_ms=7*C.HOUR_MS,
            long_symbol="LONG", short_symbol="SHORT",
            regime="BTC_ABOVE_OR_ON_EMA200", fills=fills[:2] + [wrong_side, fills[3]],
            symbol_bars=fill_bar_authority(fills[:2] + [wrong_side, fills[3]]),
            accepted_pair=accepted_pair(short_qty="2", short_entry="50"))
    with pytest.raises(C.IncompleteEpisode, match="declared pair"):
        C.account_atomic_one_leg(
            attempt_id="decoy-leg", expected_long_symbol="LONG", expected_short_symbol="SHORT",
            accepted_leg=leg("DECOY", 1, "100", "98"),
            fills=[replace(fills[0], symbol="DECOY"),
                   replace(fills[2], symbol="DECOY", phase="cleanup")],
            symbol_bars=fill_bar_authority([replace(fills[0], symbol="DECOY"),
                                            replace(fills[2], symbol="DECOY", phase="cleanup")]),
            regime="BTC_ABOVE_OR_ON_EMA200")
    assert "accepted_quantities" not in inspect.signature(C.close_attempt).parameters


def test_lifecycle_replay_derives_first_stop_and_immediate_mate_cleanup():
    entry_ms = C.HOUR_MS
    stop_ms = 4 * C.HOUR_MS
    pair = accepted_pair(stop_delta="2")

    def bars(symbol, *, stop_touch=False, due_open=100.0):
        rows = []
        for timestamp in range(0, 9 * C.HOUR_MS, C.HOUR_MS):
            open_price = due_open if timestamp == 7 * C.HOUR_MS else 100.0
            low = 97.0 if stop_touch and timestamp == 3 * C.HOUR_MS else open_price - .01
            rows.append(C.Bar(timestamp, open_price, open_price + .01, low,
                              open_price, 1_000_000_000.0))
        return tuple(rows)

    authority = {"LONG": bars("LONG", stop_touch=True),
                 "SHORT": bars("SHORT", due_open=95.0)}
    entries = [
        C.make_fill(timestamp_ms=entry_ms, symbol="LONG", side=1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=entry_ms, symbol="SHORT", side=-1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
    ]
    stop = C.make_fill(timestamp_ms=stop_ms, symbol="LONG", side=-1, quantity=1,
                       reference_price=98, phase="stop", movement_from=100)
    cleanup = C.make_fill(timestamp_ms=stop_ms, symbol="SHORT", side=1, quantity=1,
                          reference_price=100, phase="cleanup", movement_from=100)
    closed = C.close_attempt(
        attempt_id="derived-stop-cleanup", entry_ms=entry_ms, close_ms=stop_ms,
        long_symbol="LONG", short_symbol="SHORT", regime="BTC_ABOVE_OR_ON_EMA200",
        fills=entries + [stop, cleanup], symbol_bars=authority, accepted_pair=pair)
    assert {(fill.symbol, fill.phase, fill.timestamp_ms) for fill in closed.fills} == {
        ("LONG", "entry", entry_ms), ("SHORT", "entry", entry_ms),
        ("LONG", "stop", stop_ms), ("SHORT", "cleanup", stop_ms)}

    later_profit = C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="SHORT", side=1,
                               quantity=1, reference_price=95, phase="exit",
                               movement_from=100)
    with pytest.raises(C.IncompleteEpisode, match="deterministic lifecycle"):
        C.close_attempt(
            attempt_id="later-profit", entry_ms=entry_ms, close_ms=7*C.HOUR_MS,
            long_symbol="LONG", short_symbol="SHORT", regime="BTC_ABOVE_OR_ON_EMA200",
            fills=entries + [stop, later_profit], symbol_bars=authority, accepted_pair=pair)

    late_cleanup = C.make_fill(timestamp_ms=5*C.HOUR_MS, symbol="SHORT", side=1,
                               quantity=1, reference_price=100, phase="cleanup",
                               movement_from=100)
    with pytest.raises(C.IncompleteEpisode, match="deterministic lifecycle"):
        C.close_attempt(
            attempt_id="late-cleanup", entry_ms=entry_ms, close_ms=5*C.HOUR_MS,
            long_symbol="LONG", short_symbol="SHORT", regime="BTC_ABOVE_OR_ON_EMA200",
            fills=entries + [stop, late_cleanup], symbol_bars=authority, accepted_pair=pair)


def test_lifecycle_replay_rejects_ordinary_one_hour_exit_and_accepts_due_open():
    pair = accepted_pair(stop_delta="2")
    due_fills = [
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol="LONG", side=1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol="SHORT", side=-1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="LONG", side=-1, quantity=1,
                    reference_price=101, phase="exit", movement_from=100),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="SHORT", side=1, quantity=1,
                    reference_price=99, phase="exit", movement_from=100),
    ]
    authority = fill_bar_authority(due_fills)
    closed = C.close_attempt(
        attempt_id="due-open", entry_ms=C.HOUR_MS, close_ms=7*C.HOUR_MS,
        long_symbol="LONG", short_symbol="SHORT", regime="BTC_ABOVE_OR_ON_EMA200",
        fills=due_fills, symbol_bars=authority, accepted_pair=pair)
    assert closed.close_ms == 7*C.HOUR_MS

    early_fills = due_fills[:2] + [
        C.make_fill(timestamp_ms=2*C.HOUR_MS, symbol="LONG", side=-1, quantity=1,
                    reference_price=100, phase="exit", movement_from=100),
        C.make_fill(timestamp_ms=2*C.HOUR_MS, symbol="SHORT", side=1, quantity=1,
                    reference_price=100, phase="exit", movement_from=100),
    ]
    with pytest.raises(C.IncompleteEpisode, match="deterministic lifecycle"):
        C.close_attempt(
            attempt_id="one-hour-exit", entry_ms=C.HOUR_MS, close_ms=2*C.HOUR_MS,
            long_symbol="LONG", short_symbol="SHORT", regime="BTC_ABOVE_OR_ON_EMA200",
            fills=early_fills, symbol_bars=authority, accepted_pair=pair)


def test_fill_funding_and_risk_economics_are_derived_and_hostile_rows_fail():
    entry_ms, close_ms = C.HOUR_MS, 7*C.HOUR_MS
    fills = [
        C.make_fill(timestamp_ms=entry_ms, symbol="LONG", side=1, quantity=1, reference_price=100,
                    phase="entry", movement_from=None, prior_quote_volume=1_000_000),
        C.make_fill(timestamp_ms=entry_ms, symbol="SHORT", side=-1, quantity=1, reference_price=100,
                    phase="entry", movement_from=None, prior_quote_volume=1_000_000),
        C.make_fill(timestamp_ms=close_ms, symbol="LONG", side=-1, quantity=1, reference_price=101,
                    phase="exit", movement_from=100, prior_quote_volume=1_000_000),
        C.make_fill(timestamp_ms=close_ms, symbol="SHORT", side=1, quantity=1, reference_price=99,
                    phase="exit", movement_from=100, prior_quote_volume=1_000_000),
    ]
    kwargs = dict(attempt_id="derived", entry_ms=entry_ms, close_ms=close_ms, long_symbol="LONG",
                  short_symbol="SHORT", regime="BTC_ABOVE_OR_ON_EMA200", fills=fills,
                  symbol_bars=fill_bar_authority(fills), accepted_pair=accepted_pair())
    base = C.close_attempt(**kwargs)
    stress = C.close_attempt(**kwargs, stress=True)
    assert base.fee_drag > 0 and stress.fee_drag > 0
    assert all(fill.fee_drag == pytest.approx(abs(fill.quantity * fill.execution_price)
                                              * C.FEE_BPS / 10_000)
               for fill in stress.fills)
    assert stress.slippage_drag == pytest.approx(base.slippage_drag * 5)
    assert stress.fills[0].execution_price > base.fills[0].execution_price
    assert stress.fills[2].execution_price < base.fills[2].execution_price
    with pytest.raises(C.IncompleteEpisode, match="caller-supplied fill economics"):
        C.close_attempt(**{**kwargs, "fills": [replace(fills[0], fee_drag=0), *fills[1:]]})
    with pytest.raises(C.IncompleteEpisode, match="impact cap"):
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol="LONG", side=1, quantity=10,
                    reference_price=100, phase="entry", movement_from=None,
                    prior_quote_volume=100_000)
    tiny = accepted_pair()
    tiny = replace(tiny, sizing=replace(tiny.sizing, initial_pair_risk_cash=Decimal("1e-300")))
    with pytest.raises(C.IncompleteEpisode, match="initial risk"):
        C.close_attempt(**{**kwargs, "accepted_pair": tiny})


def test_bar_derived_stop_and_complete_canonical_funding_schedule_are_mandatory():
    entry_ms, close_ms = 7*C.HOUR_MS, 13*C.HOUR_MS
    fills = [
        C.make_fill(timestamp_ms=entry_ms, symbol="LONG", side=1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=entry_ms, symbol="SHORT", side=-1, quantity=1,
                    reference_price=100, phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=close_ms, symbol="LONG", side=-1, quantity=1,
                    reference_price=101, phase="exit", movement_from=100),
        C.make_fill(timestamp_ms=close_ms, symbol="SHORT", side=1, quantity=1,
                    reference_price=99, phase="exit", movement_from=100),
    ]
    bars = fill_bar_authority(fills)
    settlement = 8*C.HOUR_MS
    funding = [C.FundingPoint(symbol, settlement, rate)
               for symbol, rate in (("LONG", .001), ("SHORT", -.001))]
    marks = {symbol: [C.MarkClose(settlement-C.HOUR_MS, 100)]
             for symbol in ("LONG", "SHORT")}
    kwargs = dict(attempt_id="funding", entry_ms=entry_ms, close_ms=close_ms,
                  long_symbol="LONG", short_symbol="SHORT",
                  regime="BTC_ABOVE_OR_ON_EMA200", fills=fills, symbol_bars=bars,
                  accepted_pair=accepted_pair(), funding_points=funding,
                  funding_authority=funding, funding_marks=marks)
    row = C.close_attempt(**kwargs)
    assert {(event.symbol, event.timestamp_ms) for event in row.funding_events} == {
        ("LONG", settlement), ("SHORT", settlement)}
    with pytest.raises(C.IncompleteEpisode, match="8h exposure settlements"):
        C.close_attempt(**{**kwargs, "funding_authority": funding[:-1],
                           "funding_points": funding[:-1]})
    extra = C.FundingPoint("LONG", 16*C.HOUR_MS, .001)
    with pytest.raises(C.IncompleteEpisode, match="8h exposure settlements"):
        C.close_attempt(**{**kwargs, "funding_authority": funding + [extra],
                           "funding_points": funding + [extra]})
    stale = {**marks, "LONG": [C.MarkClose(settlement-7*C.HOUR_MS, 100)]}
    with pytest.raises(C.IncompleteAuthority, match="missing or stale"):
        C.close_attempt(**{**kwargs, "funding_marks": stale})

    stop_entry = C.make_fill(timestamp_ms=C.HOUR_MS, symbol="LONG", side=1, quantity=1,
                             reference_price=100, phase="entry", movement_from=None)
    forged_stop = C.make_fill(timestamp_ms=2*C.HOUR_MS, symbol="LONG", side=-1, quantity=1,
                              reference_price=1000, phase="stop", movement_from=100)
    with pytest.raises(C.IncompleteEpisode, match="frozen reference|reference/impact"):
        C.account_atomic_one_leg(
            attempt_id="forged-stop", expected_long_symbol="LONG", expected_short_symbol="SHORT",
            accepted_leg=leg("LONG", 1, "100", "98"), fills=[stop_entry, forged_stop],
            symbol_bars=fill_bar_authority([stop_entry, forged_stop]),
            regime="BTC_ABOVE_OR_ON_EMA200")
    bad_bars = {symbol: list(rows) for symbol, rows in fill_bar_authority(fills).items()}
    prior_index = next(i for i, bar in enumerate(bad_bars["LONG"])
                       if bar.open_ms == entry_ms-C.HOUR_MS)
    bad_bars["LONG"][prior_index] = replace(bad_bars["LONG"][prior_index], quote_volume=123)
    with pytest.raises(C.IncompleteEpisode, match="reference/impact|impact cap"):
        C.close_attempt(**{**kwargs, "symbol_bars": bad_bars})


def test_open_event_order_funding_boundaries_required_exit_impact_and_book_caps():
    assert C.OPEN_EVENT_ORDER[0] == "FUNDING_STRICTLY_BEFORE_OPEN"
    assert C.OPEN_EVENT_ORDER[-1] == "FUNDING_AT_OPEN"
    assert C.funding_belongs_to_exposure(nominal_time_ms=10, entry_open_ms=10, exit_open_ms=20)
    assert not C.funding_belongs_to_exposure(nominal_time_ms=20, entry_open_ms=10, exit_open_ms=20)
    event = 10*C.HOUR_MS
    marks = [C.MarkClose(event-C.HOUR_MS, 99), C.MarkClose(event, 100)]
    assert C.causal_mark_strictly_before(marks, event).price == 99
    with pytest.raises(C.IncompleteAuthority, match="duplicate"):
        C.causal_mark_strictly_before(marks + [C.MarkClose(event-C.HOUR_MS, 98)], event)
    multiplier, incomplete = C.required_exit_impact(10, 100, 500_000)
    assert multiplier == pytest.approx(2.0) and incomplete is True
    assert not C.entry_impact_feasible(10, 100, 500_000)

    sized = C.size_pair(leg("AAA", 1, "100", "98"), leg("BBB", -1, "50", "51"), book_equity=2000)
    assert sized is not None
    book = C.BookState(Decimal("2000"))
    assert book.can_admit("AAA", "BBB", sized)
    book.admit("AAA", "BBB", sized)
    assert not book.can_admit("AAA", "CCC", sized)
    book.release("AAA", "BBB", sized)
    assert book.open_pairs == 0 and not book.occupied_symbols


def test_chronological_marked_plus_realized_drawdown_includes_conservative_adjustment():
    events = [
        C.DrawdownEvent(1, "MARK_END", 0, 0, 0),
        C.DrawdownEvent(2, "MARK_BEFORE_EXIT", 0, 0, -20),
        C.DrawdownEvent(2, "EXIT", 0, 4, -4),
        C.DrawdownEvent(2, "CONSERVATIVE_ADJUSTMENT", 0, -10, 0),
    ]
    dd, recovery = C.chronological_drawdown(events)
    assert dd == 20
    assert recovery == 1
    with pytest.raises(C.IncompleteEpisode, match="missing/non-finite"):
        C.chronological_drawdown([replace(events[0], marked_open_R=math.nan)])
    # Same timestamp phases remain distinct; collapsing EXIT+adjustment would miss the +4 interim state.
    assert C.DRAW_PHASE_ORDER["EXIT"] < C.DRAW_PHASE_ORDER["CONSERVATIVE_ADJUSTMENT"]


def test_drawdown_is_derived_from_accepted_fills_positions_and_marks_not_mark_only_claims():
    row = attempt("derived", 0.2)
    marks = marks_for_attempts((row,))
    derived = C.derive_drawdown_events((row,), marks)
    assert any(event.phase == "ENTRY" for event in derived)
    assert any(event.phase == "EXIT" for event in derived)
    with pytest.raises(C.IncompleteEpisode, match="mark-only"):
        C.derive_drawdown_events((), marks)
    with pytest.raises(C.IncompleteEpisode, match="missing causal marks"):
        C.derive_drawdown_events((row,), marks[:-1])
    with pytest.raises(C.IncompleteEpisode, match="finish flat"):
        C.derive_drawdown_events((replace(row, fills=row.fills[:2]),), marks)


def test_capital_diagnostics_are_isolated_and_cannot_veto_primary():
    def factory(capital):
        if capital == 1000:
            return C.ReplayResult(capital, (), ("PAIR_SIZE_INFEASIBLE",))
        return C.ReplayResult(capital, (attempt(str(capital), 0.1),))
    results = C.replay_capitals(factory)
    assert C.primary_result(results).capital == 2000
    assert len(C.primary_result(results).attempts) == 1
    assert results[1000].skips == ("PAIR_SIZE_INFEASIBLE",)


def _reference_bootstrap(values, *, centered, seed, resamples):
    source = np.asarray(values, dtype=np.float64)
    observed = math.fsum(map(float, source)) / len(source)
    if centered:
        source = source - observed
    draws = np.random.Generator(np.random.PCG64(seed)).random(
        size=(resamples, len(source), 2), dtype=np.float64)
    output = []
    for b in range(resamples):
        index = math.floor(draws[b, 0, 1] * len(source))
        sample = [float(source[index])]
        for j in range(1, len(source)):
            restart, u_index = draws[b, j]
            index = math.floor(u_index * len(source)) if restart < 1 / 21 else (index + 1) % len(source)
            sample.append(float(source[index]))
        output.append(math.fsum(sample) / len(sample))
    return np.asarray(output)


def test_stationary_bootstrap_consumes_exact_fixed_draws_pcg64_and_atomic_rows_are_ordinary():
    values = [0.2, -0.1, -0.03, 0.4]
    actual = C.stationary_bootstrap(values, centered=True, seed=25820261107, resamples=25)
    expected = _reference_bootstrap(values, centered=True, seed=25820261107, resamples=25)
    np.testing.assert_array_equal(actual, expected)
    stats = C.bootstrap_statistics(values, resamples=25)
    assert stats["mean"] == pytest.approx(math.fsum(values) / len(values))
    assert 0 < stats["p_value"] <= 1


def test_fold_metrics_include_atomic_failures_regimes_and_fail_closed_on_zero_denominator():
    rows = [attempt("1", 0.2), replace(attempt("2", -0.1), atomic_one_leg_loss=True),
            attempt("3", 0.05, regime="BTC_BELOW_EMA200")]
    metrics = C.fold_metrics(rows)
    assert metrics["closed_attempts"] == 3 and metrics["atomic_failures"] == 1
    assert set(metrics["regime"]) == {"BTC_ABOVE_OR_ON_EMA200", "BTC_BELOW_EMA200"}
    source = attempt("x", 0.0)
    zero_row = C.bind_attempt_derivation(replace(source, movement_cash=0, funding_cash=0,
        fee_drag=0, slippage_drag=0, actual_net_cash=0, verdict_net_cash=0, verdict_R=0,
        gross_R=0, long_contribution_R=0, short_contribution_R=0,
        legs=tuple(replace(item, movement_cash=0, funding_cash=0, fee_drag=0,
                           slippage_drag=0, net_cash=0, net_R=0) for item in source.legs)))
    zero = C.fold_metrics([zero_row])
    assert math.isinf(zero["max_symbol_concentration"])
    with pytest.raises(C.C258Error, match="undefined"):
        C.fold_metrics([])
    assert math.isnan(C.profit_factor([0.2, 0.1]))


def test_leg_cashflows_and_symbol_concentration_use_actual_attribution_not_half_split():
    fills = [
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol="LONG", side=1, quantity=1, reference_price=100,
                    phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=C.HOUR_MS, symbol="SHORT", side=-1, quantity=1, reference_price=100,
                    phase="entry", movement_from=None),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="LONG", side=-1, quantity=1,
                    reference_price=109, phase="exit", movement_from=100),
        C.make_fill(timestamp_ms=7*C.HOUR_MS, symbol="SHORT", side=1, quantity=1,
                    reference_price=99, phase="exit", movement_from=100),
    ]
    row = C.close_attempt(
        attempt_id="asymmetric", entry_ms=C.HOUR_MS, close_ms=7*C.HOUR_MS,
        long_symbol="LONG", short_symbol="SHORT", regime="BTC_ABOVE_OR_ON_EMA200",
        fills=fills, symbol_bars=fill_bar_authority(fills), accepted_pair=accepted_pair())
    assert row.long_contribution_R > row.short_contribution_R
    assert math.fsum(leg.net_R for leg in row.legs) == pytest.approx(row.verdict_R)
    metrics = C.fold_metrics([row])
    assert metrics["max_symbol_concentration"] > 0.5


def test_output_identity_binds_every_pin_source_result_and_default_off():
    pins = C.AuthorityPins("a" * 40, *[
        C.ArtifactPin(path, str(i) * 64, chr(97 + i) * 64)
        for i, path in enumerate(C.EXPECTED_AUTHORITY_PATHS.values())
    ])
    first = C.immutable_output_identity(pins=pins, source_digest="f" * 64, result={"verdict": "REJECT"})
    second = C.immutable_output_identity(pins=pins, source_digest="f" * 64, result={"verdict": "PASS"})
    assert first["default_off"] is True and first["paper_testnet_only"] is True
    assert first["sealed_holdout_opened"] is False
    assert first["output_identity_sha256"] != second["output_identity_sha256"]


def test_development_veto_never_opens_forward_and_stress_cannot_change_selection():
    base_rows = tuple(C.bind_attempt_derivation(replace(attempt(str(i), 0.01), gross_R=0.02))
                      for i in range(10))
    stress_rows = tuple(C.mechanical_stress_attempt(row) for row in base_rows)
    fold = C.FoldEvidence("development", C.ReplayResult(2000, base_rows),
                          C.ReplayResult(2000, stress_rows), (10, 10), (0.1, 0.1),
                          marks_for_attempts(base_rows), marks_for_attempts(base_rows),
                          100, 100, 10, 10, 0)
    result = C.evaluate_stage_a(fold, ())
    assert result["verdict"] == "REJECT_DEVELOPMENT"
    assert result["forward_opened"] is False

    changed = replace(base_rows[0], attempt_id="changed")
    hostile = replace(fold, stress=C.ReplayResult(2000, (changed,) + stress_rows[1:]))
    with pytest.raises(C.C258Error, match="derivation identity mismatch|stress replay changed selection"):
        C.validate_fold_evidence(hostile)
    close_changed = replace(base_rows[0], close_ms=3 * C.HOUR_MS)
    hostile_close = replace(fold, stress=C.ReplayResult(2000, (close_changed,) + stress_rows[1:]))
    with pytest.raises(C.C258Error, match="derivation identity mismatch|stress replay changed selection|not mechanically derived"):
        C.validate_fold_evidence(hostile_close)
    lifecycle_changed = replace(base_rows[0], lifecycle_identity=base_rows[0].lifecycle_identity[:-1] +
                                ((2 * C.HOUR_MS, "L0", -1, "1.0", "102.0", "exit"),))
    hostile_lifecycle = replace(fold, stress=C.ReplayResult(2000, (lifecycle_changed,) + stress_rows[1:]))
    with pytest.raises(C.C258Error, match="derivation identity mismatch|stress replay changed selection"):
        C.validate_fold_evidence(hostile_lifecycle)

    funding = C.FundingCashEvent(C.HOUR_MS // 2, base_rows[0].long_symbol, 0.01)
    original = base_rows[0]
    long_leg = original.legs[0]
    funded = C.bind_attempt_derivation(replace(original, funding_cash=original.funding_cash + 0.01,
                     actual_net_cash=original.actual_net_cash + 0.01,
                     verdict_net_cash=original.verdict_net_cash + 0.01,
                     verdict_R=original.verdict_R + 0.01 / original.initial_risk_cash,
                     funding_events=(funding,),
                     long_contribution_R=original.long_contribution_R + 0.01 / original.initial_risk_cash,
                     legs=(replace(long_leg, funding_cash=0.01, net_cash=long_leg.net_cash + 0.01,
                                   net_R=long_leg.net_R + 0.01 / original.initial_risk_cash),
                           original.legs[1])))
    stress_funded = C.mechanical_stress_attempt(funded)
    funded_fold = replace(fold, base=C.ReplayResult(2000, (funded,) + base_rows[1:]),
                          stress=C.ReplayResult(2000, (stress_funded,) + stress_rows[1:]))
    C.validate_fold_evidence(funded_fold)
    bad_funding = C.bind_attempt_derivation(replace(stress_funded,
                          funding_events=(replace(stress_funded.funding_events[0], cash=0.02),)))
    with pytest.raises(C.IncompleteEpisode, match="mechanically derived|derivation identity"):
        C.validate_fold_evidence(replace(funded_fold,
            stress=C.ReplayResult(2000, (bad_funding,) + stress_rows[1:])))
    for hostile_row in (replace(stress_funded, initial_risk_cash=2),
                        replace(stress_funded, verdict_R=999),
                        replace(stress_funded, atomic_one_leg_loss=True),
                        replace(stress_funded, fills=(replace(stress_funded.fills[0], fee_drag=1),)
                                                     + stress_funded.fills[1:])):
        with pytest.raises((C.IncompleteEpisode, C.C258Error)):
            C.validate_fold_evidence(replace(funded_fold,
                stress=C.ReplayResult(2000, (hostile_row,) + stress_rows[1:])))


def test_gate_derives_worse_of_marked_and_close_time_drawdown_never_accepts_supplied_value():
    assert "max_drawdown_R" not in {field.name for field in fields(C.FoldEvidence)}
    rows = tuple(C.bind_attempt_derivation(replace(attempt(str(i), 10), gross_R=20))
                 for i in range(300))
    stress_rows = tuple(C.mechanical_stress_attempt(row) for row in rows)
    marked = tuple(C.PortfolioMark(mark.timestamp_ms, mark.symbol,
                                  39 if mark.symbol == "L0" else 100)
                   for mark in marks_for_attempts(rows))
    fold = C.FoldEvidence(
        "development", C.ReplayResult(2000, rows), C.ReplayResult(2000, stress_rows),
        (25, 25), (1, 1), marked, marked, 300, 300, 300, 0, 0)
    derived = C.fold_drawdown(fold)
    assert derived["closed_R"] == 0
    assert derived["marked_cashflow_R"] > 60
    assert derived["worse_of_R"] == derived["marked_cashflow_R"]
    assert C.development_gate(fold)["checks"]["closed_R_drawdown_lte_60"] is False


def test_drawdown_tiers_and_append_only_trial_ledger_are_frozen():
    assert C.drawdown_tier(net_R=20, max_drawdown_R=10,
                           stress_net_R=10, stress_max_drawdown_R=10)["tier"] == "standard"
    elevated = C.drawdown_tier(net_R=30, max_drawdown_R=20,
                               stress_net_R=10, stress_max_drawdown_R=10)
    assert elevated == {"tier": "elevated", "pass": True, "paper_pair_risk_multiplier": 1.0}
    high = C.drawdown_tier(net_R=80, max_drawdown_R=40,
                           stress_net_R=40, stress_max_drawdown_R=40)
    assert high["pass"] is True and high["paper_pair_risk_multiplier"] == pytest.approx(0.375)
    assert C.drawdown_tier(net_R=200, max_drawdown_R=61,
                           stress_net_R=100, stress_max_drawdown_R=20)["pass"] is False

    ledger = C.TrialLedger()
    first = ledger.append(fold="wf1", kind="SCHEDULED", detail="decision")
    second = ledger.append(fold="wf1", kind="INDEXED_SKIP", detail="occupied", attempt_id="wf1-1")
    assert (first.sequence, second.sequence) == (0, 1)
    before = ledger.digest()
    ledger.append(fold="wf1", kind="REJECTION", detail="gate failed")
    assert ledger.digest() != before
    with pytest.raises(C.C258Error, match="trial-ledger"):
        ledger.append(fold="wf1", kind="PROMOTE_DIAGNOSTIC", detail="forbidden")
