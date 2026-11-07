from __future__ import annotations

import gzip
import hashlib
import json
import math
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from tools.research import btc_residual_leadlag_continuation_c259_stagea as c259


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _digest(document: dict) -> str:
    body = {key: value for key, value in document.items()
            if key not in {"digest", "content_digest"}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _write_document(repo: Path, relative: str, document: dict, *, compressed: bool = False) -> bytes:
    document["digest"] = _digest(document)
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    raw = gzip.compress(payload, mtime=0) if compressed else payload
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _authority_repo(tmp_path: Path) -> tuple[Path, c259.AuthorityPins]:
    repo = tmp_path / "authority"
    _run(tmp_path, "git", "clone", "--shared", str(c259.ROOT), str(repo))
    _run(repo, "git", "checkout", "--detach", c259.CONTRACT_HEAD)
    _run(repo, "git", "config", "user.email", "c259@example.invalid")
    _run(repo, "git", "config", "user.name", "C259 Synthetic")

    manifest = {"format": "zb-binance-um-archive-manifest/6",
                "cutoff_exclusive_utc": "2026-11-05T00:00:00Z"}
    manifest_raw = _write_document(repo, c259.EXPECTED_PATHS["manifest_v6"], manifest,
                                   compressed=True)
    pit = {"format": "zb-pit-universe/2", "manifest_digest": manifest["digest"],
           "cells": [{"monday_ms": 1, "symbols": ["ETHUSDT"]}]}
    pit_raw = _write_document(repo, c259.EXPECTED_PATHS["pit_v9"], pit)
    filters = {"authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY",
               "version": 4, "pit_universe_sha256": hashlib.sha256(pit_raw).hexdigest(),
               "pit_universe_digest": pit["digest"]}
    filter_raw = _write_document(repo, c259.EXPECTED_PATHS["filter_v4"], filters)
    coverage = {"required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
                "guaranteed_period_gaps": 0, "gaps": [],
                "manifest_digest": manifest["digest"], "pit_universe_digest": pit["digest"],
                "filter_authority_digest": filters["digest"]}
    coverage_raw = _write_document(repo, c259.EXPECTED_PATHS["coverage_v4"], coverage)
    _run(repo, "git", "add", *c259.EXPECTED_PATHS.values())
    _run(repo, "git", "commit", "-m", "synthetic authority-v6")
    head = _run(repo, "git", "rev-parse", "HEAD")
    _run(repo, "git", "update-ref", "refs/remotes/origin/master", head)

    def pin(name: str, raw: bytes, document: dict) -> c259.ArtifactPin:
        return c259.ArtifactPin(c259.EXPECTED_PATHS[name], hashlib.sha256(raw).hexdigest(),
                                document["digest"])

    pins = c259.AuthorityPins(
        head, pin("manifest_v6", manifest_raw, manifest), pin("pit_v9", pit_raw, pit),
        pin("filter_v4", filter_raw, filters), pin("coverage_v4", coverage_raw, coverage))
    return repo, pins


def _bar(open_ms: int, open_: float = 100, high: float = 102, low: float = 98,
         close: float = 101, volume: float = 1_000_000) -> c259.Bar:
    return c259.Bar(open_ms, open_, high, low, close, volume)


def _fill(*, movement: float = 0, fee: float = 0, slip: float = 0,
          impact: float = 0, side: int = 1, timestamp: int = 1) -> c259.Fill:
    return c259.Fill(timestamp, "TEST", "ALTUSDT", side, 1, 100, movement, fee, slip, impact)


def _trade(attempt: str, side: int, symbol: str, entry: int, close: int,
           cash: float, risk: float = 10, regime: str = "BTC_ABOVE_OR_ON_EMA200",
           incomplete: bool = False) -> c259.ClosedTrade:
    return c259.ClosedTrade(attempt, side, symbol, entry, close, regime,
                            (_fill(movement=cash, side=-side, timestamp=close),), 0, risk,
                            incomplete)


def test_frozen_bytes_and_cli_remain_locked() -> None:
    contract = c259.load_frozen_contract()
    assert contract["candidate_id"] == 259 and contract["default_off"] is True
    with pytest.raises(c259.EvaluationLocked, match="no loader invoked"):
        c259.assert_real_evaluation_authorized()


def test_authority_validates_protected_tree_before_loader(tmp_path: Path) -> None:
    repo, pins = _authority_repo(tmp_path)
    calls: list[dict] = []
    assert c259.run_with_authority(pins, lambda docs: calls.append(docs) or "loaded",
                                   repo=repo) == "loaded"
    assert len(calls) == 1 and set(calls[0]) == set(c259.EXPECTED_PATHS)


@pytest.mark.parametrize("attack", ["invented_head", "wrong_byte", "wrong_content",
                                     "not_protected", "coverage_gap"])
def test_authority_hostile_mutations_fail_before_loader(tmp_path: Path, attack: str) -> None:
    repo, pins = _authority_repo(tmp_path)
    if attack == "invented_head":
        pins = replace(pins, protected_head="0" * 40)
    elif attack == "wrong_byte":
        pins = replace(pins, pit_v9=replace(pins.pit_v9, byte_sha256="1" * 64))
    elif attack == "wrong_content":
        pins = replace(pins, filter_v4=replace(pins.filter_v4, content_digest="2" * 64))
    elif attack == "not_protected":
        _run(repo, "git", "update-ref", "refs/remotes/origin/master", c259.CONTRACT_HEAD)
    else:
        coverage_path = repo / c259.EXPECTED_PATHS["coverage_v4"]
        coverage = json.loads(coverage_path.read_text())
        coverage["guaranteed_period_gaps"] = 1
        coverage["gaps"] = ["missing"]
        raw = _write_document(repo, c259.EXPECTED_PATHS["coverage_v4"], coverage)
        _run(repo, "git", "add", c259.EXPECTED_PATHS["coverage_v4"])
        _run(repo, "git", "commit", "-m", "hostile coverage")
        head = _run(repo, "git", "rev-parse", "HEAD")
        _run(repo, "git", "update-ref", "refs/remotes/origin/master", head)
        pins = replace(pins, protected_head=head,
                       coverage_v4=replace(pins.coverage_v4,
                                           byte_sha256=hashlib.sha256(raw).hexdigest(),
                                           content_digest=coverage["digest"]))
    calls: list[object] = []
    with pytest.raises(c259.IncompleteAuthority):
        c259.run_with_authority(pins, lambda docs: calls.append(docs), repo=repo)
    assert calls == []


def test_signal_uses_prior_window_only_and_unconsumed_residual() -> None:
    rng = np.random.Generator(np.random.PCG64(259))
    btc = list(rng.normal(0, .001, 720))
    noise = list(rng.normal(0, .00001, 720))
    alt = [1.3 * btc[i] + (0.35 * btc[i - 1] if i else 0) + noise[i]
           for i in range(720)]
    first = c259.fit_signal(btc, alt, btc_d=0.01, alt_d=0.013)
    second = c259.fit_signal(btc, alt, btc_d=0.02, alt_d=0.026)
    assert first.beta0 == second.beta0 and first.gamma == second.gamma
    assert first.gamma >= 0.10 and first.t_gamma >= 2 and first.side == 1 and first.qualifies
    assert second.side == 1 and second.qualifies
    consumed = c259.fit_signal(btc, alt, btc_d=0.01,
                               alt_d=first.beta0 * 0.01 + first.forecast)
    assert consumed.qualifies is False


def test_signal_sides_are_separate_and_candidate_order_is_deterministic() -> None:
    base = c259.SignalFit(1, .3, .01, 30, 0, .005, .0075, 1, True)
    stronger = replace(base, forecast=.007)
    short = replace(base, forecast=-.006, side=-1)
    rows = [("ZZZUSDT", stronger), ("AAAUSDT", stronger), ("MIDUSDT", base),
            ("SHORTUSDT", short), ("OCCUPIED", replace(base, forecast=.02))]
    assert c259.select_candidates(rows, 1, occupied=["OCCUPIED"]) == (
        "AAAUSDT", "ZZZUSDT", "MIDUSDT")
    assert c259.select_candidates(rows, -1) == ("SHORTUSDT",)


def test_next_open_requires_exact_strictly_later_boundary() -> None:
    decision = 10 * c259.HOUR_MS
    assert c259.next_open([_bar(decision), _bar(decision + c259.HOUR_MS)], decision).open_ms \
        == decision + c259.HOUR_MS
    with pytest.raises(c259.IncompleteAuthority, match="exact next"):
        c259.next_open([_bar(decision + 2 * c259.HOUR_MS)], decision)


def test_atr_is_contiguous_and_protection_rounds_toward_entry() -> None:
    bars = [_bar(i * c259.HOUR_MS, 100, 102, 98, 101) for i in range(25)]
    atr = c259.atr24_wilder(bars, 25 * c259.HOUR_MS)
    assert atr == pytest.approx(4)
    assert c259.protection_prices(Decimal("100.03"), Decimal(str(atr)), 1,
                                  Decimal("0.1")) == (Decimal("92.1"), Decimal("115.8"))
    gapped = [_bar(i*c259.HOUR_MS) for i in range(26) if i != 12]
    with pytest.raises(c259.IncompleteAuthority, match="ATR gap"):
        c259.atr24_wilder(gapped, 26*c259.HOUR_MS)


def test_sizing_is_decimal_floor_only_and_enforces_caps_and_impact() -> None:
    filters = c259.Filters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"),
                           Decimal("100"), Decimal("5"))
    sized = c259.size_position(side=1, entry=Decimal("100"), stop=Decimal("96"),
                               filters=filters, side_book_equity=Decimal("2000"),
                               quote_volume_24h=10_000_000)
    assert sized is not None
    assert sized.quantity % filters.step == 0
    assert sized.initial_risk_cash <= Decimal("20")
    assert sized.raw_notional <= Decimal("1000")
    assert c259.size_position(side=1, entry=Decimal("100"), stop=Decimal("99.99"),
                              filters=replace(filters, min_notional=Decimal("5000")),
                              side_book_equity=Decimal("2000"),
                              quote_volume_24h=1000) is None
    with pytest.raises(c259.IncompleteEpisode):
        c259.impact_drag(Decimal("2"), Decimal("100"), 10_000)


def test_prefilter_liquidity_and_cost_admissions_are_frozen() -> None:
    assert not c259.filter_authority_available(1734134399999)
    assert c259.filter_authority_available(1734134400000)
    preceding = [100.0 + value for value in range(24)]
    assert c259.liquidity_admission(decision_quote_volume=112,
                                    preceding_quote_volumes=preceding,
                                    raw_entry_notional=Decimal("1000"),
                                    causal_quote_volume_24h=100_000)
    assert not c259.liquidity_admission(decision_quote_volume=111,
                                        preceding_quote_volumes=preceding,
                                        raw_entry_notional=Decimal("1000"),
                                        causal_quote_volume_24h=100_000)
    assert c259.cost_admission(entry=Decimal("100"), stop=Decimal("99.3"),
                               forecast=.0028)
    assert not c259.cost_admission(entry=Decimal("100"), stop=Decimal("99.31"),
                                   forecast=.0028)


@pytest.mark.parametrize(
    "side,bar,scheduled,expected",
    [(1, _bar(0, 94, 100, 90, 95), False, ("STOP_GAP", 94, 0)),
     (1, _bar(0, 111, 112, 109, 110), False, ("TARGET_GAP", 110, 0)),
     (1, _bar(0, 100, 112, 94, 101), False, ("STOP_RANGE", 95, c259.HOUR_MS)),
     (-1, _bar(0, 100, 106, 88, 99), False, ("STOP_RANGE", 105, c259.HOUR_MS)),
     (1, _bar(0, 100, 103, 98, 101), True, ("SCHEDULED", 100, 0))],
)
def test_exit_order_gap_cap_collision_and_schedule(side: int, bar: c259.Bar,
                                                    scheduled: bool, expected: tuple) -> None:
    assert c259.exit_reference(side=side, stop=95 if side == 1 else 105,
                               target=110 if side == 1 else 90, bar=bar,
                               scheduled_due=scheduled) == expected


def test_funding_sign_fill_costs_and_frozen_event_order() -> None:
    assert c259.funding_cash(1, 2, 100, .001) == pytest.approx(-.2)
    assert c259.funding_cash(-1, 2, 100, .001) == pytest.approx(.2)
    fill = c259.make_fill(timestamp_ms=1, phase="EXIT", symbol="ALT", side=-1,
                          quantity=2, reference=110, movement_from=100,
                          quote_volume_24h=1_000_000)
    assert fill.movement_cash == 20
    assert fill.fee_drag > 0 and fill.slippage_drag > 0 and fill.impact_drag > 0
    phases = [row[1] for row in c259.ordered_events([
        (1, "ENTRY", "A", None), (1, "FUNDING", "A", None),
        (1, "GAP_OR_SCHEDULED", "A", None),
        (1, "INTRABAR_PRIOR_BOUNDARY", "A", None), (1, "MARK", "A", None)])]
    assert phases == ["INTRABAR_PRIOR_BOUNDARY", "FUNDING", "GAP_OR_SCHEDULED",
                      "ENTRY", "MARK"]


def test_side_books_are_isolated_and_protection_failure_is_retained() -> None:
    sized = c259.SizedPosition(Decimal("1"), Decimal("10"), Decimal("10"), Decimal("500"))
    long_book, short_book = c259.SideBook(Decimal("2000")), c259.SideBook(Decimal("2000"))
    long_book.admit("ALT", sized)
    assert "ALT" in long_book.positions and short_book.positions == {}
    for symbol in ("B", "C"):
        long_book.admit(symbol, sized)
    assert not long_book.can_admit("D", sized)
    failed = c259.protection_failure(attempt_id="x", side=1, symbol="ALT", entry_ms=1,
                                    cleanup_ms=2, fills=[_fill(fee=1, slip=2, impact=3)],
                                    funding=-.5, initial_risk_cash=10,
                                    regime="BTC_ABOVE_OR_ON_EMA200")
    assert failed.incomplete and failed.net_cash == pytest.approx(-6.5)
    with pytest.raises(c259.IncompleteEpisode):
        c259.protection_failure(attempt_id="x", side=1, symbol="ALT", entry_ms=1,
                                cleanup_ms=1, fills=[], funding=0, initial_risk_cash=10,
                                regime="BTC_ABOVE_OR_ON_EMA200")


def test_chronological_marked_drawdown_and_realized_order_are_not_entry_order() -> None:
    # Marked equity falls $100 from a $2,050 peak; close-time realized fall is 2R.
    marked_dd = c259.marked_equity_drawdown_R([(1, 0, 2050), (2, 0, 1950),
                                                (3, 0, 2000)], 2000)
    trades = [_trade("late-entry", 1, "A", 10, 20, -20),
              _trade("early-entry", 1, "B", 1, 10, 10)]
    realized_dd = c259.closed_realized_drawdown_R(trades)
    assert marked_dd == pytest.approx(5) and realized_dd == pytest.approx(2)
    assert c259.gate_drawdown_R(marked_drawdown_R=marked_dd,
                               realized_drawdown_R=realized_dd) == marked_dd


def test_split_boundaries_and_regime_state_reset() -> None:
    assert c259.split_name(1743465600000) == "wf1"
    assert c259.split_name(1790553600000) == "sealed_holdout"
    state = c259.EMA200()
    for i in range(200):
        result = state.update(_bar(i*c259.HOUR_MS, close=100 + i/1000))
    assert result == "BTC_ABOVE_OR_ON_EMA200"
    assert state.update(_bar(205*c259.HOUR_MS, close=100)) is None


def test_metrics_stress_identity_forward_gates_and_drawdown_tiers() -> None:
    base = [_trade("a", 1, "A", 1, 2, 2), _trade("b", 1, "B", 3, 4, -1)]
    stress = [_trade("a", 1, "A", 1, 2, 1), _trade("b", 1, "B", 3, 4, -2)]
    c259.stress_same_selection(base, stress)
    with pytest.raises(c259.C259Error, match="changed admitted"):
        c259.stress_same_selection(base, stress[:-1])
    report = c259.metrics(base)
    assert report["closed"] == 2 and report["profit_factor"] == pytest.approx(2)
    passing = {"mean_R": .04, "profit_factor": 1.2, "closed": 300, "symbols": 12,
               "median_monthly_closes": 12, "positive_folds": 2,
               "worst_fold_mean_R": -.04, "positive_month_fraction": .56,
               "min_regime_closes": 30, "worst_regime_mean_R": -.02,
               "stress_mean_R": .01, "stress_profit_factor": 1.03,
               "p_value": c259.MULTIPLICITY_ALPHA, "max_symbol_concentration": .25,
               "drawdown_pass": True, "incomplete": False}
    assert c259.forward_gate(passing) == (True, ())
    assert c259.forward_gate({**passing, "p_value": c259.MULTIPLICITY_ALPHA + 1e-12}) \
        == (False, ("multiplicity",))
    assert c259.drawdown_tier(50, 20, 20, 10)["pass"]
    assert not c259.drawdown_tier(100, 61, 100, 20)["pass"]


def test_bootstrap_exact_draws_resolution_and_immutable_identity(tmp_path: Path) -> None:
    repo, pins = _authority_repo(tmp_path)
    del repo
    values = [.2, -.1, .3, .05]
    first = c259.stationary_bootstrap(values, seed=259202611071, centered=True,
                                      resamples=23)
    second = c259.stationary_bootstrap(values, seed=259202611071, centered=True,
                                       resamples=23)
    assert np.array_equal(first, second)
    stats = c259.bootstrap_stats(values, side=1, resamples=20_000)
    assert 1 / 20_001 <= stats["p_value"] <= 1
    assert 1 / 20_001 < c259.MULTIPLICITY_ALPHA
    identity = c259.immutable_result_identity(pins=pins, source_digest="a" * 64,
                                              result={"verdict": "REJECT"})
    mutated = c259.immutable_result_identity(pins=pins, source_digest="a" * 64,
                                             result={"verdict": "PASS"})
    assert identity["default_off"] and identity["paper_testnet_only"]
    assert identity["sealed_holdout_opened"] is False
    assert identity["output_identity_sha256"] != mutated["output_identity_sha256"]
