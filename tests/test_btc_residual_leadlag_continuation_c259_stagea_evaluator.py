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


def _authority_repo(tmp_path: Path, schema_attack: str | None = None) -> tuple[Path, c259.AuthorityPins]:
    repo = tmp_path / "authority"
    _run(tmp_path, "git", "clone", "--shared", str(c259.ROOT), str(repo))
    _run(repo, "git", "checkout", "--detach", c259.CONTRACT_HEAD)
    _run(repo, "git", "config", "user.email", "c259@example.invalid")
    _run(repo, "git", "config", "user.name", "C259 Synthetic")

    manifest = {"format": "zb-binance-um-archive-manifest/6",
                "cutoff_exclusive_utc": "2026-11-05T00:00:00Z",
                "cutoff_exclusive_ms": 1793836800000}
    if schema_attack == "conflicting_cutoff":
        manifest["cutoff_exclusive_ms"] = 1793836800001
    manifest_raw = _write_document(repo, c259.EXPECTED_PATHS["manifest_v6"], manifest,
                                   compressed=True)
    pit = {"format": "zb-pit-universe/2", "manifest_digest": manifest["digest"],
           "cells": [{"monday_ms": monday, "symbols": ["ETHUSDT"]}
                     for monday in range(1728864000000, 1793577600000+1,
                                         7*24*c259.HOUR_MS)]}
    if schema_attack == "empty_pit":
        pit["cells"] = []
    elif schema_attack == "one_cell_pit":
        pit["cells"] = pit["cells"][-1:]
    pit_raw = _write_document(repo, c259.EXPECTED_PATHS["pit_v9"], pit)
    filters = {"authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY",
               "version": 4, "pit_universe_sha256": hashlib.sha256(pit_raw).hexdigest(),
               "pit_universe_digest": pit["digest"], "fold_bindings": {
                   "development_eligible_segment": "2024-12-13T04:44:48Z",
                   "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
                   "wf3": "2026-02-17T04:13:28Z",
                   "sealed_holdout": "2026-02-17T04:13:28Z"}}
    if schema_attack == "missing_folds":
        filters.pop("fold_bindings")
    filter_raw = _write_document(repo, c259.EXPECTED_PATHS["filter_v4"], filters)
    coverage = {"format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
                "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
                "guaranteed_period_gaps": 0, "gaps": [],
                "required_streams": ["klines:1d", "klines:1h", "markPriceKlines:1h",
                                     "fundingRate:null"],
                "coverage_start_ms": 1728864000000,
                "cutoff_exclusive_ms": 1793836800000,
                "manifest_digest": manifest["digest"], "pit_universe_digest": pit["digest"],
                "filter_authority_digest": filters["digest"]}
    if schema_attack == "coverage_semantics":
        coverage["required_streams"] = ["klines:1h"]
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
          impact: float = 0, side: int = 1, timestamp: int = 1,
          phase: str = "TEST", quantity: float = 1, symbol: str = "ALTUSDT",
          slippage_bps: float = c259.BASE_SLIPPAGE_BPS,
          reference: float = 100) -> c259.Fill:
    asof = timestamp
    rows = tuple(c259.VolumePoint(asof-(24-i)*c259.HOUR_MS, 1_000_000)
                 for i in range(24))
    execution = reference*(1+side*slippage_bps/10_000)
    computed_fee = abs(quantity*execution)*c259.FEE_BPS/10_000
    computed_slip = abs(quantity)*abs(execution-reference)
    computed_impact = float(c259.impact_drag(Decimal(str(quantity)), Decimal(str(reference)),
                                             24_000_000))
    return c259.Fill(timestamp, phase, symbol, side, quantity, reference, movement,
                     computed_fee if fee == 0 else fee, computed_slip if slip == 0 else slip,
                     computed_impact if impact == 0 else impact, slippage_bps, asof, rows)


def _trade(attempt: str, side: int, symbol: str, entry: int, close: int,
           cash: float, regime: str = "BTC_ABOVE_OR_ON_EMA200",
           incomplete: bool = False) -> c259.ClosedTrade:
    stop, target = ((Decimal("96"), 108.0) if side == 1 else (Decimal("104"), 92.0))
    filters = c259.Filters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"),
                           Decimal("100"), Decimal("5"))
    sizing = c259.size_position(side=side, entry=Decimal("100"), stop=stop,
                                filters=filters, side_book_equity=Decimal("2000"),
                                quote_volume_24h=24_000_000)
    assert sizing is not None
    quantity = float(sizing.quantity)
    exit_reference = 100+cash/(side*quantity)
    entry_bar = _bar(entry, 100, 101, 99, 100)
    exit_bar = _bar(close, exit_reference, exit_reference+1, exit_reference-1,
                    exit_reference)
    return c259.ClosedTrade(attempt, side, symbol, entry, close, regime,
                            (_fill(side=side, timestamp=entry, phase="ENTRY", symbol=symbol,
                                   quantity=quantity),
                             _fill(movement=cash, side=-side, timestamp=close,
                                   phase=("PROTECTION_FAILURE_CLEANUP" if incomplete
                                          else "SCHEDULED"), symbol=symbol,
                                   reference=exit_reference, quantity=quantity)),
                            (), sizing, entry_bar, exit_bar, float(stop), target, incomplete)


def _stress_trade(trade: c259.ClosedTrade) -> c259.ClosedTrade:
    return replace(trade, fills=tuple(_fill(
        movement=fill.movement_cash, side=fill.side, timestamp=fill.timestamp_ms,
        phase=fill.phase, quantity=fill.quantity, symbol=fill.symbol,
        slippage_bps=c259.STRESS_SLIPPAGE_BPS,
        reference=fill.reference) for fill in trade.fills))


def _mark(timestamp: int, symbol: str, price: float = 100) -> c259.PositionMark:
    return c259.make_position_mark(timestamp_ms=timestamp, symbol=symbol, price=price,
                                   source_digest="a"*64)


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


@pytest.mark.parametrize("attack", ["conflicting_cutoff", "empty_pit", "one_cell_pit",
                                     "missing_folds", "coverage_semantics"])
def test_authority_full_schema_hostile_mutations_fail_before_loader(tmp_path: Path,
                                                                   attack: str) -> None:
    repo, pins = _authority_repo(tmp_path, attack)
    called = False
    def loader(_docs: object) -> None:
        nonlocal called
        called = True
    with pytest.raises(c259.IncompleteAuthority):
        c259.run_with_authority(pins, loader, repo=repo)
    assert called is False


@pytest.mark.parametrize("attack", ["invented_head", "wrong_byte", "wrong_content",
                                     "not_protected", "contract_blob", "coverage_gap"])
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
    elif attack == "contract_blob":
        contract_path = repo / c259.CONTRACT_PATH.relative_to(c259.ROOT)
        contract_path.write_text("{}", encoding="utf-8")
        _run(repo, "git", "add", str(contract_path.relative_to(repo)))
        _run(repo, "git", "commit", "-m", "hostile empty contract")
        head = _run(repo, "git", "rev-parse", "HEAD")
        _run(repo, "git", "update-ref", "refs/remotes/origin/master", head)
        pins = replace(pins, protected_head=head)
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
    decision = 1000 * c259.HOUR_MS
    def prices(returns: list[float]) -> list[c259.ClosePrice]:
        values = [100.0]
        for value in returns:
            values.append(values[-1] * math.exp(value))
        return [c259.ClosePrice(decision-(721-i)*c259.HOUR_MS, price)
                for i, price in enumerate(values)]
    btc_prices, alt_prices = prices(btc), prices(alt)
    first = c259.fit_signal(btc_prices, alt_prices,
                            btc_decision=c259.ClosePrice(
                                decision, btc_prices[-1].price*math.exp(.01)),
                            alt_decision=c259.ClosePrice(
                                decision, alt_prices[-1].price*math.exp(.013)))
    second = c259.fit_signal(btc_prices, alt_prices,
                             btc_decision=c259.ClosePrice(
                                 decision, btc_prices[-1].price*math.exp(.02)),
                             alt_decision=c259.ClosePrice(
                                 decision, alt_prices[-1].price*math.exp(.026)))
    assert first.beta0 == second.beta0 and first.gamma == second.gamma
    assert first.gamma >= 0.10 and first.t_gamma >= 2 and first.side == 1 and first.qualifies
    assert second.side == 1 and second.qualifies
    consumed = c259.fit_signal(
        btc_prices, alt_prices,
        btc_decision=c259.ClosePrice(decision, btc_prices[-1].price*math.exp(.01)),
        alt_decision=c259.ClosePrice(
            decision, alt_prices[-1].price * math.exp(first.beta0 * 0.01 + first.forecast)))
    assert consumed.qualifies is False
    with pytest.raises(c259.IncompleteAuthority, match="contiguous prior"):
        broken = btc_prices[:-1] + [replace(btc_prices[-1], close_ms=btc_prices[-1].close_ms+1)]
        c259.fit_signal(broken, alt_prices, btc_decision=c259.ClosePrice(decision, 101),
                        alt_decision=c259.ClosePrice(decision, 101))
    with pytest.raises(c259.IncompleteAuthority, match="non-positive"):
        broken = btc_prices[:-1] + [replace(btc_prices[-1], price=0)]
        c259.fit_signal(broken, alt_prices, btc_decision=c259.ClosePrice(decision, 101),
                        alt_decision=c259.ClosePrice(decision, 101))
    with pytest.raises(c259.IncompleteAuthority, match="decision close"):
        c259.fit_signal(btc_prices, alt_prices,
                        btc_decision=c259.ClosePrice(decision, 101),
                        alt_decision=c259.ClosePrice(decision+c259.HOUR_MS, 101))


def test_signal_sides_are_separate_and_candidate_order_is_deterministic() -> None:
    decision = 1000*c259.HOUR_MS
    rng = np.random.Generator(np.random.PCG64(259))
    btc = list(rng.normal(0, .001, 720)); noise = list(rng.normal(0, .00001, 720))
    alt = [1.3*btc[i]+(.35*btc[i-1] if i else 0)+noise[i] for i in range(720)]
    def closes(values: list[float]) -> list[c259.ClosePrice]:
        prices = [100.0]
        for value in values:
            prices.append(prices[-1]*math.exp(value))
        return [c259.ClosePrice(decision-(721-i)*c259.HOUR_MS, price)
                for i, price in enumerate(prices)]
    bp, ap = closes(btc), closes(alt)
    long_fit = c259.fit_signal(
        bp, ap, btc_decision=c259.ClosePrice(decision, bp[-1].price*math.exp(.01)),
        alt_decision=c259.ClosePrice(decision, ap[-1].price*math.exp(.013)))
    short_fit = c259.fit_signal(
        bp, ap, btc_decision=c259.ClosePrice(decision, bp[-1].price*math.exp(-.01)),
        alt_decision=c259.ClosePrice(decision, ap[-1].price*math.exp(-.013)))
    assert long_fit.qualifies and short_fit.qualifies
    rows = [("ZZZUSDT", long_fit), ("AAAUSDT", long_fit), ("MIDUSDT", long_fit),
            ("SHORTUSDT", short_fit), ("OCCUPIED", long_fit)]
    assert c259.select_candidates(rows, 1, decision_close_ms=decision,
                                  occupied=["OCCUPIED"]) == ("AAAUSDT", "MIDUSDT", "ZZZUSDT")
    assert c259.select_candidates(rows, -1, decision_close_ms=decision) == ("SHORTUSDT",)
    with pytest.raises(c259.IncompleteAuthority, match="stale"):
        c259.select_candidates([("STALE", replace(long_fit, forecast=999))],
                               1, decision_close_ms=decision)
    with pytest.raises(c259.IncompleteAuthority, match="stale"):
        c259.select_candidates([("FORGED", replace(
            long_fit, window_identity_sha256=long_fit.window_identity_sha256.upper()))],
            1, decision_close_ms=decision)
    with pytest.raises(c259.IncompleteAuthority, match="721|stale"):
        c259.select_candidates([("STALE-WINDOW", replace(
            long_fit, prior_btc_closes=long_fit.prior_btc_closes[:-1]))],
            1, decision_close_ms=decision)


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
    with pytest.raises(c259.C259Error, match="collapsed"):
        c259.protection_prices(Decimal("100.01"), Decimal("0.001"), 1, Decimal("1"))


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
    timestamp = 30*c259.HOUR_MS
    volume_bars = [_bar((6+i)*c259.HOUR_MS) for i in range(24)]
    fill = c259.make_fill(timestamp_ms=timestamp, phase="SCHEDULED", symbol="ALT", side=-1,
                          quantity=2, reference=110, movement_from=100,
                          volume_bars=volume_bars)
    assert fill.movement_cash == 20
    assert fill.fee_drag > 0 and fill.slippage_drag > 0 and fill.impact_drag > 0
    intrabar_bars = [_bar((5+i)*c259.HOUR_MS) for i in range(24)]
    intrabar = c259.make_fill(timestamp_ms=timestamp, phase="STOP_RANGE", symbol="ALT",
                              side=-1, quantity=1, reference=95, movement_from=100,
                              volume_bars=intrabar_bars)
    assert intrabar.impact_asof_ms == timestamp-c259.HOUR_MS
    phases = [row[1] for row in c259.ordered_events([
        (1, "ENTRY", "A", None), (1, "FUNDING", "A", None),
        (1, "GAP_OR_SCHEDULED", "A", None),
        (1, "INTRABAR_PRIOR_BOUNDARY", "A", None), (1, "MARK", "A", None)])]
    assert phases == ["INTRABAR_PRIOR_BOUNDARY", "FUNDING", "GAP_OR_SCHEDULED",
                      "ENTRY", "MARK"]


def test_causal_marks_funding_and_fill_volume_fail_on_duplicates_or_gaps() -> None:
    event = 30 * c259.HOUR_MS
    marks = [c259.MarkClose(event-c259.HOUR_MS, 100), c259.MarkClose(event, 999)]
    assert c259.causal_mark_before(marks, event).price == 100
    with pytest.raises(c259.IncompleteAuthority, match="missing/compressed"):
        c259.causal_mark_before([c259.MarkClose(event-2*c259.HOUR_MS, 100)], event)
    with pytest.raises(c259.IncompleteAuthority, match="duplicate"):
        c259.causal_mark_before(marks + [marks[0]], event)
    assert len(c259.validate_funding_events([
        c259.FundingEvent(event, .001), c259.FundingEvent(event+1, -.001)])) == 2
    with pytest.raises(c259.IncompleteAuthority, match="duplicate funding"):
        c259.validate_funding_events([c259.FundingEvent(event, 0),
                                      c259.FundingEvent(event, .001)])
    bars = [_bar((6+i)*c259.HOUR_MS, volume=100+i) for i in range(24)]
    assert c259.causal_quote_volume_24h(bars, event) == sum(100+i for i in range(24))
    with pytest.raises(c259.IncompleteAuthority, match="missing/compressed"):
        c259.causal_quote_volume_24h(bars[:-1], event)
    with pytest.raises(c259.IncompleteAuthority, match="duplicate"):
        c259.causal_quote_volume_24h(bars + [bars[-1]], event)


def test_side_books_are_isolated_and_protection_failure_is_retained() -> None:
    filters = c259.Filters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"),
                           Decimal("100"), Decimal("5"))
    sized = c259.size_position(side=1, entry=Decimal("100"), stop=Decimal("96"),
                               filters=filters, side_book_equity=Decimal("2000"),
                               quote_volume_24h=24_000_000)
    assert sized is not None
    long_book, short_book = c259.SideBook(Decimal("2000")), c259.SideBook(Decimal("2000"))
    long_book.admit("ALT", sized)
    assert "ALT" in long_book.positions and short_book.positions == {}
    for symbol in ("B", "C"):
        long_book.admit(symbol, sized)
    assert not long_book.can_admit("D", sized)
    quantity = float(sized.quantity)
    entry_bar = _bar(c259.HOUR_MS, 100, 101, 99, 100)
    exit_bar = _bar(2*c259.HOUR_MS, 100, 101, 99, 100)
    failed = c259.protection_failure(attempt_id="x", side=1, symbol="ALT",
                                    entry_ms=c259.HOUR_MS, cleanup_ms=2*c259.HOUR_MS, fills=[
                                        _fill(phase="ENTRY", symbol="ALT",
                                              timestamp=c259.HOUR_MS, side=1, quantity=quantity),
                                        _fill(phase="PROTECTION_FAILURE_CLEANUP", symbol="ALT",
                                              timestamp=2*c259.HOUR_MS, side=-1,
                                              quantity=quantity)],
                                    funding_events=[c259.AppliedFunding(
                                        c259.FundingEvent(2*c259.HOUR_MS, .005),
                                        _mark(c259.HOUR_MS, "ALT", 100))],
                                    sizing=sized, entry_bar=entry_bar, exit_bar=exit_bar,
                                    stop=96, target=108,
                                    regime="BTC_ABOVE_OR_ON_EMA200")
    assert failed.incomplete and failed.net_cash < -.5
    with pytest.raises(c259.IncompleteEpisode, match="exactly flat"):
        c259.protection_failure(attempt_id="x", side=1, symbol="ALT",
                                entry_ms=c259.HOUR_MS, cleanup_ms=2*c259.HOUR_MS, fills=[
                                    _fill(phase="ENTRY", symbol="ALT", timestamp=c259.HOUR_MS,
                                          side=1, quantity=quantity),
                                    _fill(phase="PROTECTION_FAILURE_CLEANUP", symbol="ALT",
                                          timestamp=2*c259.HOUR_MS, side=-1,
                                          quantity=quantity/2)],
                                funding_events=[], sizing=sized, entry_bar=entry_bar,
                                exit_bar=exit_bar, stop=96, target=108,
                                regime="BTC_ABOVE_OR_ON_EMA200")
    with pytest.raises(c259.IncompleteEpisode):
        c259.protection_failure(attempt_id="x", side=1, symbol="ALT", entry_ms=1,
                                cleanup_ms=1, fills=[], funding_events=[], sizing=sized,
                                entry_bar=entry_bar, exit_bar=exit_bar, stop=96, target=108,
                                regime="BTC_ABOVE_OR_ON_EMA200")


def test_chronological_marked_drawdown_and_realized_order_are_not_entry_order() -> None:
    # Marked equity falls $100 from a $2,050 peak; close-time realized fall is 2R.
    marked_dd = c259.marked_equity_drawdown_R([(1, 0, 2050), (2, 0, 1950),
                                                (3, 0, 2000)], 2000)
    trades = [_trade("late-entry", 1, "A", 10*c259.HOUR_MS, 22*c259.HOUR_MS, -2),
              _trade("early-entry", 1, "B", c259.HOUR_MS, 13*c259.HOUR_MS, 1)]
    realized_dd = c259.closed_realized_drawdown_R(trades)
    assert marked_dd == pytest.approx(5) and realized_dd > 0
    assert c259.gate_drawdown_R(marked_drawdown_R=marked_dd,
                               realized_drawdown_R=realized_dd) == marked_dd
    with pytest.raises(c259.IncompleteAuthority, match="phase ordered"):
        c259.marked_equity_drawdown_R([(2, 0, 2000), (1, 0, 1990)], 2000)


def test_derived_drawdown_requires_every_open_hour_mark() -> None:
    trade = _trade("marks", 1, "ALT", 10*c259.HOUR_MS, 22*c259.HOUR_MS, 1)
    complete = tuple(_mark(t, "ALT", 100) for t in range(
        trade.entry_ms, trade.close_ms, c259.HOUR_MS))
    points = c259.derive_equity_points((trade,), complete, start_ms=9*c259.HOUR_MS,
                                      end_ms=23*c259.HOUR_MS)
    assert points[-1][0] == trade.close_ms
    with pytest.raises(c259.IncompleteAuthority, match="missing, extra"):
        c259.derive_equity_points((trade,), complete[::2], start_ms=9*c259.HOUR_MS,
                                  end_ms=23*c259.HOUR_MS)


def test_split_boundaries_and_regime_state_reset() -> None:
    assert c259.split_name(1743465600000) == "wf1"
    assert c259.split_name(1790553600000) == "sealed_holdout"
    state = c259.EMA200()
    for i in range(200):
        result = state.update(_bar(i*c259.HOUR_MS, close=100 + i/1000))
    assert result == "BTC_ABOVE_OR_ON_EMA200"
    assert state.update(_bar(205*c259.HOUR_MS, close=100)) is None


def test_metrics_stress_identity_forward_gates_and_drawdown_tiers() -> None:
    base = [_trade("a", 1, "A", c259.HOUR_MS, 13*c259.HOUR_MS, 5),
            _trade("b", 1, "B", 3*c259.HOUR_MS, 15*c259.HOUR_MS, -1)]
    stress = [_stress_trade(trade) for trade in base]
    c259.stress_same_selection(base, stress)
    with pytest.raises(c259.C259Error, match="changed admitted"):
        c259.stress_same_selection(base, stress[:-1])
    with pytest.raises(c259.C259Error, match="changed admitted"):
        c259.stress_same_selection(base, [replace(stress[0], close_ms=3*c259.HOUR_MS), stress[1]])
    with pytest.raises(c259.C259Error, match="changed admitted"):
        changed_fill = replace(stress[0].fills[0], quantity=2)
        c259.stress_same_selection(base, [replace(stress[0], fills=(changed_fill,)), stress[1]])
    zero_cost = replace(stress[0].fills[0], fee_drag=0, slippage_drag=0, impact_drag=0)
    with pytest.raises(c259.IncompleteEpisode, match="recompute"):
        c259.stress_same_selection(base, [replace(stress[0], fills=(zero_cost, stress[0].fills[1])),
                                          stress[1]])
    missing_volume = replace(base[0].fills[0], volume_rows=base[0].fills[0].volume_rows[:-1])
    with pytest.raises(c259.IncompleteEpisode, match="volume/slippage"):
        c259.metrics([replace(base[0], fills=(missing_volume, base[0].fills[1]))])
    stale_rows = tuple(replace(row, open_ms=row.open_ms-c259.HOUR_MS)
                       for row in base[0].fills[0].volume_rows)
    stale_fill = replace(base[0].fills[0], impact_asof_ms=base[0].fills[0].impact_asof_ms-c259.HOUR_MS,
                         volume_rows=stale_rows)
    with pytest.raises(c259.IncompleteEpisode, match="volume/slippage"):
        c259.metrics([replace(base[0], fills=(stale_fill, base[0].fills[1]))])
    forged_movement = replace(base[0].fills[1], movement_cash=999)
    with pytest.raises(c259.IncompleteEpisode, match="movement"):
        c259.metrics([replace(base[0], fills=(base[0].fills[0], forged_movement))])
    bad_funding = c259.AppliedFunding(c259.FundingEvent(base[0].entry_ms, .001),
                                     _mark(base[0].entry_ms-2*c259.HOUR_MS, "A", 100))
    with pytest.raises(c259.IncompleteEpisode, match="funding"):
        c259.metrics([replace(base[0], funding_events=(bad_funding,))])
    forged_risk = replace(base[0].sizing,
                          initial_risk_cash=base[0].sizing.initial_risk_cash/2)
    with pytest.raises(c259.IncompleteEpisode, match="initial risk"):
        c259.metrics([replace(base[0], sizing=forged_risk)])
    early = _trade("early", 1, "EARLY", c259.HOUR_MS, 2*c259.HOUR_MS, 1)
    with pytest.raises(c259.IncompleteEpisode, match="12h"):
        c259.metrics([early])
    gap = _trade("gap", 1, "GAP", c259.HOUR_MS, 13*c259.HOUR_MS, -1)
    forged_gap_fill = _fill(movement=-float(gap.sizing.quantity)*5, side=-1,
                            timestamp=gap.close_ms, phase="STOP_GAP", symbol="GAP",
                            reference=95.0, quantity=float(gap.sizing.quantity))
    forged_gap = replace(gap, fills=(gap.fills[0], forged_gap_fill),
                         exit_bar=_bar(gap.close_ms, 96, 101, 94, 96))
    with pytest.raises(c259.IncompleteEpisode, match="canonical bar/protection"):
        c259.metrics([forged_gap])
    report = c259.metrics(base)
    assert report["closed"] == 2 and report["profit_factor"] > 1.5
    with pytest.raises(c259.C259Error, match="wf1/wf2/wf3"):
        c259.forward_gate([])
    folds = []
    for name, (start, _end) in c259.FOLD_RANGES.items():
        if name == "development":
            continue
        trade = _trade(name, 1, f"{name}USDT", start+c259.HOUR_MS,
                       start+13*c259.HOUR_MS, 1)
        stressed = _stress_trade(trade)
        marks = tuple(_mark(timestamp, trade.symbol, 100)
                      for timestamp in range(trade.entry_ms, trade.close_ms, c259.HOUR_MS))
        folds.append(c259.FoldLifecycle(name, (trade,), (stressed,), marks))
    derived = c259.forward_gate(folds, bootstrap_resamples=25)
    assert derived["pass"] is False and derived["base"]["closed"] == 3
    assert "sample" in derived["failures"] and "monthly_frequency" in derived["failures"]
    broken_marks = replace(folds[0], marks=())
    with pytest.raises(c259.IncompleteAuthority, match="missing, extra"):
        c259.forward_gate([broken_marks, *folds[1:]], bootstrap_resamples=25)
    funded = folds[0].base[0]
    event_ms = funded.entry_ms+2*c259.HOUR_MS
    conflicting = c259.AppliedFunding(c259.FundingEvent(event_ms, .001),
                                      c259.make_position_mark(
                                          timestamp_ms=event_ms-c259.HOUR_MS,
                                          symbol=funded.symbol, price=100,
                                          source_digest="b"*64))
    funded_base = replace(funded, funding_events=(conflicting,))
    funded_stress = _stress_trade(funded_base)
    conflicting_fold = replace(folds[0], base=(funded_base,), stress=(funded_stress,))
    with pytest.raises(c259.IncompleteAuthority, match="conflicts"):
        c259.forward_gate([conflicting_fold, *folds[1:]], bootstrap_resamples=25)
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
    with pytest.raises(c259.C259Error, match="side"):
        c259.bootstrap_stats(values, side=0, resamples=10)
