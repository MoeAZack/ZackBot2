"""Hostile metadata/fixture checks for deterministic authority-v6 tooling.

No network call, repository market archive, candidate identity, signal, return, or
outcome is read by this module.
"""
from __future__ import annotations

import copy
import gzip
import json
import shutil
import io
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.research import authority_v6 as V6


ROOT = Path(__file__).resolve().parents[1]
PIT8 = ROOT / "research_evidence/universe/pit-top40-qv30d-precutoff-monthly-rest-v8.json"
CLASSES = ROOT / "research_evidence/universe/instrument-classes-v1.json"
FILTER3 = ROOT / "contracts/strategy_causal_pack_01/exchange_filter_authority.v3.json"
MANIFEST5 = ROOT / "research_evidence/manifests/binance-um-archive-precutoff-monthly-rest-v5.json.gz"


def _pit_v9(tail_crypto=None):
    pit = json.loads(PIT8.read_text(encoding="utf-8"))
    template = copy.deepcopy(pit["weeks"][-1])
    for monday in V6.NEW_CELLS:
        week = copy.deepcopy(template)
        week["monday_ms"] = monday
        week["monday_utc"] = V6.datetime.fromtimestamp(
            monday / 1000, tz=V6.timezone.utc).strftime("%Y-%m-%d")
        if tail_crypto is not None:
            week["books"] = {
                name: {"eligible": len(tail_crypto) if name == "crypto" else 0,
                       "members": list(tail_crypto) if name == "crypto" else [],
                       "qv30d_usdt": [str(1000 - i) for i in range(len(tail_crypto))]
                       if name == "crypto" else []}
                for name in pit["books"]
            }
            week["vetoes"] = []
            week["gaps"] = []
            week["not_addressable"] = []
        pit["weeks"].append(week)
    pit["universe_id"] = "PIT-TOP40-QV30D-V9"
    pit["manifest_digest"] = "1" * 64
    pit["store_end_ms"] = V6.CUTOFF_MS
    pit["digest"] = V6.U.digest_of(pit)
    return pit


def _filter_inputs():
    base_raw = FILTER3.read_bytes()
    base = json.loads(base_raw)
    payloads, sets = {}, []
    for row in base["snapshots"]:
        raw = (ROOT / row["path"]).read_bytes()
        payloads[row["captured_at"]] = raw
        sets.append(V6._snapshot_eligible(json.loads(raw)))
    eligible = sorted(set.intersection(*map(set, sets)))
    assert len(eligible) >= 20
    ineligible = sorted(set(V6.frozen_seed_symbols()) - set.union(*map(set, sets)))
    assert ineligible
    return base_raw, payloads, eligible, ineligible


def _filter_v4(pit):
    base_raw, payloads, _, _ = _filter_inputs()
    return V6.build_filter_v4(base_raw, pit, payloads,
                              pit_byte_sha256=V6.sha256(V6.canonical(pit)))


def _funding_row(symbol, time_ms, rate="0.0001"):
    return {"symbol": symbol, "fundingTime": time_ms, "fundingRate": rate}


def _vision_record(symbol="BTCUSDT", time_ms=None):
    time_ms = V6._iso_ms("2026-11-04T00:00:00Z") if time_ms is None else time_ms
    path = f"um/daily/klines/{symbol}/1h/{symbol}-1h-2026-11-04.zip"
    raw = b"synthetic-authority-bytes"
    digest = V6.sha256(raw)
    return {
        "path": path, "series": "klines", "interval": "1h", "symbol": symbol,
        "declared_start_ms": V6._iso_ms("2026-11-04T00:00:00Z"),
        "declared_end_ms": V6.CUTOFF_MS,
        "observed_first_ms": time_ms, "observed_last_ms": time_ms,
        "rows": 1, "raw_bytes": len(raw), "raw_sha256": digest,
        "parsed_content_digest": "2" * 64,
        "row_digests": {str(time_ms): "3" * 64}, "times": [time_ms],
        "source": {"kind": "binance-vision-archive", "url": V6.M.archive_url(path),
                   "archive_checksum": digest, "checksum_path": path + ".CHECKSUM",
                   "checksum_raw_sha256": "4" * 64},
    }


def _ledger_raw(records):
    lines = []
    for record in records:
        source = record["source"]
        if source["kind"] == "binance-vision-archive":
            acquired = {"kind": "VISION", "decision": "PRESENT_CHECKSUM_VERIFIED",
                        "path": record["path"], "attempt": 0,
                        "url": source["url"], "checksum_url": source["url"] + ".CHECKSUM",
                        "raw_sha256": record["raw_sha256"], "raw_bytes": record["raw_bytes"],
                        "checksum_raw_sha256": source["checksum_raw_sha256"]}
            lines.append(V6.canonical(acquired))
            row = {"kind": "VISION", "decision": "VISION_PARSER_SCHEMA_CUTOFF_ACCEPTED",
                   "path": record["path"], "attempt": 0,
                   "raw_sha256": record["raw_sha256"],
                   "checksum_raw_sha256": source["checksum_raw_sha256"],
                   "parsed_content_digest": record["parsed_content_digest"],
                   "acquisition_record_sha256": V6.sha256(V6.canonical(acquired)),
                   "acquisition_attempt": 0, "source_url": source["url"],
                   "checksum_url": source["url"] + ".CHECKSUM",
                   "raw_bytes": record["raw_bytes"],
                   "rows": record["rows"],
                   "observed_first_ms": record["observed_first_ms"],
                   "observed_last_ms": record["observed_last_ms"],
                   "declared_start_ms": record["declared_start_ms"],
                   "declared_end_ms": record["declared_end_ms"]}
        elif source["kind"] == V6.M.FUNDING_API_KIND:
            row = {"decision": "PRESERVED", "path": record["path"],
                   "raw_sha256": record["raw_sha256"]}
        else:
            row = {"decision": "PRESENT_CHECKSUM_VERIFIED", "path": record["path"]}
        lines.append(V6.canonical(row))
    return b"\n".join(lines) + b"\n"


def _api_record(page):
    symbol, time_ms = "BTCUSDT", V6.API_START_MS
    path = (f"um/api/fundingRate/{symbol}/"
            f"{symbol}-fundingRate-2026-11-01-2026-11-04-p{page:03d}.json")
    raw = f"api-{page}".encode(); digest = V6.sha256(raw)
    return {
        "path": path, "series": "fundingRate", "interval": None, "symbol": symbol,
        "declared_start_ms": V6.API_START_MS, "declared_end_ms": V6.CUTOFF_MS,
        "observed_first_ms": time_ms, "observed_last_ms": time_ms, "rows": 1,
        "raw_bytes": len(raw), "raw_sha256": digest, "parsed_content_digest": "5" * 64,
        "row_digests": {str(time_ms): "6" * 64}, "times": [time_ms],
        "source": {"kind": V6.M.FUNDING_API_KIND,
                   "url": V6.funding_url(symbol, V6.API_START_MS),
                   "archive_checksum": digest, "provenance_path": path + ".provenance.json",
                   "provenance_sha256": "7" * 64},
    }


def _sep_base_record():
    manifest = json.loads(gzip.decompress(MANIFEST5.read_bytes()))
    return next(row for row in manifest["files"]
                if row["series"] == "klines" and row["interval"] == "1h"
                and row["symbol"] == "BTCUSDT"
                and row["first_open_ms"] == V6._iso_ms("2026-09-01T00:00:00Z"))


def _base_proof(old):
    times = list(range(old["first_open_ms"], old["last_open_ms"] + V6.HOUR_MS,
                       V6.HOUR_MS))
    return {"source_sha256": old["sha256"],
            "row_digests": {str(time_ms): V6.sha256(V6.canonical(["base", time_ms]))
                            for time_ms in times}}


def _sep_monthly_record(times, digests):
    path = "um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09.zip"
    raw = b"synthetic-september-monthly-archive"
    raw_digest = V6.sha256(raw)
    return {
        "path": path, "series": "klines", "interval": "1h", "symbol": "BTCUSDT",
        "declared_start_ms": V6._iso_ms("2026-09-01T00:00:00Z"),
        "declared_end_ms": V6._iso_ms("2026-10-01T00:00:00Z"),
        "observed_first_ms": times[0] if times else None,
        "observed_last_ms": times[-1] if times else None,
        "rows": len(times), "raw_bytes": len(raw), "raw_sha256": raw_digest,
        "parsed_content_digest": V6.sha256(V6.canonical(digests)),
        "row_digests": {str(time_ms): digests[str(time_ms)] for time_ms in times},
        "times": list(times),
        "source": {"kind": "binance-vision-archive", "url": V6.M.archive_url(path),
                   "archive_checksum": raw_digest, "checksum_path": path + ".CHECKSUM",
                   "checksum_raw_sha256": "8" * 64},
    }


def test_allowlists_come_only_from_exact_frozen_bindings(tmp_path):
    symbols = V6.frozen_seed_symbols()
    assert "BTCUSDT" in symbols and len(symbols) == 900
    bad_root = tmp_path / "repo"
    for source in (V6.PLAN_PATH, PIT8, CLASSES):
        target = bad_root / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    mutated = json.loads((bad_root / PIT8.relative_to(ROOT)).read_text())
    mutated["symbols"].append({"symbol": "ATTACKERUSDT"})
    (bad_root / PIT8.relative_to(ROOT)).write_text(json.dumps(mutated))
    with pytest.raises(V6.AuthorityV6Error, match="byte checksum"):
        V6.frozen_seed_symbols(bad_root)


def test_requests_reject_caller_symbol_additions_and_cutoff(tmp_path):
    request = V6.daily_seed_requests(("ATTACKERUSDT",))[0]
    called = False

    def fetch(url):
        nonlocal called
        called = True
        return 500, b"", url

    with pytest.raises(V6.AuthorityV6Error, match="symbol allowlist"):
        V6.acquire_vision((request,), tmp_path / "store", tmp_path / "ledger", fetch,
                          max_total_bytes=1000, phase="seed")
    assert called is False
    with pytest.raises(V6.AuthorityV6Error, match="cutoff"):
        V6._vision_request("klines", "1h", "daily", "BTCUSDT", "2026-11-05")
    hostile_pit = _pit_v9(["ATTACKERUSDT"])
    with pytest.raises(V6.AuthorityV6Error, match="outside frozen seed"):
        V6.frozen_pit_union_symbols(hostile_pit)


@pytest.mark.parametrize("raw", [
    b"A" * 64 + b"  X.zip\n", b"0" * 64 + b" X.zip\n",
    b"0" * 64 + b"  other.zip\n", b"0" * 64 + b"  X.zip\nextra",
])
def test_checksum_parser_is_exact(raw):
    with pytest.raises(V6.AuthorityV6Error):
        V6.parse_official_checksum(raw, "X.zip")


def test_vision_preserves_official_checksum_bytes_before_parse(tmp_path):
    request = V6.daily_seed_requests(("BTCUSDT",))[0]
    archive = b"synthetic zip fixture"
    checksum = V6.sha256(archive).encode() + b"  " + Path(request.path).name.encode() + b"\r\n"

    def fetch(url):
        return 200, checksum if url.endswith(".CHECKSUM") else archive, url

    store, ledger = tmp_path / "store", tmp_path / "ledger.jsonl"
    result = V6.acquire_vision((request,), store, ledger, fetch, max_total_bytes=1000,
                               phase="seed")
    target = store / Path(*request.path.split("/"))
    assert result["verified"] == [request.path]
    assert Path(str(target) + ".CHECKSUM").read_bytes() == checksum
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert records[0]["decision"] == "CHECKSUM_RAW_PRESERVED"
    Path(str(target) + ".CHECKSUM").unlink()
    with pytest.raises(V6.AuthorityV6Error, match="CHECKSUM raw"):
        V6.scan_v6_record(store, request.path, ledger)
    assert json.loads(ledger.read_text().splitlines()[-1])["decision"] == \
        "VISION_PARSER_SCHEMA_CUTOFF_REFUSED"


def test_malformed_checksum_is_preserved_before_refusal(tmp_path):
    request = V6.daily_seed_requests(("BTCUSDT",))[0]

    def fetch(url):
        return 200, b"malformed", url

    store, ledger = tmp_path / "store", tmp_path / "ledger.jsonl"
    with pytest.raises(V6.AuthorityV6Error, match="checksum"):
        V6.acquire_vision((request,), store, ledger, fetch, max_total_bytes=1000, phase="seed")
    target = store / Path(*request.path.split("/"))
    assert Path(str(target) + ".CHECKSUM").read_bytes() == b"malformed"
    assert "CHECKSUM_RAW_PRESERVED" in ledger.read_text()


@pytest.mark.parametrize("failure_stage", ["checksum", "archive"])
def test_vision_transport_exceptions_are_ledgered(failure_stage, tmp_path):
    request = V6.daily_seed_requests(("BTCUSDT",))[0]
    archive = b"fixture"
    checksum = V6.sha256(archive).encode() + b"  " + Path(request.path).name.encode() + b"\n"

    def fetch(url):
        if failure_stage == "checksum" or not url.endswith(".CHECKSUM"):
            raise OSError("hostile transport")
        return 200, checksum, url

    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(V6.AuthorityV6Error, match="fetch failed"):
        V6.acquire_vision((request,), tmp_path / "store", ledger, fetch,
                          max_total_bytes=1000, phase="seed")
    decisions = [json.loads(line)["decision"] for line in ledger.read_text().splitlines()]
    assert decisions[-1] == ("CHECKSUM_FETCH_FAILED" if failure_stage == "checksum"
                             else "ARCHIVE_FETCH_FAILED")


def test_funding_rejects_addition_and_preserves_malformed_raw_before_parse(tmp_path):
    pit = _pit_v9(["BTCUSDT"])
    with pytest.raises(V6.AuthorityV6Error, match="exact PIT-v9 union"):
        V6.capture_funding_residual(("ATTACKERUSDT",), tmp_path / "x", tmp_path / "x.log",
                                    lambda url: (200, b"[]", url), max_total_bytes=100,
                                    pit_v9=pit)
    raw = b"{malformed-json"

    def malformed(url):
        return 200, raw, url

    store, ledger = tmp_path / "store", tmp_path / "ledger.jsonl"
    with pytest.raises(V6.AuthorityV6Error, match="strict UTF-8 JSON"):
        V6.capture_funding_residual(("BTCUSDT",), store, ledger, malformed,
                                    max_total_bytes=1000, pit_v9=pit)
    target = store / "um/api/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-11-01-2026-11-04-p000.json.attempt-000.raw"
    assert target.read_bytes() == raw
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["decision"] == "RAW_RESPONSE_PRESERVED" and rows[0]["raw_sha256"] == V6.sha256(raw)
    assert rows[-1]["decision"] == "PARSER_SCHEMA_CUTOFF_REFUSED"


def test_funding_malformed_retry_preserves_both_attempts(tmp_path):
    pit, store, ledger = _pit_v9(["BTCUSDT"]), tmp_path / "store", tmp_path / "ledger.jsonl"
    malformed = b"{bad"
    with pytest.raises(V6.AuthorityV6Error):
        V6.capture_funding_residual(("BTCUSDT",), store, ledger,
                                    lambda url: (200, malformed, url),
                                    max_total_bytes=1000, pit_v9=pit)
    result = V6.capture_funding_residual(("BTCUSDT",), store, ledger,
                                         lambda url: (200, b"[]", url),
                                         max_total_bytes=1000, pit_v9=pit)
    folder = store / "um/api/fundingRate/BTCUSDT"
    assert (folder / "BTCUSDT-fundingRate-2026-11-01-2026-11-04-p000.json.attempt-000.raw").read_bytes() == malformed
    assert (folder / "BTCUSDT-fundingRate-2026-11-01-2026-11-04-p000.json.attempt-001.raw").read_bytes() == b"[]"
    assert result["incomplete"] == ["BTCUSDT"]


def test_funding_fetch_failure_is_ledgered_before_refusal(tmp_path):
    pit = _pit_v9(["BTCUSDT"])

    def fail(_url):
        raise OSError("fixture transport failure")

    ledger, store = tmp_path / "ledger.jsonl", tmp_path / "store"
    with pytest.raises(V6.AuthorityV6Error, match="fetch failed"):
        V6.capture_funding_residual(("BTCUSDT",), store, ledger, fail,
                                    max_total_bytes=1000, pit_v9=pit)
    assert json.loads(ledger.read_text())["decision"] == "FETCH_FAILED"
    V6.capture_funding_residual(("BTCUSDT",), store, ledger,
                                lambda url: (200, b"[]", url),
                                max_total_bytes=1000, pit_v9=pit)
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    preserved = next(row for row in rows if row["decision"] == "RAW_RESPONSE_PRESERVED")
    assert preserved["attempt"] == 1 and preserved["path"].endswith("attempt-001.raw")


def test_bounded_funding_paginates_preserved_pages(tmp_path):
    pit, symbol, calls = _pit_v9(["BTCUSDT"]), "BTCUSDT", []
    first = [_funding_row(symbol, V6.API_START_MS + i) for i in range(1000)]
    second = [_funding_row(symbol, V6.API_START_MS + 1000)]

    def fetch(url):
        calls.append(url)
        start = int(parse_qs(urlsplit(url).query)["startTime"][0])
        rows = first if start == V6.API_START_MS else second
        return 200, json.dumps(rows, separators=(",", ":")).encode(), url

    store, ledger = tmp_path / "store", tmp_path / "ledger.jsonl"
    result = V6.capture_funding_residual((symbol,), store, ledger, fetch,
                                         max_total_bytes=1_000_000, pit_v9=pit)
    assert result["completed"] == [symbol] and len(calls) == 2
    record = V6.scan_v6_record(
        store, "um/api/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-11-01-2026-11-04-p001.json",
        ledger)
    assert record["rows"] == 1 and record["requested_end_ms"] == V6.API_END_MS


def test_store_scanner_requires_official_checksum_and_schema(tmp_path):
    rel = "um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-11-04.zip"
    name = "BTCUSDT-1h-2026-11-04.csv"
    open_ms = V6._iso_ms("2026-11-04T00:00:00Z")
    row = f"{open_ms},1,2,0.5,1.5,3,{open_ms + V6.HOUR_MS - 1},4,5,1,2,0\n".encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, row)
    raw, digest = buffer.getvalue(), V6.sha256(buffer.getvalue())
    target = tmp_path / Path(*rel.split("/"))
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    Path(str(target) + ".ok").write_text(digest + "\n")
    checksum = digest.encode() + b"  " + target.name.encode() + b"\n"
    Path(str(target) + ".CHECKSUM").write_bytes(checksum)
    acquired = {"kind": "VISION", "decision": "PRESENT_CHECKSUM_VERIFIED", "path": rel,
                "attempt": 0, "url": V6.M.archive_url(rel),
                "checksum_url": V6.M.archive_url(rel) + ".CHECKSUM",
                "raw_sha256": digest, "raw_bytes": len(raw),
                "checksum_raw_sha256": V6.sha256(checksum)}
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(V6.canonical(acquired) + b"\n")
    record = V6.scan_v6_record(tmp_path, rel, ledger)
    assert record["source"]["checksum_raw_sha256"] == V6.sha256(checksum)
    accepted = json.loads(ledger.read_text().splitlines()[-1])
    assert (accepted["decision"] == "VISION_PARSER_SCHEMA_CUTOFF_ACCEPTED"
            and accepted["parsed_content_digest"] == record["parsed_content_digest"]
            and accepted["rows"] == 1)
    Path(str(target) + ".ok").write_text("0" * 64 + "\n")
    with pytest.raises(Exception, match="checksum|SHA-256|derived"):
        V6.scan_v6_record(tmp_path, rel, ledger)
    assert json.loads(ledger.read_text().splitlines()[-1])["decision"] == \
        "VISION_PARSER_SCHEMA_CUTOFF_REFUSED"


def test_vision_malformed_archive_scan_refusal_is_append_only(tmp_path):
    request = V6.daily_seed_requests(("BTCUSDT",))[0]
    raw = b"not-a-zip"
    checksum = V6.sha256(raw).encode() + b"  " + Path(request.path).name.encode() + b"\n"

    def fetch(url):
        return 200, checksum if url.endswith(".CHECKSUM") else raw, url

    store, ledger = tmp_path / "store", tmp_path / "ledger.jsonl"
    V6.acquire_vision((request,), store, ledger, fetch, max_total_bytes=1000, phase="seed")
    before = ledger.read_bytes()
    with pytest.raises(Exception, match="zip"):
        V6.scan_v6_record(store, request.path, ledger)
    after = ledger.read_bytes()
    assert after.startswith(before) and len(after) > len(before)
    refused = json.loads(after.splitlines()[-1])
    assert (refused["decision"] == "VISION_PARSER_SCHEMA_CUTOFF_REFUSED"
            and refused["raw_sha256"] == V6.sha256(raw)
            and refused["checksum_raw_sha256"] == V6.sha256(checksum)
            and refused["acquisition_record_sha256"])


def test_vision_corruption_refusal_retains_attempt_and_allows_contiguous_recovery(tmp_path):
    request = V6._vision_request("klines", "1h", "daily", "BTCUSDT", "2026-11-04")
    open_ms = V6._iso_ms("2026-11-04T00:00:00Z")
    csv = (f"{open_ms},1,2,0.5,1.5,3,{open_ms + V6.HOUR_MS - 1},4,5,1,2,0\n"
           .encode())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-1h-2026-11-04.csv", csv)
    raw = buffer.getvalue()
    checksum = V6.sha256(raw).encode() + b"  BTCUSDT-1h-2026-11-04.zip\n"

    def fetch(url):
        return 200, checksum if url.endswith(".CHECKSUM") else raw, url

    store, ledger = tmp_path / "store", tmp_path / "ledger.jsonl"
    V6.acquire_vision((request,), store, ledger, fetch, max_total_bytes=10000, phase="seed")
    V6.scan_v6_record(store, request.path, ledger)
    target = store / Path(*request.path.split("/"))
    target.write_bytes(b"corrupt-after-acquisition")
    with pytest.raises(Exception):
        V6.scan_v6_record(store, request.path, ledger)
    refused = json.loads(ledger.read_text().splitlines()[-1])
    assert (refused["attempt"] == 0
            and refused["expected_raw_sha256"] == V6.sha256(raw)
            and refused["observed_raw_sha256"] == V6.sha256(b"corrupt-after-acquisition"))

    V6.acquire_vision((request,), store, ledger, fetch, max_total_bytes=10000, phase="seed")
    recovered = V6.scan_v6_record(store, request.path, ledger)
    assert recovered["rows"] == 1
    manifest = V6.build_manifest_v6_from_store(
        MANIFEST5, [request.path], store, ledger, _pit_v9(["BTCUSDT"]))
    assert manifest["files_v6"][0]["path"] == request.path
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert {row["attempt"] for row in rows if row.get("kind") == "VISION"} == {0, 1}


def test_manifest_binds_base_and_rejects_fixture_crosscutoff_duplicates():
    base_raw, good, pit = MANIFEST5.read_bytes(), _vision_record(), _pit_v9(["BTCUSDT"])
    out = V6.build_manifest_v6(base_raw, [good], _ledger_raw([good]), pit)
    assert out["base_manifest"]["byte_sha256"] == V6.load_frozen_plan()["base_authorities"]["manifest_v5"]["byte_sha256"]
    mutated = bytearray(base_raw); mutated[-1] ^= 1
    with pytest.raises(V6.AuthorityV6Error, match="bytes changed"):
        V6.build_manifest_v6(bytes(mutated), [good], _ledger_raw([good]), pit)
    with pytest.raises(V6.AuthorityV6Error, match="ledger byte evidence|scan verdict"):
        V6.build_manifest_v6(base_raw, [good],
                             V6.canonical({"decision": "PRESENT_CHECKSUM_VERIFIED",
                                           "path": good["path"], "raw_sha256": "0" * 64}) + b"\n", pit)
    fixture = V6._record_from_fixture("fixture/a", series="klines", interval="1h",
                                      symbol="BTCUSDT", times=[V6.API_START_MS])
    with pytest.raises(V6.AuthorityV6Error, match="fixture/unallowlisted"):
        V6.build_manifest_v6(base_raw, [fixture], _ledger_raw([fixture]), pit)
    cross = copy.deepcopy(good); cross["declared_end_ms"] = V6.CUTOFF_MS + 1
    with pytest.raises(V6.AuthorityV6Error, match="cross-cutoff"):
        V6.build_manifest_v6(base_raw, [cross], _ledger_raw([cross]), pit)
    with pytest.raises(V6.AuthorityV6Error, match="duplicate .*timestamp"):
        V6.build_manifest_v6(base_raw, [_api_record(0), _api_record(1)],
                             _ledger_raw([_api_record(0), _api_record(1)]), pit)
    conflict = _api_record(1)
    conflict["row_digests"][str(V6.API_START_MS)] = "9" * 64
    with pytest.raises(V6.AuthorityV6Error, match="duplicate .*timestamp"):
        V6.build_manifest_v6(base_raw, [_api_record(0), conflict],
                             _ledger_raw([_api_record(0), conflict]), pit)


@pytest.mark.parametrize("attack", [
    "orphan", "random_link", "wrong_status", "cross_attempt", "cross_path", "mutated_linked_row",
])
def test_manifest_rejects_forged_or_crosslinked_vision_scan_verdict(attack):
    good, pit = _vision_record(), _pit_v9(["BTCUSDT"])
    rows = [json.loads(line) for line in _ledger_raw([good]).splitlines()]
    acquired, scan = rows
    if attack == "orphan":
        rows = [scan]
    elif attack == "random_link":
        scan["acquisition_record_sha256"] = "f" * 64
    elif attack == "wrong_status":
        acquired["decision"] = "CHECKSUM_FAILURE"
        scan["acquisition_record_sha256"] = V6.sha256(V6.canonical(acquired))
    elif attack == "cross_attempt":
        scan["acquisition_attempt"] = acquired["attempt"] + 1
    elif attack == "cross_path":
        acquired["path"] = "um/daily/klines/ETHUSDT/1h/ETHUSDT-1h-2026-11-04.zip"
        scan["acquisition_record_sha256"] = V6.sha256(V6.canonical(acquired))
    else:
        acquired["raw_bytes"] = 999  # link deliberately left bound to the pre-mutation row
    raw = b"\n".join(V6.canonical(row) for row in rows) + b"\n"
    with pytest.raises(V6.AuthorityV6Error, match="ledger byte evidence|scan verdict"):
        V6.build_manifest_v6(MANIFEST5.read_bytes(), [good], raw, pit)


@pytest.mark.parametrize("attack", ["first_99", "gap", "duplicate", "regression", "scan_first"])
def test_manifest_rejects_noncontiguous_vision_attempt_history(attack):
    good, pit = _vision_record(), _pit_v9(["BTCUSDT"])
    acquired, scan = [json.loads(line) for line in _ledger_raw([good]).splitlines()]
    path = good["path"]
    if attack == "first_99":
        acquired["attempt"] = scan["attempt"] = scan["acquisition_attempt"] = 99
        scan["acquisition_record_sha256"] = V6.sha256(V6.canonical(acquired))
        rows = [acquired, scan]
    elif attack == "gap":
        acquired["attempt"] = scan["attempt"] = scan["acquisition_attempt"] = 2
        scan["acquisition_record_sha256"] = V6.sha256(V6.canonical(acquired))
        rows = [{"kind": "VISION", "path": path, "attempt": 0,
                 "decision": "CHECKSUM_FETCH_FAILED"}, acquired, scan]
    elif attack == "duplicate":
        rows = [acquired, copy.deepcopy(acquired), scan]
    elif attack == "regression":
        rows = [acquired, {"kind": "VISION", "path": path, "attempt": 1,
                           "decision": "CHECKSUM_FETCH_FAILED"}, scan]
    else:
        rows = [scan, acquired]
    raw = b"\n".join(V6.canonical(row) for row in rows) + b"\n"
    with pytest.raises(V6.AuthorityV6Error, match="attempt|scan verdict"):
        V6.build_manifest_v6(MANIFEST5.read_bytes(), [good], raw, pit)


def test_manifest_accepts_contiguous_append_only_vision_retry_history():
    good, pit = _vision_record(), _pit_v9(["BTCUSDT"])
    acquired, scan = [json.loads(line) for line in _ledger_raw([good]).splitlines()]
    acquired["attempt"] = scan["attempt"] = scan["acquisition_attempt"] = 1
    scan["acquisition_record_sha256"] = V6.sha256(V6.canonical(acquired))
    failure = {"kind": "VISION", "path": good["path"], "attempt": 0,
               "decision": "ARCHIVE_FETCH_FAILED"}
    raw = b"\n".join(V6.canonical(row) for row in (failure, acquired, scan)) + b"\n"
    out = V6.build_manifest_v6(MANIFEST5.read_bytes(), [good], raw, pit)
    assert out["files_v6"][0]["path"] == good["path"]


def test_manifest_supersession_requires_every_exact_matching_base_row():
    old, pit, base_raw = _sep_base_record(), _pit_v9(["BTCUSDT"]), MANIFEST5.read_bytes()
    proof = _base_proof(old)
    all_times = sorted(int(key) for key in proof["row_digests"])

    one_time = [V6._iso_ms("2026-09-15T00:00:00Z")]
    one = _sep_monthly_record(one_time, proof["row_digests"])
    with pytest.raises(V6.AuthorityV6Error, match="every exact base timestamp"):
        V6.build_manifest_v6(base_raw, [one], _ledger_raw([one]), pit,
                             verified_base_rows={old["path"]: proof})

    missing_times = all_times[:100] + all_times[101:]
    missing = _sep_monthly_record(missing_times, proof["row_digests"])
    with pytest.raises(V6.AuthorityV6Error, match="every exact base timestamp"):
        V6.build_manifest_v6(base_raw, [missing], _ledger_raw([missing]), pit,
                             verified_base_rows={old["path"]: proof})

    conflicting = copy.deepcopy(proof["row_digests"])
    conflicting[str(all_times[100])] = "a" * 64
    bad = _sep_monthly_record(all_times, conflicting)
    with pytest.raises(V6.AuthorityV6Error, match="conflicts with exact base row"):
        V6.build_manifest_v6(base_raw, [bad], _ledger_raw([bad]), pit,
                             verified_base_rows={old["path"]: proof})

    complete = _sep_monthly_record(all_times, proof["row_digests"])
    out = V6.build_manifest_v6(base_raw, [complete], _ledger_raw([complete]), pit,
                               verified_base_rows={old["path"]: proof})
    assert old["path"] in out["superseded_base_paths"]
    assert old in out["base_files_v5"]
    assert old["path"] not in {row["path"] for row in out["active_base_files_v5"]}


def test_manifest_rejects_overlap_without_exact_base_source_proof():
    old, pit = _sep_base_record(), _pit_v9(["BTCUSDT"])
    proof = _base_proof(old)
    times = sorted(int(key) for key in proof["row_digests"])
    complete = _sep_monthly_record(times, proof["row_digests"])
    with pytest.raises(V6.AuthorityV6Error, match="lacks exact source-row evidence"):
        V6.build_manifest_v6(MANIFEST5.read_bytes(), [complete], _ledger_raw([complete]), pit)


def test_pit_replay_only_allows_manifest_binding_and_digest_change():
    first = _pit_v9(["BTCUSDT"])
    replay = copy.deepcopy(first)
    replay["manifest_digest"] = "2" * 64
    replay["digest"] = V6.U.digest_of(replay)
    final = V6.finalize_pit_v9(first, replay, manifest_digest="4" * 64)
    assert final["manifest_digest"] == "4" * 64
    replay["weeks"][-1]["books"]["crypto"]["members"].append("ATTACKERUSDT")
    replay["digest"] = V6.U.digest_of(replay)
    with pytest.raises(V6.AuthorityV6Error, match="outside frozen seed"):
        V6.finalize_pit_v9(first, replay, manifest_digest="4" * 64)


def test_pit_builder_rejects_unbound_base_or_classes_before_rows():
    pit, classes = json.loads(PIT8.read_text()), json.loads(CLASSES.read_text())
    hostile = copy.deepcopy(classes)
    hostile["entries"].append({"symbol": "ATTACKERUSDT"})
    with pytest.raises(V6.AuthorityV6Error, match="exact frozen"):
        V6.build_pit_v9_first_pass(pit, {}, hostile, provisional_manifest_digest="1" * 64)


def _coverage_fixture():
    _, _, eligible, ineligible = _filter_inputs()
    raw_members = [ineligible[0]] + eligible[:20]
    pit = _pit_v9(raw_members)
    filt = _filter_v4(pit)
    replacement = eligible[19]
    assert replacement not in raw_members[:20]
    assert replacement in {s for c in filt["weekly_membership"]["sealed_holdout"] for s in c["members"]}
    rows = {}
    daily = list(range(V6.NEW_CELLS[0] - 30 * V6.DAY_MS, V6.NEW_CELLS[-1], V6.DAY_MS))
    for week in pit["weeks"][-5:]:
        for book in week["books"].values():
            for symbol in book["members"]:
                rows[("klines", "1d", symbol)] = daily
    holdout = V6._iso_ms("2026-09-28T00:00:00Z")
    hourly = list(range(holdout, V6.CUTOFF_MS, V6.HOUR_MS))
    members = {s for c in filt["weekly_membership"]["sealed_holdout"] for s in c["members"]}
    funding_events = {}
    for symbol in members:
        expected = list(range(holdout, V6.CUTOFF_MS, 8 * V6.HOUR_MS))
        funding_events[symbol] = [(holdout - 8 * V6.HOUR_MS, 8)] + [(t, None) for t in expected]
        rows[("klines", "1h", symbol)] = hourly
        rows[("markPriceKlines", "1h", symbol)] = hourly
        rows[("fundingRate", None, symbol)] = expected
    return pit, filt, rows, funding_events, replacement, hourly


def test_filter_membership_includes_replacement_beyond_raw_top20():
    pit, filt, rows, events, replacement, hourly = _coverage_fixture()
    clean = V6.coverage_v4(pit, filt, rows, events, manifest_digest="c" * 64)
    assert clean["guaranteed_period_gaps"] == 0
    broken = copy.deepcopy(rows)
    broken[("markPriceKlines", "1h", replacement)] = hourly[1:]
    report = V6.coverage_v4(pit, filt, broken, events, manifest_digest="c" * 64)
    assert any(g["symbol"] == replacement and g["stream"] == "markPriceKlines:1h"
               for g in report["gaps"])
    hostile = copy.deepcopy(filt)
    hostile["weekly_membership"]["sealed_holdout"][0]["members"][0] = "ATTACKERUSDT"
    hostile["digest"] = V6.content_digest(hostile)
    with pytest.raises(V6.AuthorityV6Error, match="membership digest"):
        V6.coverage_v4(pit, hostile, rows, events, manifest_digest="c" * 64)


def test_coverage_derives_full_funding_grid_and_sparse_rows_fail():
    pit, filt, rows, events, replacement, _ = _coverage_fixture()
    expected = rows[("fundingRate", None, replacement)]
    sparse = copy.deepcopy(rows)
    sparse[("fundingRate", None, replacement)] = expected[::2]
    report = V6.coverage_v4(pit, filt, sparse, events, manifest_digest="c" * 64)
    missing = [g for g in report["gaps"]
               if g["symbol"] == replacement and g["stream"] == "fundingRate:null"]
    assert len(missing) == len(expected) // 2
    no_events = copy.deepcopy(events); no_events.pop(replacement)
    report = V6.coverage_v4(pit, filt, rows, no_events, manifest_digest="c" * 64)
    assert any(g.get("reason") == "MISSING_EXPLICIT_CADENCE_AUTHORITY"
               and g["symbol"] == replacement for g in report["gaps"])


def test_missing_4h_funding_cannot_be_redefined_as_8h():
    start = V6.API_START_MS
    expected = V6.derive_funding_expected(
        [(start - 4 * V6.HOUR_MS, 4), (start, None), (start + 8 * V6.HOUR_MS, None)],
        start_ms=start, end_ms=start + 12 * V6.HOUR_MS)
    assert expected == (start, start + 4 * V6.HOUR_MS, start + 8 * V6.HOUR_MS)


def test_public_module_has_no_candidate_or_evaluator_entrypoint():
    assert not any(name.startswith(("evaluate", "candidate", "promote")) for name in dir(V6))
