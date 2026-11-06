import hashlib
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tools.research import manifest as M
from tools.research import universe as U
from tools.research import strategy_causal_pack_01_archive as A
import feasibility as F


T0 = 1_736_121_600_000  # 2025-01-06T00:00:00Z, Monday
CORE = tuple(f"S{i:02d}" for i in range(20))


def _kline(t, close=100):
    return f"{t},100,101,99,{close},10,{t + A.HOUR_MS - 1},1000,1,5,500,0"


def _write_archive(store: Path, rel: str, rows):
    path = store.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_name = path.name[:-4] + ".csv"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(csv_name, ("\n".join(rows) + "\n").encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".ok").write_text(digest, encoding="ascii")
    return M.scan_archive(str(path), rel)


def _manifest(records):
    value = {
        "format": M.FORMAT,
        "manifest_id": "synthetic-stratpack",
        "source_class": "archive-verified",
        "survivor_only": False,
        "loader_version": M.ARCHIVE_LOADER,
        "available_rule": M.ARCHIVE_RULE,
        "accessed_cairo": "2026-11-06 synthetic",
        "note": "synthetic only",
        "data_root": M.ARCHIVE_ROOT,
        "files": sorted(records, key=lambda row: row["path"]),
    }
    value["digest"] = M.digest_of(value)
    M.validate(value)
    return value


def _universe(manifest, monday=T0):
    value = {
        "format": U.FORMAT,
        "universe_id": U.UNIVERSE_ID,
        "manifest_digest": manifest["digest"],
        "books": ["crypto"],
        "weeks": [{
            "monday_ms": monday,
            "books": {"crypto": {"members": list(CORE), "eligible": 20, "qv30d_usdt": ["1"] * 20}},
        }],
        "symbols": [{"symbol": symbol, "class": "crypto", "subclass": "synthetic"} for symbol in CORE],
    }
    value["digest"] = U.digest_of(value)
    return value


def _fixture(store: Path, *, kline_times=(T0, T0 + A.HOUR_MS, T0 + 2 * A.HOUR_MS)):
    records = [
        _write_archive(
            store, "um/monthly/klines/S00/1h/S00-1h-2025-01.zip",
            [_kline(t) for t in kline_times]),
        _write_archive(
            store, "um/monthly/markPriceKlines/S00/1h/S00-1h-2025-01.zip",
            [_kline(t, 100.5) for t in (T0, T0 + A.HOUR_MS, T0 + 2 * A.HOUR_MS)]),
        _write_archive(
            store, "um/monthly/fundingRate/S00/S00-fundingRate-2025-01.zip",
            [f"{T0 + 2 * A.HOUR_MS},8,0.0001"]),
    ]
    manifest = _manifest(records)
    return manifest, _universe(manifest)


def test_synthetic_archive_adapter_reuses_verified_manifest_pit_and_parsers(tmp_path):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    bars = adapter.load_klines("S00", "1h", T0, T0 + 3 * A.HOUR_MS)
    marks = adapter.load_mark_closes("S00", T0 + A.HOUR_MS, T0 + 3 * A.HOUR_MS)
    funding = adapter.load_funding("S00", T0, T0 + 3 * A.HOUR_MS)
    assert [bar.open_ms for bar in bars] == [T0, T0 + A.HOUR_MS, T0 + 2 * A.HOUR_MS]
    assert marks == ((T0 + A.HOUR_MS, 100.5), (T0 + 2 * A.HOUR_MS, 100.5),
                     (T0 + 3 * A.HOUR_MS, 100.5))
    assert funding == (A.RawFunding(T0 + 2 * A.HOUR_MS, 8, 0.0001),)
    assert adapter.core20_at(T0) == CORE


def test_archive_checksum_tamper_fails_closed(tmp_path):
    manifest, universe = _fixture(tmp_path)
    path = tmp_path / "um/monthly/klines/S00/1h/S00-1h-2025-01.zip"
    path.write_bytes(path.read_bytes() + b"tamper")
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    with pytest.raises(A.ArchiveIncomplete, match="archive verification failed"):
        adapter.load_klines("S00", "1h", T0, T0 + 3 * A.HOUR_MS)


def test_archive_internal_kline_gap_refuses_whole_symbol(tmp_path):
    manifest, universe = _fixture(tmp_path, kline_times=(T0, T0 + 2 * A.HOUR_MS))
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    with pytest.raises(A.ArchiveIncomplete, match="incomplete or misaligned"):
        adapter.load_klines("S00", "1h", T0, T0 + 3 * A.HOUR_MS)


def test_sparse_kline_load_returns_verified_rows_and_explicit_internal_gap(tmp_path):
    manifest, universe = _fixture(tmp_path, kline_times=(T0, T0 + 2 * A.HOUR_MS))
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    result = adapter.load_klines_sparse("S00", "1h", T0, T0 + 3 * A.HOUR_MS)
    assert [row.open_ms for row in result.rows] == [T0, T0 + 2 * A.HOUR_MS]
    assert result.gaps == (A.CoverageGap(
        T0 + A.HOUR_MS, T0 + 2 * A.HOUR_MS, "missing_row"),)


def test_sparse_kline_load_reports_absent_manifest_without_opening_rows(tmp_path):
    manifest, _ = _fixture(tmp_path)
    manifest = _manifest([row for row in manifest["files"] if row["series"] != "klines"])
    universe = _universe(manifest)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    result = adapter.load_klines_sparse("S00", "1h", T0, T0 + 3 * A.HOUR_MS)
    assert result.rows == ()
    assert result.gaps == (
        A.CoverageGap(T0, T0 + 3 * A.HOUR_MS, "missing_manifest"),
        A.CoverageGap(T0, T0 + 3 * A.HOUR_MS, "missing_row"),
    )


def test_sparse_kline_load_keeps_checksum_and_grid_guards(tmp_path):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    path = tmp_path / "um/monthly/klines/S00/1h/S00-1h-2025-01.zip"
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(A.ArchiveIncomplete, match="archive verification failed"):
        adapter.load_klines_sparse("S00", "1h", T0, T0 + 3 * A.HOUR_MS)
    with pytest.raises(A.ArchiveIncomplete, match="grid-aligned"):
        adapter.load_klines_sparse("S00", "1h", T0 + 1, T0 + 3 * A.HOUR_MS)


def test_sparse_kline_non_finite_row_becomes_explicit_gap_without_bridging(
        tmp_path, monkeypatch):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    rows = [
        [str(T0).encode(), b"100", b"101", b"99", b"100", b"10"],
        [str(T0 + A.HOUR_MS).encode(), b"100", b"101", b"99", b"nan", b"10"],
        [str(T0 + 2 * A.HOUR_MS).encode(), b"100", b"101", b"99", b"100", b"10"],
    ]
    monkeypatch.setattr(adapter, "_rows_sparse", lambda *_: (rows, ()))
    result = adapter.load_klines_sparse("S00", "1h", T0, T0 + 3 * A.HOUR_MS)
    assert [row.open_ms for row in result.rows] == [T0, T0 + 2 * A.HOUR_MS]
    assert result.gaps == (A.CoverageGap(
        T0 + A.HOUR_MS, T0 + 2 * A.HOUR_MS, "non_finite_row"),)


def test_sparse_fixture_cannot_bypass_authoritative_duplicate_refusal(tmp_path):
    with pytest.raises(M.ManifestError, match="unordered or duplicate row"):
        _fixture(tmp_path, kline_times=(T0, T0, T0 + A.HOUR_MS))


def test_missing_funding_archive_coverage_refuses_whole_symbol(tmp_path):
    manifest, _ = _fixture(tmp_path)
    manifest = _manifest([row for row in manifest["files"] if row["series"] != "fundingRate"])
    universe = _universe(manifest)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    with pytest.raises(A.ArchiveIncomplete, match="no manifest coverage"):
        adapter.load_funding("S00", T0, T0 + 3 * A.HOUR_MS)


def test_sparse_funding_load_reports_manifest_and_settlement_gaps_separately(tmp_path):
    manifest, _ = _fixture(tmp_path)
    manifest = _manifest([row for row in manifest["files"] if row["series"] != "fundingRate"])
    universe = _universe(manifest)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    result = adapter.load_funding_sparse("S00", T0, T0 + 3 * A.HOUR_MS)
    assert result.rows == ()
    assert result.gaps == (
        A.CoverageGap(T0, T0 + 3 * A.HOUR_MS, "missing_manifest"),
        A.CoverageGap(T0, T0 + 3 * A.HOUR_MS, "no_settlement"),
    )


def test_sparse_funding_load_reports_tail_as_interval_not_boolean(tmp_path):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    end_ms = T0 + 12 * A.HOUR_MS
    result = adapter.load_funding_sparse("S00", T0, end_ms)
    assert result.rows == (A.RawFunding(T0 + 2 * A.HOUR_MS, 8, 0.0001),)
    assert result.gaps == (A.CoverageGap(
        T0 + 10 * A.HOUR_MS, end_ms, "settlement_gap"),)


def test_sparse_funding_collapses_sub_tolerance_settlement_jitter(tmp_path, monkeypatch):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    jitter = A.FUNDING_TIME_TOLERANCE_MS - 1
    rows = [
        [str(T0).encode(), b"8", b"0.0001"],
        [str(T0 + 8 * A.HOUR_MS + jitter).encode(), b"8", b"0.0001"],
    ]
    monkeypatch.setattr(adapter, "_rows_sparse", lambda *_: (rows, ()))
    result = adapter.load_funding_sparse(
        "S00", T0, T0 + 16 * A.HOUR_MS + jitter)
    assert [row.time_ms for row in result.rows] == [
        T0, T0 + 8 * A.HOUR_MS + jitter]
    assert result.gaps == ()


def test_sparse_funding_tolerance_boundary_covers_head_middle_and_tail(
        tmp_path, monkeypatch):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    tolerance = A.FUNDING_TIME_TOLERANCE_MS
    first = T0 + 8 * A.HOUR_MS + tolerance
    second = first + 8 * A.HOUR_MS + tolerance
    rows = [
        [str(first).encode(), b"8", b"0.0001"],
        [str(second).encode(), b"8", b"0.0001"],
    ]
    monkeypatch.setattr(adapter, "_rows_sparse", lambda *_: (rows, ()))
    result = adapter.load_funding_sparse(
        "S00", T0, second + 8 * A.HOUR_MS + tolerance)
    assert result.gaps == ()


def test_sparse_funding_one_ms_beyond_tolerance_is_a_gap(tmp_path, monkeypatch):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    lateness = A.FUNDING_TIME_TOLERANCE_MS + 1
    rows = [
        [str(T0).encode(), b"8", b"0.0001"],
        [str(T0 + 8 * A.HOUR_MS + lateness).encode(), b"8", b"0.0001"],
    ]
    monkeypatch.setattr(adapter, "_rows_sparse", lambda *_: (rows, ()))
    result = adapter.load_funding_sparse(
        "S00", T0, T0 + 16 * A.HOUR_MS + lateness)
    assert result.gaps == (A.CoverageGap(
        T0 + 8 * A.HOUR_MS,
        T0 + 8 * A.HOUR_MS + lateness,
        "settlement_gap"),)


def test_sparse_funding_tolerance_does_not_bridge_missing_settlement(
        tmp_path, monkeypatch):
    manifest, universe = _fixture(tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    rows = [
        [str(T0 + 8 * A.HOUR_MS).encode(), b"8", b"0.0001"],
        [str(T0 + 24 * A.HOUR_MS).encode(), b"8", b"0.0001"],
    ]
    monkeypatch.setattr(adapter, "_rows_sparse", lambda *_: (rows, ()))
    result = adapter.load_funding_sparse("S00", T0, T0 + 32 * A.HOUR_MS)
    assert result.gaps == (A.CoverageGap(
        T0 + 16 * A.HOUR_MS, T0 + 24 * A.HOUR_MS, "settlement_gap"),)


def test_sparse_mark_load_reports_gap_in_causal_availability_time(tmp_path):
    manifest, universe = _fixture(tmp_path)
    # Rebuild only the mark archive with the middle raw hour absent.
    records = [row for row in manifest["files"] if row["series"] != "markPriceKlines"]
    records.append(_write_archive(
        tmp_path, "um/monthly/markPriceKlines/S00/1h/S00-1h-2025-01.zip",
        [_kline(T0, 100.5), _kline(T0 + 2 * A.HOUR_MS, 100.5)]))
    manifest = _manifest(records)
    universe = _universe(manifest)
    result = A.RawArchiveAdapter(manifest, universe, tmp_path).load_mark_closes_sparse(
        "S00", T0 + A.HOUR_MS, T0 + 3 * A.HOUR_MS)
    assert result.rows == ((T0 + A.HOUR_MS, 100.5), (T0 + 3 * A.HOUR_MS, 100.5))
    assert result.gaps == (A.CoverageGap(
        T0 + 2 * A.HOUR_MS, T0 + 3 * A.HOUR_MS, "missing_row"),)


def test_cutoff_crossing_archive_is_refused_before_its_bytes_are_read(tmp_path):
    before = A.CUTOFF_MS - A.HOUR_MS
    rel = "um/monthly/klines/S00/1h/S00-1h-2026-09.zip"
    record = _write_archive(tmp_path, rel, [_kline(before), _kline(A.CUTOFF_MS)])
    manifest = _manifest([record])
    universe = _universe(manifest, monday=before)
    path = tmp_path.joinpath(*rel.split("/"))
    path.unlink()  # cutoff metadata guard must win before any attempted archive read
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    with pytest.raises(A.ArchiveIncomplete, match="crosses frozen cutoff; not read"):
        adapter.load_klines("S00", "1h", before, A.CUTOFF_MS)


def test_universe_manifest_binding_and_non_core_symbol_fail_closed(tmp_path):
    manifest, universe = _fixture(tmp_path)
    wrong = dict(universe, manifest_digest="0" * 64)
    wrong["digest"] = U.digest_of(wrong)
    with pytest.raises(A.ArchiveIncomplete, match="digest/manifest binding"):
        A.RawArchiveAdapter(manifest, wrong, tmp_path)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    with pytest.raises(A.ArchiveIncomplete, match="outside frozen PIT CORE-20"):
        adapter.load_klines("NOTCORE20", "1h", T0, T0 + A.HOUR_MS)


def test_exchange_filters_use_checksum_bound_shared_engine_parser(tmp_path):
    path = tmp_path / "exchangeInfo.json"
    info = F.exchange_info_from_rules({
        "S00": {"tick": 0.01, "step": 0.1, "min_qty": 0.1, "min_notional": 5, "max_qty": 100},
    })
    raw = json.dumps(info, separators=(",", ":")).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    rules, actual = A.load_exchange_filters(path, digest, ("S00",))
    assert actual == digest
    assert rules["S00"] == {"tick": 0.01, "step": 0.1, "min_qty": 0.1,
                            "min_notional": 5.0, "max_qty": 100.0}
    with pytest.raises(A.ArchiveIncomplete, match="checksum mismatch"):
        A.load_exchange_filters(path, "0" * 64, ("S00",))
    with pytest.raises(A.ArchiveIncomplete, match="lacks actual filters"):
        A.load_exchange_filters(path, digest, ("S01",))


def _filter_authority_fixture(tmp_path, *, ineligible=(), capture="2024-12-01T00:00:00Z",
                              server="2024-12-01T00:00:00Z", fold_start="2025-01-06T00:00:00Z"):
    symbols = [f"S{i:02d}" for i in range(21)]
    rules = {symbol: {"tick": 0.01, "step": 0.1, "min_qty": 0.1,
                      "min_notional": 5, "max_qty": 100} for symbol in symbols}
    info = F.exchange_info_from_rules(rules)
    info["serverTime"] = int(datetime.fromisoformat(server.replace("Z", "+00:00")).timestamp() * 1000)
    for row in info["symbols"]:
        row["quoteAsset"] = "USDT"
        row["underlyingType"] = "COIN"
        if row["symbol"] in ineligible:
            row["status"] = "SETTLING"
    snapshot_rel = "evidence/exchange.json"
    snapshot_path = tmp_path / snapshot_rel
    snapshot_path.parent.mkdir(parents=True)
    snapshot_raw = json.dumps(info, separators=(",", ":")).encode()
    snapshot_path.write_bytes(snapshot_raw)

    fold_dt = datetime.fromisoformat(fold_start.replace("Z", "+00:00"))
    active_monday = fold_dt - timedelta(days=fold_dt.weekday())
    monday_utc = active_monday.strftime("%Y-%m-%dT%H:%M:%SZ")
    monday_ms = int(active_monday.timestamp() * 1000)
    pit = {
        "format": U.FORMAT, "universe_id": U.UNIVERSE_ID, "manifest_digest": "m" * 64,
        "books": ["crypto"],
        "weeks": [{"monday_ms": monday_ms, "monday_utc": monday_utc,
                   "books": {"crypto": {"members": symbols, "eligible": 21,
                                           "qv30d_usdt": ["1"] * 21}}}],
        "symbols": [{"symbol": symbol, "class": "crypto", "subclass": "synthetic"}
                    for symbol in symbols],
    }
    pit["digest"] = U.digest_of(pit)
    pit_rel = "evidence/pit.json"
    pit_path = tmp_path / pit_rel
    pit_raw = json.dumps(pit, separators=(",", ":")).encode()
    pit_path.write_bytes(pit_raw)
    members = [symbol for symbol in symbols if symbol not in ineligible][:20]
    cells = [{"monday_utc": monday_utc, "members": members}]
    membership_sha = hashlib.sha256(json.dumps(
        cells, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    authority = {
        "authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY",
        "version": 2, "status": "FROZEN", "signed_by": "codex",
        "pit_universe_path": pit_rel,
        "pit_universe_sha256": hashlib.sha256(pit_raw).hexdigest(),
        "pit_universe_digest": pit["digest"],
        "snapshots": [{"captured_at": capture, "server_time": server,
                       "path": snapshot_rel,
                       "sha256": hashlib.sha256(snapshot_raw).hexdigest()}],
        "fold_bindings": {"wf": capture},
        "weekly_membership_sha256": {"wf": membership_sha},
    }
    authority_path = tmp_path / "authority.json"
    authority_raw = json.dumps(authority, separators=(",", ":")).encode()
    authority_path.write_bytes(authority_raw)
    authority_sha = hashlib.sha256(authority_raw).hexdigest()
    contract = {
        "data": {"exchange_filter_authority_sha256": authority_sha,
                 "pit_universe_path": pit_rel,
                 "pit_universe_sha256": authority["pit_universe_sha256"],
                 "pit_universe_digest": pit["digest"]},
        "folds": [{"name": "wf", "start": fold_start, "end": "2025-01-13T00:00:00Z"}],
    }
    return authority_path, authority_sha, contract, members


def test_filter_authority_replaces_ineligible_ranked_symbol_before_market_reads(tmp_path):
    path, digest, contract, members = _filter_authority_fixture(
        tmp_path, ineligible=("S00",))
    result = A.load_fold_filter_authorities(path, digest, tmp_path, contract)
    assert tuple(symbol for symbol in (f"S{i:02d}" for i in range(21))
                 if symbol in result["wf"].eligible_symbols) == tuple(members)
    assert "S00" not in result["wf"].eligible_symbols
    assert "S20" in result["wf"].eligible_symbols


def test_filter_authority_pins_membership_cell_active_before_midweek_fold_start(tmp_path):
    path, digest, contract, _ = _filter_authority_fixture(
        tmp_path, fold_start="2025-01-07T00:00:00Z")
    result = A.load_fold_filter_authorities(path, digest, tmp_path, contract)
    assert len(result["wf"].eligible_symbols) == 21


def test_filter_authority_refuses_future_server_time_even_with_old_capture(tmp_path):
    path, digest, contract, _ = _filter_authority_fixture(
        tmp_path, capture="2024-12-01T00:00:00Z", server="2025-01-07T00:00:00Z")
    with pytest.raises(A.ArchiveIncomplete, match="no causal pre-fold filter snapshot"):
        A.load_fold_filter_authorities(path, digest, tmp_path, contract)


def test_filter_authority_refuses_fewer_than_twenty_eligible_symbols(tmp_path):
    path, digest, contract, _ = _filter_authority_fixture(
        tmp_path, ineligible=("S00", "S01"))
    with pytest.raises(A.ArchiveIncomplete, match="CORE-20 is incomplete"):
        A.load_fold_filter_authorities(path, digest, tmp_path, contract)


def test_filter_authority_refuses_declared_server_time_mismatch(tmp_path):
    path, digest, contract, _ = _filter_authority_fixture(tmp_path)
    authority = json.loads(path.read_text())
    authority["snapshots"][0]["server_time"] = "2024-11-30T23:59:59Z"
    raw = json.dumps(authority, separators=(",", ":")).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    contract["data"]["exchange_filter_authority_sha256"] = digest
    with pytest.raises(A.ArchiveIncomplete, match="serverTime mismatch"):
        A.load_fold_filter_authorities(path, digest, tmp_path, contract)


def test_filter_authority_refuses_snapshot_and_membership_tamper(tmp_path):
    path, digest, contract, _ = _filter_authority_fixture(tmp_path)
    snapshot = tmp_path / "evidence/exchange.json"
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    with pytest.raises(A.ArchiveIncomplete, match="snapshot checksum mismatch"):
        A.load_fold_filter_authorities(path, digest, tmp_path, contract)

    path, digest, contract, _ = _filter_authority_fixture(tmp_path / "membership")
    authority = json.loads(path.read_text())
    authority["weekly_membership_sha256"]["wf"] = "0" * 64
    raw = json.dumps(authority, separators=(",", ":")).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    contract["data"]["exchange_filter_authority_sha256"] = digest
    with pytest.raises(A.ArchiveIncomplete, match="weekly membership digest mismatch"):
        A.load_fold_filter_authorities(path, digest, tmp_path / "membership", contract)
