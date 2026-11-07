from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import subprocess
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from tools.research import btc_residual_leadlag_continuation_c259_stagea as c259

DEV_START = c259.ALL_SPLIT_RANGES["development"][0]
SOURCE_DIGEST = "a"*64
SOURCE_AUTHORITY: c259.ProtectedAuthority | None = None
SOURCE_ARCHIVE: c259.ProtectedSourceArchive | None = None
VALID_ENTRY = DEV_START+26*c259.HOUR_MS


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


def _zip_csv(name: str, rows: list[list[object]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, "\n".join(",".join(str(value) for value in row)
                                           for row in rows) + "\n")
    return buffer.getvalue()


def _authority_repo(tmp_path: Path, schema_attack: str | None = None,
                    source_records: list[dict] | None = None) -> tuple[Path, c259.AuthorityPins]:
    repo = tmp_path / "authority"
    _run(tmp_path, "git", "clone", "--shared", "--no-checkout", str(c259.ROOT), str(repo))
    # Populate only the index: no market/outcome files need a working-tree copy.
    _run(repo, "git", "update-ref", "--no-deref", "HEAD", c259.CONTRACT_HEAD)
    _run(repo, "git", "read-tree", c259.CONTRACT_HEAD)
    _run(repo, "git", "config", "user.email", "c259@example.invalid")
    _run(repo, "git", "config", "user.name", "C259 Synthetic")

    manifest = {"format": "zb-binance-um-archive-manifest/6",
                "cutoff_exclusive_ms": 1793836800000,
                "active_base_files_v5": source_records or [], "files_v6": []}
    if schema_attack == "conflicting_cutoff":
        manifest["cutoff_exclusive_ms"] = 1793836800001
    manifest_raw = _write_document(repo, c259.EXPECTED_PATHS["manifest_v6"], manifest,
                                   compressed=True)
    members = [f"SYM{index:02d}USDT" for index in range(20)]
    pit = {"format": "zb-pit-universe/2", "manifest_digest": manifest["digest"],
           "weeks": [{"monday_ms": monday,
                       "books": {"crypto": {"eligible": 20, "members": members,
                                              "qv30d_usdt": ["1"]*20}}}
                     for monday in range(1580688000000, 1793577600000+1,
                                         7*24*c259.HOUR_MS)]}
    if schema_attack == "empty_pit":
        pit["weeks"] = []
    elif schema_attack == "one_cell_pit":
        pit["weeks"] = pit["weeks"][-1:]
    pit_raw = _write_document(repo, c259.EXPECTED_PATHS["pit_v9"], pit)
    filters = {"authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY",
               "version": 4, "pit_universe_sha256": hashlib.sha256(pit_raw).hexdigest(),
               "pit_universe_digest": pit["digest"], "fold_bindings": {
                   "development_eligible_segment": "2024-12-13T04:44:48Z",
                   "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
                   "wf3": "2026-02-17T04:13:28Z",
                   "sealed_holdout": "2026-02-17T04:13:28Z"},
               "weekly_membership": {name: [{"monday_utc": "synthetic",
                                               "members": members}]
                                      for name in ("development_eligible_segment", "wf1",
                                                   "wf2", "wf3", "sealed_holdout")}}
    if schema_attack == "missing_folds":
        filters.pop("fold_bindings")
    filter_raw = _write_document(repo, c259.EXPECTED_PATHS["filter_v4"], filters)
    coverage = {"format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
                "authority_version": 4,
                "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
                "guaranteed_period_gaps": 0, "gaps": [],
                "guaranteed_period_gaps_by_stream": {
                    "klines:1d": 0, "klines:1h": 0,
                    "markPriceKlines:1h": 0, "fundingRate:null": 0},
                "required_streams": ["klines:1d", "klines:1h", "markPriceKlines:1h",
                                     "fundingRate:null"],
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


@pytest.fixture(scope="module", autouse=True)
def _protected_source_issuer(tmp_path_factory: pytest.TempPathFactory) -> None:
    global SOURCE_AUTHORITY, SOURCE_ARCHIVE, SOURCE_DIGEST
    repo, pins = _authority_repo(tmp_path_factory.mktemp("c259-source"))
    SOURCE_AUTHORITY = c259.validate_authority_pins(pins, repo=repo)
    SOURCE_ARCHIVE = SOURCE_AUTHORITY.open_source(repo)
    SOURCE_DIGEST = SOURCE_AUTHORITY.source_digest


def _bar(open_ms: int, open_: float = 100, high: float = 102, low: float = 98,
         close: float = 101, volume: float = 1_000_000) -> c259.Bar:
    return c259.Bar(open_ms, open_, high, low, close, volume)


def _fill(*, movement: float = 0, fee: float = 0, slip: float = 0,
          impact: float = 0, side: int = 1, timestamp: int = 1,
          phase: str = "TEST", quantity: float = 1, symbol: str = "ALTUSDT",
          slippage_bps: float = c259.BASE_SLIPPAGE_BPS,
          reference: float = 100) -> c259.Fill:
    asof = timestamp-c259.HOUR_MS if phase in {"STOP_RANGE", "TARGET_RANGE"} else timestamp
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


def _volume_rows(asof: int, volume: float = 1_000_000) -> tuple[c259.VolumePoint, ...]:
    return tuple(c259.VolumePoint(timestamp, volume)
                 for timestamp in range(asof-24*c259.HOUR_MS, asof, c259.HOUR_MS))


def _atr_bars(decision_close_ms: int, split_start_ms: int = DEV_START) -> tuple[c259.Bar, ...]:
    return tuple(_bar(timestamp, 100, 101, 99, 100)
                 for timestamp in range(split_start_ms,
                                        decision_close_ms, c259.HOUR_MS))


def _synthetic_source_seal(*, attempt_id: str, symbol: str, entry_ms: int,
                           close_ms: int, split_start_ms: int, split_end_ms: int,
                           atr_bars, funding_authority, lifecycle_bars, fills,
                           funding_marks, lifecycle_marks) -> c259.ProtectedSourceSeal:
    """Test-only issuance for arithmetic fixtures; never exposed by production API.

    Fixed-archive tests below exercise the actual protected-source boundary.
    """
    seal = c259.ProtectedSourceSeal(
        SOURCE_DIGEST, attempt_id, symbol, entry_ms, close_ms,
        split_start_ms, split_end_ms,
        c259.atr_tape_digest(source_digest=SOURCE_DIGEST, split_start_ms=split_start_ms,
                             decision_close_ms=entry_ms-c259.HOUR_MS, bars=atr_bars),
        c259.funding_tape_digest(source_digest=SOURCE_DIGEST, symbol=symbol,
                                 split_start_ms=split_start_ms, split_end_ms=split_end_ms,
                                 events=funding_authority),
        c259._stream_digest(SOURCE_DIGEST, f"lifecycle:{symbol}", lifecycle_bars),
        tuple((fill.timestamp_ms, fill.phase, c259._stream_digest(
            SOURCE_DIGEST, f"volume:{symbol}:{fill.timestamp_ms}:{fill.phase}",
            fill.volume_rows)) for fill in fills),
        c259._stream_digest(SOURCE_DIGEST, f"funding-marks:{symbol}", funding_marks),
        c259._stream_digest(SOURCE_DIGEST, f"lifecycle-marks:{symbol}", lifecycle_marks),
        c259._SOURCE_SEAL_CAPABILITY)
    c259._ISSUED_SOURCE_SEALS[id(seal)] = seal
    return seal


def _trade(attempt: str, side: int, symbol: str, entry: int, close: int,
           cash: float, regime: str = "BTC_ABOVE_OR_ON_EMA200",
           incomplete: bool = False,
           funding_authority: tuple[c259.FundingEvent, ...] = ()) -> c259.ClosedTrade:
    decision = entry-c259.HOUR_MS
    split = c259.split_name(decision)
    split_start, split_end = c259.ALL_SPLIT_RANGES[split]
    atr_bars = _atr_bars(decision, split_start)
    atr_digest = c259.atr_tape_digest(
        source_digest=SOURCE_DIGEST, split_start_ms=split_start,
        decision_close_ms=decision, bars=atr_bars)
    funding_digest = c259.funding_tape_digest(
        source_digest=SOURCE_DIGEST, symbol=symbol, split_start_ms=split_start,
        split_end_ms=split_end, events=funding_authority)
    stop, target_decimal = c259.protection_prices(
        Decimal("100"), Decimal(str(c259.atr24_wilder(atr_bars, decision))), side,
        Decimal("0.01"))
    target = float(target_decimal)
    filters = c259.Filters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"),
                           Decimal("100"), Decimal("5"))
    sizing = c259.size_position(side=side, entry=Decimal("100"), stop=stop,
                                filters=filters, side_book_equity=Decimal("2000"),
                                entry_impact_asof_ms=entry,
                                entry_volume_rows=_volume_rows(entry))
    assert sizing is not None
    quantity = float(sizing.quantity)
    exit_reference = 100+cash/(side*quantity)
    lifecycle = tuple(_bar(timestamp, 100, 101, 99, 100)
                      for timestamp in range(entry, close+c259.HOUR_MS, c259.HOUR_MS))
    lifecycle = lifecycle[:-1] + (_bar(close, exit_reference, exit_reference+1,
                                        exit_reference-1, exit_reference),)
    entry_bar, exit_bar = lifecycle[0], lifecycle[-1]
    fills = (_fill(side=side, timestamp=entry, phase="ENTRY", symbol=symbol,
                   quantity=quantity),
             _fill(movement=cash, side=-side, timestamp=close,
                   phase=("PROTECTION_FAILURE_CLEANUP" if incomplete else "SCHEDULED"),
                   symbol=symbol, reference=exit_reference, quantity=quantity))
    lifecycle_marks = tuple(_mark(timestamp, symbol, 100)
                            for timestamp in range(entry, close, c259.HOUR_MS))
    assert SOURCE_ARCHIVE is not None
    source_seal = _synthetic_source_seal(
        attempt_id=attempt, symbol=symbol, entry_ms=entry, close_ms=close,
        split_start_ms=split_start, split_end_ms=split_end, atr_bars=atr_bars,
        funding_authority=funding_authority, lifecycle_bars=lifecycle, fills=fills,
        funding_marks=(), lifecycle_marks=lifecycle_marks)
    return c259.ClosedTrade(attempt, side, symbol, entry, close, regime,
                            fills,
                            (), sizing, decision, split_start, SOURCE_DIGEST, source_seal,
                            atr_digest, atr_bars, funding_authority, funding_digest, lifecycle,
                            entry_bar, exit_bar, float(stop), target, incomplete)


def _stress_trade(trade: c259.ClosedTrade) -> c259.ClosedTrade:
    return replace(trade, fills=tuple(_fill(
        movement=fill.movement_cash, side=fill.side, timestamp=fill.timestamp_ms,
        phase=fill.phase, quantity=fill.quantity, symbol=fill.symbol,
        slippage_bps=c259.STRESS_SLIPPAGE_BPS,
        reference=fill.reference) for fill in trade.fills))


def _reseal_trade(trade: c259.ClosedTrade) -> c259.ClosedTrade:
    assert SOURCE_ARCHIVE is not None
    split = c259.split_name(trade.decision_close_ms)
    split_start, split_end = c259.ALL_SPLIT_RANGES[split]
    lifecycle_marks = tuple(
        _mark(bar.open_ms, trade.symbol, bar.close)
        for bar in trade.lifecycle_bars if bar.open_ms < trade.close_ms)
    seal = _synthetic_source_seal(
        attempt_id=trade.attempt_id, symbol=trade.symbol,
        entry_ms=trade.entry_ms, close_ms=trade.close_ms,
        split_start_ms=split_start, split_end_ms=split_end,
        atr_bars=trade.atr_bars, funding_authority=trade.funding_authority,
        lifecycle_bars=trade.lifecycle_bars, fills=trade.fills,
        funding_marks=tuple(item.mark for item in trade.funding_events),
        lifecycle_marks=lifecycle_marks)
    return replace(trade, authority_source_digest=seal.source_digest,
                   source_seal=seal, atr_authority_digest=seal.atr_digest,
                   funding_authority_digest=seal.funding_digest)


def _mark(timestamp: int, symbol: str, price: float = 100) -> c259.PositionMark:
    return c259.make_position_mark(timestamp_ms=timestamp, symbol=symbol, price=price,
                                   source_digest=SOURCE_DIGEST)


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


@pytest.mark.parametrize("funding_format", ["zip", "api_json"])
def test_protected_source_loader_rejects_omit_mutate_and_cross_source(
        tmp_path: Path, funding_format: str) -> None:
    symbol = "ALT"
    split_start, split_end = c259.ALL_SPLIT_RANGES["development"]
    entry, close = split_start+26*c259.HOUR_MS, split_start+38*c259.HOUR_MS
    timestamps = tuple(range(split_start-24*c259.HOUR_MS,
                             close+c259.HOUR_MS, c259.HOUR_MS))
    kline_rows = [[timestamp, 100, 101, 99, 100, 1, timestamp+c259.HOUR_MS-1,
                   1_000_000, 1, 1, 1, 0] for timestamp in timestamps]
    mark_rows = [[timestamp, 100, 101, 99, 100, 1, timestamp+c259.HOUR_MS-1,
                  1_000_000, 1, 1, 1, 0] for timestamp in timestamps]
    funding_time = entry+c259.HOUR_MS
    funding_rows = [[funding_time, 0, "0.001"]]
    payloads = {
        "um/monthly/klines/ALT/1h/ALT-1h-test.zip": _zip_csv("klines.csv", kline_rows),
        "um/monthly/markPriceKlines/ALT/1h/ALT-1h-test.zip": _zip_csv(
            "marks.csv", mark_rows),
        "um/monthly/fundingRate/ALT/ALT-fundingRate-test.zip": _zip_csv(
            "funding.csv", funding_rows),
    }
    if funding_format == "api_json":
        payloads.pop("um/monthly/fundingRate/ALT/ALT-fundingRate-test.zip")
        payloads["um/api/fundingRate/ALT/ALT-fundingRate-test.json"] = json.dumps([
            {"fundingTime": funding_time, "fundingRate": "0.001", "symbol": symbol}
        ]).encode()
    records = []
    for path, raw in payloads.items():
        if "/klines/" in path:
            series, interval = "klines", "1h"
        elif "/markPriceKlines/" in path:
            series, interval = "markPriceKlines", "1h"
        else:
            series, interval = "fundingRate", None
        records.append({"path": path, "series": series, "interval": interval,
                        "symbol": symbol, "raw_sha256": hashlib.sha256(raw).hexdigest()})
    repo, pins = _authority_repo(tmp_path, source_records=records)
    store = tmp_path / "store"
    for path, raw in payloads.items():
        target = store / Path(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    authority = c259.validate_authority_pins(pins, repo=repo)
    archive = authority.open_source(store)
    bars = tuple(_bar(timestamp, 100, 101, 99, 100)
                 for timestamp in timestamps)
    by_time = {bar.open_ms: bar for bar in bars}
    atr = tuple(by_time[timestamp] for timestamp in range(
        split_start, entry-c259.HOUR_MS, c259.HOUR_MS))
    lifecycle = tuple(by_time[timestamp] for timestamp in range(
        entry, close+c259.HOUR_MS, c259.HOUR_MS))
    fills = (_fill(timestamp=entry, phase="ENTRY", symbol=symbol),
             _fill(timestamp=close, phase="SCHEDULED", symbol=symbol, side=-1))
    funding = (c259.FundingEvent(funding_time, .001),)
    funding_marks = (c259.make_position_mark(
        timestamp_ms=funding_time-c259.HOUR_MS, symbol=symbol, price=100.0,
        source_digest=authority.source_digest),)
    lifecycle_marks = tuple(c259.make_position_mark(
        timestamp_ms=timestamp, symbol=symbol, price=100.0,
        source_digest=authority.source_digest)
        for timestamp in range(entry, close, c259.HOUR_MS))
    seal = archive.seal_trade(
        attempt_id="source", symbol=symbol, entry_ms=entry, close_ms=close,
        split_start_ms=split_start, split_end_ms=split_end, atr_bars=atr,
        funding_authority=funding, lifecycle_bars=lifecycle, fills=fills,
        funding_marks=funding_marks, lifecycle_marks=lifecycle_marks)
    assert seal.source_digest == authority.source_digest
    trade = _trade("source", 1, symbol, entry, close, 0)
    trade = replace(trade, authority_source_digest=authority.source_digest,
                    source_seal=seal, atr_authority_digest=seal.atr_digest,
                    funding_authority=funding, funding_authority_digest=seal.funding_digest,
                    lifecycle_bars=lifecycle, entry_bar=lifecycle[0], exit_bar=lifecycle[-1],
                    funding_events=(c259.AppliedFunding(funding[0], funding_marks[0]),))
    c259.validate_closed_trade(trade)
    omitted_digest = c259.funding_tape_digest(
        source_digest=authority.source_digest, symbol=symbol, split_start_ms=split_start,
        split_end_ms=split_end, events=())
    with pytest.raises(c259.IncompleteAuthority, match="funding tape"):
        c259.validate_closed_trade(replace(trade, funding_authority=(), funding_events=(),
                                           funding_authority_digest=omitted_digest))
    altered_atr = atr[:-1]+(replace(atr[-1], close=100.5),)
    altered_digest = c259.atr_tape_digest(
        source_digest=authority.source_digest, split_start_ms=split_start,
        decision_close_ms=entry-c259.HOUR_MS, bars=altered_atr)
    with pytest.raises(c259.IncompleteAuthority, match="ATR"):
        c259.validate_closed_trade(replace(trade, atr_bars=altered_atr,
                                           atr_authority_digest=altered_digest))
    # An arbitrary authority callback cannot sign its own rows. Returned documents
    # are copies, so replacing a manifest record after validation changes nothing.
    assert not hasattr(authority, "seal_trade")
    assert not hasattr(archive, "_issue_seal")
    class ArbitraryLoader(c259.ProtectedSourceArchive):
        def _load_symbol(self, _symbol):
            pytest.fail("an arbitrary loader must not execute at the seal boundary")
    arbitrary = ArbitraryLoader(authority, store, authority._capability)
    with pytest.raises(c259.IncompleteAuthority, match="protected-tree"):
        arbitrary.seal_trade(
            attempt_id="source", symbol=symbol, entry_ms=entry, close_ms=close,
            split_start_ms=split_start, split_end_ms=split_end, atr_bars=atr,
            funding_authority=funding, lifecycle_bars=lifecycle, fills=fills,
            funding_marks=funding_marks, lifecycle_marks=lifecycle_marks)
    manifest_copy = authority["manifest_v6"]
    manifest_copy["active_base_files_v5"].clear()
    assert authority["manifest_v6"]["active_base_files_v5"] == records
    with pytest.raises(c259.IncompleteAuthority, match="protected-tree"):
        replace(authority, source_digest="b"*64).open_source(store)
    with pytest.raises(c259.IncompleteAuthority, match="checksum-verified"):
        archive.seal_trade(
            attempt_id="source", symbol=symbol, entry_ms=entry, close_ms=close,
            split_start_ms=split_start, split_end_ms=split_end, atr_bars=atr,
            funding_authority=(), lifecycle_bars=lifecycle, fills=fills,
            funding_marks=(), lifecycle_marks=lifecycle_marks)
    mutated_atr = atr[:-1] + (replace(atr[-1], close=100.5),)
    with pytest.raises(c259.IncompleteAuthority, match="checksum-verified"):
        archive.seal_trade(
            attempt_id="source", symbol=symbol, entry_ms=entry, close_ms=close,
            split_start_ms=split_start, split_end_ms=split_end, atr_bars=mutated_atr,
            funding_authority=funding, lifecycle_bars=lifecycle, fills=fills,
            funding_marks=funding_marks, lifecycle_marks=lifecycle_marks)
    cross_source_mark = replace(funding_marks[0], source_digest="b"*64)
    with pytest.raises(c259.IncompleteAuthority, match="checksum-verified"):
        archive.seal_trade(
            attempt_id="source", symbol=symbol, entry_ms=entry, close_ms=close,
            split_start_ms=split_start, split_end_ms=split_end, atr_bars=atr,
            funding_authority=funding, lifecycle_bars=lifecycle, fills=fills,
            funding_marks=(cross_source_mark,), lifecycle_marks=lifecycle_marks)
    with pytest.raises(c259.IncompleteAuthority, match="checksum-verified"):
        archive.seal_trade(
            attempt_id="source", symbol=symbol, entry_ms=entry, close_ms=close,
            split_start_ms=split_start, split_end_ms=split_end, atr_bars=atr,
            funding_authority=funding, lifecycle_bars=lifecycle, fills=fills,
            funding_marks=funding_marks,
            lifecycle_marks=(replace(lifecycle_marks[0], source_digest="b"*64),
                             *lifecycle_marks[1:]))
    # Fresh loaders must recheck fixed byte identities; caller-provided digests
    # or edits to a returned manifest cannot bless a rewritten source archive.
    kline_path = next(path for path in payloads if "/klines/" in path)
    (store / kline_path).write_bytes(payloads[kline_path]+b"tampered")
    with pytest.raises(c259.IncompleteAuthority, match="source byte mismatch"):
        authority.open_source(store).seal_trade(
            attempt_id="source", symbol=symbol, entry_ms=entry, close_ms=close,
            split_start_ms=split_start, split_end_ms=split_end, atr_bars=atr,
            funding_authority=funding, lifecycle_bars=lifecycle, fills=fills,
            funding_marks=funding_marks, lifecycle_marks=lifecycle_marks)


def test_recomputed_caller_hashes_cannot_replace_sealed_source() -> None:
    entry, close = VALID_ENTRY, VALID_ENTRY+12*c259.HOUR_MS
    event = c259.FundingEvent(entry+c259.HOUR_MS, .001)
    trade = _trade("redigest", 1, "ALT", entry, close, 1, funding_authority=(event,))
    funded = _reseal_trade(replace(trade, funding_events=(c259.AppliedFunding(
        event, _mark(event.timestamp_ms-c259.HOUR_MS, "ALT")),)))
    c259.validate_closed_trade(funded)
    start, end = c259.ALL_SPLIT_RANGES["development"]
    missing_funding = replace(
        funded, funding_authority=(), funding_events=(),
        funding_authority_digest=c259.funding_tape_digest(
            source_digest=SOURCE_DIGEST, symbol="ALT", split_start_ms=start,
            split_end_ms=end, events=()))
    with pytest.raises(c259.IncompleteAuthority, match="funding tape"):
        c259.validate_closed_trade(missing_funding)
    changed_atr = funded.atr_bars[:-1]+(replace(funded.atr_bars[-1], close=100.5),)
    changed_digest = c259.atr_tape_digest(
        source_digest=SOURCE_DIGEST, split_start_ms=start,
        decision_close_ms=funded.decision_close_ms, bars=changed_atr)
    with pytest.raises(c259.IncompleteAuthority, match="ATR"):
        c259.validate_closed_trade(replace(funded, atr_bars=changed_atr,
                                           atr_authority_digest=changed_digest))
    # Cloning a genuine issued seal and editing its expected digest is untrusted.
    forged_seal = replace(funded.source_seal, atr_digest=changed_digest)
    with pytest.raises(c259.IncompleteAuthority, match="protected source"):
        c259.validate_closed_trade(replace(funded, source_seal=forged_seal,
                                           atr_bars=changed_atr,
                                           atr_authority_digest=changed_digest))
    bad_mark = c259.make_position_mark(timestamp_ms=entry, symbol="ALT", price=100,
                                       source_digest="b"*64)
    with pytest.raises(c259.IncompleteEpisode, match="funding"):
        c259.validate_closed_trade(replace(funded, funding_events=(c259.AppliedFunding(
            event, bad_mark),)))
    lifecycle_marks = tuple(_mark(timestamp, "ALT") for timestamp in range(entry, close,
                                                                                c259.HOUR_MS))
    with pytest.raises(c259.IncompleteAuthority, match="protected source"):
        c259.derive_equity_points((funded,),
            (bad_mark, *lifecycle_marks[1:]),
            start_ms=entry, end_ms=close)


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
        contract_path.parent.mkdir(parents=True, exist_ok=True)
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
                               entry_impact_asof_ms=25*c259.HOUR_MS,
                               entry_volume_rows=_volume_rows(25*c259.HOUR_MS,
                                                              10_000_000/24))
    assert sized is not None
    assert sized.quantity % filters.step == 0
    assert sized.initial_risk_cash <= Decimal("20")
    assert sized.raw_notional <= Decimal("1000")
    assert c259.size_position(side=1, entry=Decimal("100"), stop=Decimal("99.99"),
                              filters=replace(filters, min_notional=Decimal("5000")),
                              side_book_equity=Decimal("2000"),
                              entry_impact_asof_ms=25*c259.HOUR_MS,
                              entry_volume_rows=_volume_rows(25*c259.HOUR_MS,
                                                             1000/24)) is None
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


def test_closed_trade_replays_full_tape_protection_and_earliest_exit() -> None:
    entry = DEV_START+30*c259.HOUR_MS
    scheduled = _trade("scheduled", 1, "ALT", entry, entry+12*c259.HOUR_MS, 1)
    assert c259.metrics((scheduled,))["closed"] == 1
    quantity = float(scheduled.sizing.quantity)
    for suffix_length in (25, 26):
        suffix = scheduled.atr_bars[-suffix_length:]
        suffix_digest = c259.atr_tape_digest(
            source_digest=SOURCE_DIGEST, split_start_ms=DEV_START,
            decision_close_ms=scheduled.decision_close_ms, bars=suffix)
        with pytest.raises(c259.IncompleteAuthority, match="split-bound"):
            c259.metrics((replace(scheduled, atr_bars=suffix,
                                  atr_authority_digest=suffix_digest),))

    target_ms = entry+2*c259.HOUR_MS
    target_bar = _bar(target_ms, 109, 110, 107, 109)
    target_fill = _fill(movement=quantity*8, side=-1, timestamp=target_ms,
                        phase="TARGET_GAP", symbol="ALT", reference=108,
                        quantity=quantity)
    target_trade = _reseal_trade(replace(
        scheduled, attempt_id="target-gap", close_ms=target_ms,
        fills=(scheduled.fills[0], target_fill),
        lifecycle_bars=scheduled.lifecycle_bars[:2]+(target_bar,), exit_bar=target_bar))
    assert c259.metrics((target_trade,))["closed"] == 1
    boundary_funding = c259.AppliedFunding(
        c259.FundingEvent(target_ms, .001), _mark(target_ms-c259.HOUR_MS, "ALT"))
    target_authority = (boundary_funding.event,)
    funded_target = _reseal_trade(replace(
        target_trade, funding_events=(boundary_funding,),
        funding_authority=target_authority))
    with pytest.raises(c259.IncompleteEpisode, match="exactly cover"):
        c259.metrics((replace(funded_target, funding_events=()),))
    assert c259.metrics((funded_target,))["closed"] == 1

    collision_open = entry+2*c259.HOUR_MS
    collision_bar = _bar(collision_open, 100, 109, 95, 100)
    stop_fill = _fill(movement=-quantity*4, side=-1,
                      timestamp=collision_open+c259.HOUR_MS, phase="STOP_RANGE",
                      symbol="ALT", reference=96, quantity=quantity)
    collision_trade = _reseal_trade(replace(
        scheduled, attempt_id="collision-stop", close_ms=collision_open+c259.HOUR_MS,
        fills=(scheduled.fills[0], stop_fill),
        lifecycle_bars=scheduled.lifecycle_bars[:2]+(collision_bar,),
        exit_bar=collision_bar))
    assert c259.metrics((collision_trade,))["closed"] == 1
    range_boundary_funding = c259.AppliedFunding(
        c259.FundingEvent(collision_trade.close_ms, .001),
        _mark(collision_trade.close_ms-c259.HOUR_MS, "ALT"))
    range_authority = (range_boundary_funding.event,)
    funded_collision = _reseal_trade(replace(
        collision_trade, funding_events=(range_boundary_funding,),
        funding_authority=range_authority))
    with pytest.raises(c259.IncompleteEpisode, match="funding"):
        c259.metrics((funded_collision,))

    with pytest.raises(c259.IncompleteEpisode, match="stop/target"):
        c259.metrics((replace(scheduled, target=999999),))
    with pytest.raises(c259.IncompleteAuthority, match="missing"):
        c259.metrics((replace(scheduled, lifecycle_bars=(
            scheduled.lifecycle_bars[0], scheduled.lifecycle_bars[-1])),))
    forged_volume = replace(scheduled.sizing, quote_volume_24h=1_000_000_000)
    with pytest.raises(c259.IncompleteEpisode, match="24-row fsum"):
        c259.metrics((replace(scheduled, sizing=forged_volume),))


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
        (c259.HOUR_MS, "ENTRY", "A", "x", None),
        (c259.HOUR_MS, "FUNDING", "A", "x", None),
        (c259.HOUR_MS, "GAP_OR_SCHEDULED", "A", "x", None),
        (c259.HOUR_MS, "INTRABAR_PRIOR_BOUNDARY", "A", "x", None),
        (c259.HOUR_MS, "MARK", "A", "x", None)])]
    assert phases == ["INTRABAR_PRIOR_BOUNDARY", "FUNDING", "GAP_OR_SCHEDULED",
                      "ENTRY", "MARK"]
    same_symbol = c259.ordered_events([
        (c259.HOUR_MS, "ENTRY", "A", "z-attempt", None),
        (c259.HOUR_MS, "ENTRY", "A", "a-attempt", None)])
    assert [row[3] for row in same_symbol] == ["a-attempt", "z-attempt"]
    with pytest.raises(c259.IncompleteAuthority, match="duplicate"):
        c259.ordered_events([same_symbol[0], same_symbol[0]])


def test_causal_marks_funding_and_fill_volume_fail_on_duplicates_or_gaps() -> None:
    event = 30 * c259.HOUR_MS
    marks = [c259.MarkClose(event-c259.HOUR_MS, 100), c259.MarkClose(event, 999)]
    assert c259.causal_mark_before(marks, event).price == 100
    with pytest.raises(c259.IncompleteAuthority, match="missing/compressed"):
        c259.causal_mark_before([c259.MarkClose(event-2*c259.HOUR_MS, 100)], event)
    with pytest.raises(c259.IncompleteAuthority, match="duplicate"):
        c259.causal_mark_before(marks + [marks[0]], event)
    assert len(c259.validate_funding_events([
        c259.FundingEvent(event, .001),
        c259.FundingEvent(event+c259.HOUR_MS, -.001)])) == 2
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
    entry_ms, cleanup_ms = VALID_ENTRY, VALID_ENTRY+c259.HOUR_MS
    filters = c259.Filters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"),
                           Decimal("100"), Decimal("5"))
    sized = c259.size_position(side=1, entry=Decimal("100"), stop=Decimal("96"),
                               filters=filters, side_book_equity=Decimal("2000"),
                               entry_impact_asof_ms=entry_ms,
                               entry_volume_rows=_volume_rows(entry_ms))
    assert sized is not None
    long_book, short_book = c259.SideBook(Decimal("2000")), c259.SideBook(Decimal("2000"))
    long_book.admit("ALT", sized)
    assert "ALT" in long_book.positions and short_book.positions == {}
    for symbol in ("B", "C"):
        long_book.admit(symbol, sized)
    assert not long_book.can_admit("D", sized)
    quantity = float(sized.quantity)
    decision = entry_ms-c259.HOUR_MS
    atr_bars = _atr_bars(decision)
    atr_digest = c259.atr_tape_digest(
        source_digest=SOURCE_DIGEST, split_start_ms=DEV_START,
        decision_close_ms=decision, bars=atr_bars)
    funding_authority = (c259.FundingEvent(cleanup_ms, .005),)
    funding_digest = c259.funding_tape_digest(
        source_digest=SOURCE_DIGEST, symbol="ALT", split_start_ms=DEV_START,
        split_end_ms=c259.ALL_SPLIT_RANGES["development"][1],
        events=funding_authority)
    entry_bar = _bar(entry_ms, 100, 101, 99, 100)
    exit_bar = _bar(cleanup_ms, 100, 101, 99, 100)
    lifecycle = (entry_bar, exit_bar)
    fills = (_fill(phase="ENTRY", symbol="ALT", timestamp=entry_ms,
                   side=1, quantity=quantity),
             _fill(phase="PROTECTION_FAILURE_CLEANUP", symbol="ALT",
                   timestamp=cleanup_ms, side=-1, quantity=quantity))
    funding_mark = _mark(entry_ms, "ALT", 100)
    funding_events = (c259.AppliedFunding(funding_authority[0], funding_mark),)
    lifecycle_marks = (_mark(entry_ms, "ALT", 100),)
    assert SOURCE_ARCHIVE is not None
    source_seal = _synthetic_source_seal(
        attempt_id="x", symbol="ALT", entry_ms=entry_ms, close_ms=cleanup_ms,
        split_start_ms=DEV_START,
        split_end_ms=c259.ALL_SPLIT_RANGES["development"][1],
        atr_bars=atr_bars, funding_authority=funding_authority,
        lifecycle_bars=lifecycle, fills=fills,
        funding_marks=(funding_mark,), lifecycle_marks=lifecycle_marks)
    failed = c259.protection_failure(attempt_id="x", side=1, symbol="ALT",
                                    entry_ms=entry_ms, cleanup_ms=cleanup_ms, fills=fills,
                                    funding_events=funding_events,
                                    sizing=sized, decision_close_ms=decision,
                                    split_start_ms=DEV_START,
                                    authority_source_digest=SOURCE_DIGEST,
                                    source_seal=source_seal,
                                    atr_authority_digest=atr_digest, atr_bars=atr_bars,
                                    funding_authority=funding_authority,
                                    funding_authority_digest=funding_digest,
                                    lifecycle_bars=lifecycle,
                                    entry_bar=entry_bar, exit_bar=exit_bar,
                                    stop=96, target=108,
                                    regime="BTC_ABOVE_OR_ON_EMA200")
    assert failed.incomplete and failed.net_cash < -.5
    with pytest.raises(c259.IncompleteEpisode, match="exactly flat"):
        c259.protection_failure(attempt_id="x", side=1, symbol="ALT",
                                entry_ms=entry_ms, cleanup_ms=cleanup_ms, fills=[
                                    _fill(phase="ENTRY", symbol="ALT", timestamp=entry_ms,
                                          side=1, quantity=quantity),
                                    _fill(phase="PROTECTION_FAILURE_CLEANUP", symbol="ALT",
                                          timestamp=cleanup_ms, side=-1,
                                          quantity=quantity/2)],
                                funding_events=[], sizing=sized, decision_close_ms=decision,
                                split_start_ms=DEV_START,
                                authority_source_digest=SOURCE_DIGEST,
                                source_seal=source_seal,
                                atr_authority_digest=atr_digest, atr_bars=atr_bars,
                                funding_authority=(), funding_authority_digest=c259.funding_tape_digest(
                                    source_digest=SOURCE_DIGEST, symbol="ALT",
                                    split_start_ms=DEV_START,
                                    split_end_ms=c259.ALL_SPLIT_RANGES["development"][1],
                                    events=()), lifecycle_bars=lifecycle,
                                entry_bar=entry_bar,
                                exit_bar=exit_bar, stop=96, target=108,
                                regime="BTC_ABOVE_OR_ON_EMA200")
    with pytest.raises(c259.IncompleteEpisode):
        c259.protection_failure(attempt_id="x", side=1, symbol="ALT", entry_ms=1,
                                cleanup_ms=1, fills=[], funding_events=[], sizing=sized,
                                decision_close_ms=decision, split_start_ms=DEV_START,
                                authority_source_digest=SOURCE_DIGEST,
                                source_seal=source_seal,
                                atr_authority_digest=atr_digest, atr_bars=atr_bars,
                                funding_authority=(), funding_authority_digest=c259.funding_tape_digest(
                                    source_digest=SOURCE_DIGEST, symbol="ALT",
                                    split_start_ms=DEV_START,
                                    split_end_ms=c259.ALL_SPLIT_RANGES["development"][1],
                                    events=()),
                                lifecycle_bars=lifecycle,
                                entry_bar=entry_bar, exit_bar=exit_bar, stop=96, target=108,
                                regime="BTC_ABOVE_OR_ON_EMA200")


def test_chronological_marked_drawdown_and_realized_order_are_not_entry_order() -> None:
    # Marked equity falls $100 from a $2,050 peak; close-time realized fall is 2R.
    marked_dd = c259.marked_equity_drawdown_R([(1, 0, 2050), (2, 0, 1950),
                                                (3, 0, 2000)], 2000)
    trades = [_trade("late-entry", 1, "A", VALID_ENTRY+9*c259.HOUR_MS,
                     VALID_ENTRY+21*c259.HOUR_MS, -2),
              _trade("early-entry", 1, "B", VALID_ENTRY,
                     VALID_ENTRY+12*c259.HOUR_MS, 1)]
    realized_dd = c259.closed_realized_drawdown_R(trades)
    assert marked_dd == pytest.approx(5) and realized_dd > 0
    assert c259.gate_drawdown_R(marked_drawdown_R=marked_dd,
                               realized_drawdown_R=realized_dd) == marked_dd
    with pytest.raises(c259.IncompleteAuthority, match="phase ordered"):
        c259.marked_equity_drawdown_R([(2, 0, 2000), (1, 0, 1990)], 2000)


def test_derived_drawdown_requires_every_open_hour_mark() -> None:
    trade = _trade("marks", 1, "ALT", VALID_ENTRY, VALID_ENTRY+12*c259.HOUR_MS, 1)
    complete = tuple(_mark(t, "ALT", 100) for t in range(
        trade.entry_ms, trade.close_ms, c259.HOUR_MS))
    points = c259.derive_equity_points((trade,), complete, start_ms=VALID_ENTRY-c259.HOUR_MS,
                                      end_ms=VALID_ENTRY+13*c259.HOUR_MS)
    assert points[-1][0] == trade.close_ms
    with pytest.raises(c259.IncompleteAuthority, match="missing, extra"):
        c259.derive_equity_points((trade,), complete[::2],
                                  start_ms=VALID_ENTRY-c259.HOUR_MS,
                                  end_ms=VALID_ENTRY+13*c259.HOUR_MS)


def test_split_boundaries_and_regime_state_reset() -> None:
    assert c259.split_name(1743465600000) == "wf1"
    assert c259.split_name(1790553600000) == "sealed_holdout"
    state = c259.EMA200()
    for i in range(200):
        result = state.update(_bar(i*c259.HOUR_MS, close=100 + i/1000))
    assert result == "BTC_ABOVE_OR_ON_EMA200"
    assert state.update(_bar(205*c259.HOUR_MS, close=100)) is None


def test_metrics_stress_identity_forward_gates_and_drawdown_tiers() -> None:
    base = [_trade("a", 1, "A", VALID_ENTRY, VALID_ENTRY+12*c259.HOUR_MS, 5),
            _trade("b", 1, "B", VALID_ENTRY+2*c259.HOUR_MS,
                   VALID_ENTRY+14*c259.HOUR_MS, -1)]
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
    early = _trade("early", 1, "EARLY", VALID_ENTRY,
                   VALID_ENTRY+c259.HOUR_MS, 1)
    with pytest.raises(c259.IncompleteEpisode, match="mandatory"):
        c259.metrics([early])
    gap = _trade("gap", 1, "GAP", VALID_ENTRY, VALID_ENTRY+12*c259.HOUR_MS, -1)
    forged_gap_fill = _fill(movement=-float(gap.sizing.quantity)*5, side=-1,
                            timestamp=gap.close_ms, phase="STOP_GAP", symbol="GAP",
                            reference=95.0, quantity=float(gap.sizing.quantity))
    forged_gap = replace(gap, fills=(gap.fills[0], forged_gap_fill),
                         exit_bar=_bar(gap.close_ms, 96, 101, 94, 96))
    with pytest.raises(c259.C259Error, match="protected source|lifecycle authority|canonical bar/protection"):
        c259.metrics([forged_gap])
    report = c259.metrics(base)
    assert report["closed"] == 2 and report["profit_factor"] > 1.5
    with pytest.raises(c259.C259Error, match="wf1/wf2/wf3"):
        c259.forward_gate([])
    folds = []
    for name, (start, _end) in c259.FOLD_RANGES.items():
        if name == "development":
            continue
        trade = _trade(name, 1, f"{name}USDT", start+26*c259.HOUR_MS,
                       start+38*c259.HOUR_MS, 1)
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
    funded_authority = (conflicting.event,)
    split_start, split_end = c259.ALL_SPLIT_RANGES[folds[0].name]
    funded_base = replace(
        funded, funding_events=(conflicting,), funding_authority=funded_authority,
        funding_authority_digest=c259.funding_tape_digest(
            source_digest=SOURCE_DIGEST, symbol=funded.symbol,
            split_start_ms=split_start, split_end_ms=split_end,
            events=funded_authority))
    funded_stress = _stress_trade(funded_base)
    conflicting_fold = replace(folds[0], base=(funded_base,), stress=(funded_stress,))
    with pytest.raises(c259.IncompleteAuthority, match="conflicts|funding tape"):
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
