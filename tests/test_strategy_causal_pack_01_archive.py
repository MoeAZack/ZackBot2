import hashlib
import json
import zipfile
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


def test_missing_funding_archive_coverage_refuses_whole_symbol(tmp_path):
    manifest, _ = _fixture(tmp_path)
    manifest = _manifest([row for row in manifest["files"] if row["series"] != "fundingRate"])
    universe = _universe(manifest)
    adapter = A.RawArchiveAdapter(manifest, universe, tmp_path)
    with pytest.raises(A.ArchiveIncomplete, match="no manifest coverage"):
        adapter.load_funding("S00", T0, T0 + 3 * A.HOUR_MS)


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
