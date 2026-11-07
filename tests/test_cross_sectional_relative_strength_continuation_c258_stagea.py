"""Hostile synthetic-only tests for C258.

No test opens a market archive, outcome, candidate packet, evaluation identity,
or sealed-holdout artifact.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C


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


def attempt(identifier: str, r_value: float, *, regime: str = "BTC_ABOVE_OR_ON_EMA200") -> C.ClosedAttempt:
    return C.ClosedAttempt(identifier, 1, 2, f"L{identifier}", f"S{identifier}", regime,
                           r_value, 0, 0, 0, r_value, r_value, 1, r_value, False, 0)


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

    pins = C.AuthorityPins("a" * 40, *[
        C.ArtifactPin(path, "0" * 64, "1" * 64) for path in C.EXPECTED_AUTHORITY_PATHS.values()
    ])
    with pytest.raises(C.IncompleteAuthority, match="missing authority-v6 output"):
        C.run_with_authority(pins, loader, root=tmp_path)
    assert called is False

    docs = {
        "manifest_v6": {"format": "zb-binance-um-archive-manifest/6", "content_digest": "a" * 64,
                        "cutoff_exclusive_utc": "2026-11-05T00:00:00Z"},
        "pit_v9": {"format": "zb-pit-universe/2", "content_digest": "b" * 64},
        "filter_v4": {"authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", "version": 4,
                      "content_digest": "c" * 64},
        "coverage_v4": {"content_digest": "d" * 64, "guaranteed_period_gaps": 0,
                        "result": "ZERO_GUARANTEED_PERIOD_GAPS"},
    }
    generated = []
    for name, path_string in C.EXPECTED_AUTHORITY_PATHS.items():
        path = tmp_path / path_string
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(docs[name], sort_keys=True, separators=(",", ":")).encode()
        raw = gzip.compress(payload, mtime=0) if path.suffix == ".gz" else payload
        path.write_bytes(raw)
        generated.append(C.ArtifactPin(path_string, hashlib.sha256(raw).hexdigest(), docs[name]["content_digest"]))
    good = C.AuthorityPins("e" * 40, *generated)
    assert C.run_with_authority(good, lambda authorities: set(authorities), root=tmp_path) == set(docs)
    bad = replace(good, coverage_v4=replace(good.coverage_v4, content_digest="f" * 64))
    called = False
    with pytest.raises(C.IncompleteAuthority, match="content digest mismatch"):
        C.run_with_authority(bad, loader, root=tmp_path)
    assert called is False


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
    assert C.fixed_exit_open(rows, C.HOUR_MS).open_ms == 7 * C.HOUR_MS
    assert C.impact_ratio(1, 100, 200_000) == pytest.approx(0.0005)
    with pytest.raises(C.IncompleteAuthority, match="impact authority"):
        C.impact_ratio(1, 100, 0)


def test_cost_signs_funding_and_atomic_one_leg_verdict_cannot_be_rescued():
    entry = C.make_fill(timestamp_ms=1, symbol="AAA", side=1, quantity=1, reference_price=100,
                        phase="entry", movement_from=None)
    cleanup = C.make_fill(timestamp_ms=2, symbol="AAA", side=-1, quantity=1, reference_price=120,
                          phase="cleanup", movement_from=100)
    assert entry.fee_drag >= 0 and entry.slippage_drag >= 0
    assert cleanup.movement_cash == 20
    funding = C.signed_funding_cash(position_side=1, quantity=1, causal_mark=110, funding_rate=-0.001)
    assert funding > 0
    stressed = C.signed_funding_cash(position_side=1, quantity=1, causal_mark=110,
                                     funding_rate=-0.001, stress=True)
    assert stressed == pytest.approx(funding / 1.5)
    closed = C.close_attempt(attempt_id="a", entry_ms=1, close_ms=2, long_symbol="AAA",
                             short_symbol="BBB", regime="BTC_ABOVE_OR_ON_EMA200",
                             fills=[entry, cleanup], funding_cash=funding,
                             initial_risk_cash=5, atomic_one_leg=True)
    assert closed.actual_net_cash > 0
    assert closed.verdict_net_cash == pytest.approx(-(closed.fee_drag + closed.slippage_drag))
    assert closed.verdict_net_cash <= 0 and closed.conservative_adjustment_cash <= 0
    assert closed.verdict_R == pytest.approx(closed.verdict_net_cash / 5)

    atomic = C.account_atomic_one_leg(
        attempt_id="atomic", expected_long_symbol="AAA", expected_short_symbol="BBB",
        accepted_leg=leg("AAA", 1, "100", "98"), quantity=Decimal("1"),
        fills=[entry, cleanup], funding_cash=funding, regime="BTC_ABOVE_OR_ON_EMA200")
    assert atomic.atomic_one_leg_loss and atomic.initial_risk_cash > 2
    same_open = replace(cleanup, timestamp_ms=1)
    with pytest.raises(C.IncompleteEpisode, match="after the entry"):
        C.account_atomic_one_leg(
            attempt_id="atomic", expected_long_symbol="AAA", expected_short_symbol="BBB",
            accepted_leg=leg("AAA", 1, "100", "98"), quantity=Decimal("1"),
            fills=[entry, same_open], funding_cash=0, regime="BTC_ABOVE_OR_ON_EMA200")


def test_open_event_order_funding_boundaries_required_exit_impact_and_book_caps():
    assert C.OPEN_EVENT_ORDER[0] == "FUNDING_STRICTLY_BEFORE_OPEN"
    assert C.OPEN_EVENT_ORDER[-1] == "FUNDING_AT_OPEN"
    assert C.funding_belongs_to_exposure(nominal_time_ms=10, entry_open_ms=10, exit_open_ms=20)
    assert not C.funding_belongs_to_exposure(nominal_time_ms=20, entry_open_ms=10, exit_open_ms=20)
    marks = [C.MarkClose(9, 99), C.MarkClose(10, 100)]
    assert C.causal_mark_strictly_before(marks, 10).price == 99
    with pytest.raises(C.IncompleteAuthority, match="duplicate"):
        C.causal_mark_strictly_before(marks + [C.MarkClose(9, 98)], 10)
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
    # The mark creates a larger intra-trade drawdown than the eventual realized cashflow.
    dd, recovery = C.chronological_drawdown([(3, 4), (4, -10)], [(1, 0), (2, -20)], 100)
    assert dd == 20
    assert recovery == 2


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
    zero = C.fold_metrics([attempt("x", 0.0)])
    assert math.isinf(zero["max_symbol_concentration"])
    with pytest.raises(C.C258Error, match="undefined"):
        C.fold_metrics([])


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
    base_rows = tuple(replace(attempt(str(i), 0.01), gross_R=0.02,
                              long_contribution_R=0.005, short_contribution_R=0.005)
                      for i in range(10))
    fold = C.FoldEvidence("development", C.ReplayResult(2000, base_rows),
                          C.ReplayResult(2000, base_rows), (10, 10), (0.1, 0.1),
                          1, 1, 100, 100, 10, 10, 0)
    result = C.evaluate_stage_a(fold, ())
    assert result["verdict"] == "REJECT_DEVELOPMENT"
    assert result["forward_opened"] is False

    changed = replace(base_rows[0], attempt_id="changed")
    hostile = replace(fold, stress=C.ReplayResult(2000, (changed,) + base_rows[1:]))
    with pytest.raises(C.C258Error, match="stress replay changed selection"):
        C.validate_fold_evidence(hostile)


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
