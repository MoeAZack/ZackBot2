import hashlib
import json
from pathlib import Path

import pytest

from tools.research import strategy_causal_pack_01_daily_authority as D
from tools.research import manifest as M
from tools.research import strategy_causal_pack_01_archive as A
from tools.research import universe as U


def _small_plan():
    body = {
        "format": D.FORMAT,
        "cutoff_exclusive": D.CUTOFF,
        "allowed_dates": list(D.ALLOWED_DATES),
        "streams": [{"series": series, "interval": interval} for series, interval in D.STREAMS],
        "symbols": ["BTCUSDT"],
        "request_count": len(D.ALLOWED_DATES) * len(D.STREAMS),
        "upstream": "https://data.binance.vision/data/futures/um/daily/",
        "checksum_rule": "fixture",
        "base_manifest": {}, "base_universe": {}, "contract_sha256": "x", "filter_authority_sha256": "y",
    }
    body["digest"] = D._sha(D._canon(body))
    return body


def test_repository_plan_is_explicit_pre_cutoff_and_metadata_only():
    plan = D.make_plan()
    assert plan["allowed_dates"] == [f"2026-09-{day:02d}" for day in range(1, 28)]
    assert "2026-09-28" not in plan["allowed_dates"]
    assert plan["request_count"] == 62 * 27 * 4 == 6696
    assert len(plan["symbols"]) == 62
    requests = D.expand(plan)
    assert len(requests) == 6696
    assert all("/daily/" in row["path"] and not row["path"].endswith("2026-09-28.zip")
               for row in requests)


def test_plan_rejects_any_date_or_availability_outside_allowlist():
    plan = _small_plan()
    plan["allowed_dates"][-1] = "2026-09-28"
    plan["digest"] = D._sha(D._canon({key: value for key, value in plan.items() if key != "digest"}))
    with pytest.raises(D.DailyAuthorityError, match="exactly"):
        D.validate_plan(plan)
    plan = _small_plan()
    plan["available"] = [{"path": "um/daily/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09-28.zip", "bytes": 1}]
    plan["listed_bytes"] = 1
    plan["digest"] = D._sha(D._canon({key: value for key, value in plan.items() if key != "digest"}))
    with pytest.raises(D.DailyAuthorityError, match="subset"):
        D.validate_plan(plan)


def test_concurrent_fetch_keeps_only_upstream_checksum_verified_bytes(tmp_path, monkeypatch):
    plan = _small_plan()
    path = D.archive_path("klines", "4h", "BTCUSDT", D.ALLOWED_DATES[0])
    raw = b"opaque zip bytes; fetch does not open rows"
    digest = hashlib.sha256(raw).hexdigest()
    plan["available"] = [{"path": path, "bytes": len(raw)}]
    plan["listed_bytes"] = len(raw)
    plan["digest"] = D._sha(D._canon({key: value for key, value in plan.items() if key != "digest"}))

    def fake(url):
        body = (digest + "  " + Path(path).name + "\n").encode() if url.endswith(".CHECKSUM") else raw
        return 200, body, url

    monkeypatch.setattr(D.BAF, "http_fetcher", fake)
    assert D.fetch(plan, tmp_path, 1000, workers=8) == (1, len(raw))
    target = tmp_path.joinpath(*path.split("/"))
    assert target.read_bytes() == raw
    assert Path(str(target) + ".ok").read_text().strip() == digest


def test_metadata_amendment_appends_failed_trials_without_changing_research_rules():
    path = D.ROOT / "contracts/strategy_causal_pack_01/precutoff_daily_archive_amendment.v1.json"
    amendment = json.loads(path.read_text(encoding="utf-8"))
    assert amendment["status"] == "FROZEN_AUTHORITY_AMENDMENT_BLOCKED"
    assert amendment["scope"] == {
        "contract_rule_changes_metadata_only": True,
        "authority_build_parsed_rows_for_schema_and_cutoff_validation": True,
        "market_outcomes_inspected_or_candidate_evaluated": False,
        "candidate_run_authorized": False, "fold_change": False, "parameter_change": False,
        "universe_rule_change": False, "promotion_gate_change": False, "holdout_read_authorized": False,
    }
    assert [row["candidate_id"] for row in amendment["failed_trials_append"]] == [
        "pullback_rsi_short_h1_causal_v3", "exp_mom_short_c27_causal_v3"]
    assert all(row["outcome_rows_read"] == 0 and row["retained_in_multiplicity"]
               for row in amendment["failed_trials_append"])
    assert amendment["public_s3_metadata_observation"]["present_files"] == 1611
    funding = amendment["public_s3_metadata_observation"]["by_stream"][-1]
    assert funding == {"series": "fundingRate", "interval": None, "files": 0, "bytes": 0}
    assert amendment["funding_archive_blocker_resolution"]["supersedes"] == "PUBLIC_ARCHIVE_SERIES_ABSENT"
    assert amendment["funding_archive_blocker_resolution"]["explicit_incomplete_symbols"] == [
        "TONUSDT", "VANRYUSDT"]


def _write_funding_api(store, symbol="S00", end_day="2026-09-27", rows=None):
    rows = rows if rows is not None else [
        {"symbol": symbol, "fundingRate": "0.0001", "fundingTime": D.FUNDING_START_MS},
        {"symbol": symbol, "fundingRate": "-0.0002", "fundingTime": D.FUNDING_START_MS + 8 * A.HOUR_MS},
    ]
    rel = f"um/api/fundingRate/{symbol}/{symbol}-fundingRate-2026-09-01-{end_day}-p000.json"
    path = store.joinpath(*rel.split("/")); path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(rows, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest(); path.write_bytes(raw)
    Path(str(path) + ".ok").write_text(digest + "\n", encoding="ascii")
    end_ms = int(__import__('datetime').datetime.fromisoformat(end_day + 'T23:59:59.999+00:00').timestamp() * 1000)
    url = (f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&startTime={D.FUNDING_START_MS}"
           f"&endTime={end_ms}&limit=1000")
    provenance = {"format": "zb-bounded-binance-funding-response/1", "url": url,
                  "response_sha256": digest, "accessed_cairo": "2026-11-06T17:30:00+02:00",
                  "server_time_ms": 1793960000000, "rows": len(rows),
                  "first_funding_time_ms": rows[0]["fundingTime"] if rows else None,
                  "last_funding_time_ms": rows[-1]["fundingTime"] if rows else None,
                  "terminal_page": True}
    Path(str(path) + ".provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return path, rel, M.scan_funding_api(str(path), rel)


def _api_manifest(record):
    value = {"format": M.FORMAT, "manifest_id": "funding-api-fixture", "source_class": "archive-verified",
             "survivor_only": False, "loader_version": M.ARCHIVE_LOADER,
             "available_rule": M.ARCHIVE_RULE, "accessed_cairo": "2026-11-06 fixture", "note": "fixture",
             "data_root": M.ARCHIVE_ROOT, "files": [record]}
    value["digest"] = M.digest_of(value); M.validate(value); return value


def _api_universe(manifest, monday=D.FUNDING_START_MS):
    members = [f"S{i:02d}" for i in range(20)]
    value = {"format": U.FORMAT, "universe_id": U.UNIVERSE_ID, "manifest_digest": manifest["digest"],
             "books": ["crypto"], "weeks": [{"monday_ms": monday, "books": {"crypto": {
                 "members": members, "eligible": 20, "qv30d_usdt": ["1"] * 20}}}],
             "symbols": [{"symbol": symbol, "class": "crypto", "subclass": "fixture"} for symbol in members]}
    value["digest"] = U.digest_of(value); return value


def test_funding_api_source_is_raw_checksum_provenance_and_gap_bound(tmp_path):
    _, _, record = _write_funding_api(tmp_path)
    manifest = _api_manifest(record)
    result = A.RawArchiveAdapter(manifest, _api_universe(manifest), tmp_path).load_funding_sparse(
        "S00", D.FUNDING_START_MS, D.FUNDING_START_MS + 16 * A.HOUR_MS)
    assert [row.time_ms for row in result.rows] == [D.FUNDING_START_MS, D.FUNDING_START_MS + 8 * A.HOUR_MS]
    assert [row.interval_hours for row in result.rows] == [8, 8]
    assert result.gaps == ()
    empty_path, empty_rel, empty = _write_funding_api(tmp_path, rows=[])
    empty_manifest = _api_manifest(empty)
    empty_result = A.RawArchiveAdapter(empty_manifest, _api_universe(empty_manifest), tmp_path).load_funding_sparse(
        "S00", D.FUNDING_START_MS, D.FUNDING_START_MS + 8 * A.HOUR_MS)
    assert empty_result.rows == () and empty_result.gaps


def test_funding_api_16h_observation_gap_stays_explicitly_incomplete(tmp_path):
    start = D.FUNDING_START_MS
    rows = [
        {"symbol": "S00", "fundingRate": "0.0001", "fundingTime": start},
        {"symbol": "S00", "fundingRate": "-0.0002", "fundingTime": start + 16 * A.HOUR_MS},
    ]
    _, _, record = _write_funding_api(tmp_path, rows=rows)
    manifest = _api_manifest(record)
    result = A.RawArchiveAdapter(manifest, _api_universe(manifest), tmp_path).load_funding_sparse(
        "S00", start, start + 24 * A.HOUR_MS)
    assert [(row.time_ms, row.interval_hours, row.rate) for row in result.rows] == [
        (start, 8, 0.0001), (start + 16 * A.HOUR_MS, 8, -0.0002)]
    assert result.gaps == (A.CoverageGap(
        start + 8 * A.HOUR_MS, start + 16 * A.HOUR_MS, "settlement_gap"),)


def test_funding_api_actual_4h_then_8h_observations_are_valid_without_cadence_inference(tmp_path):
    start = D.FUNDING_START_MS
    rows = [
        {"symbol": "S00", "fundingRate": "0.0001", "fundingTime": start},
        {"symbol": "S00", "fundingRate": "0.0002", "fundingTime": start + 4 * A.HOUR_MS},
        {"symbol": "S00", "fundingRate": "-0.0001", "fundingTime": start + 12 * A.HOUR_MS},
    ]
    _, _, record = _write_funding_api(tmp_path, rows=rows)
    manifest = _api_manifest(record)
    result = A.RawArchiveAdapter(manifest, _api_universe(manifest), tmp_path).load_funding_sparse(
        "S00", start, start + 20 * A.HOUR_MS)
    assert [row.time_ms for row in result.rows] == [
        start, start + 4 * A.HOUR_MS, start + 12 * A.HOUR_MS]
    assert [row.interval_hours for row in result.rows] == [8, 8, 8]
    assert [row.rate for row in result.rows] == [0.0001, 0.0002, -0.0001]
    assert result.gaps == ()


def test_funding_api_duplicate_bounds_and_cutoff_crossing_refused_before_bytes(tmp_path):
    meta = M.archive_meta("um/api/fundingRate/S00/S00-fundingRate-2026-09-01-2026-09-27-p000.json")
    duplicate = ("https://fapi.binance.com/fapi/v1/fundingRate?symbol=S00&startTime=1788220800000"
                 "&startTime=1788220800001&endTime=1790553599999&limit=1000")
    assert not M._funding_api_url(duplicate, meta)
    path, _, record = _write_funding_api(tmp_path, end_day="2026-09-28")
    manifest = _api_manifest(record); universe = _api_universe(manifest)
    path.unlink()
    with pytest.raises(A.ArchiveIncomplete, match="crosses frozen cutoff; not read"):
        A.RawArchiveAdapter(manifest, universe, tmp_path).load_funding_sparse(
            "S00", D.FUNDING_START_MS, A.CUTOFF_MS)


def _write_kline_api(store, *, start=D.FUNDING_START_MS, end=None, rows=None, series="klines", scan=True):
    step = M.INTERVALS["1h"]; end = end or start + 2 * step
    rows = rows if rows is not None else [
        [start + i * step, "1", "2", "0.5", "1.5", "3", start + (i + 1) * step - 1,
         "4.5", 2, "1", "1.5", "0"] for i in range((end - start) // step)]
    rel = f"um/api/{series}/S00/1h/S00-1h-{start:013d}-{end:013d}-p000.json"
    path = store.joinpath(*rel.split("/")); path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(rows, separators=(",", ":")).encode(); digest = hashlib.sha256(raw).hexdigest()
    path.write_bytes(raw); Path(str(path) + ".ok").write_text(digest + "\n", encoding="ascii")
    endpoint = "klines" if series == "klines" else "markPriceKlines"
    url = (f"https://fapi.binance.com/fapi/v1/{endpoint}?symbol=S00&interval=1h&startTime={start}"
           f"&endTime={end - 1}&limit=1500")
    provenance = {"format": "zb-bounded-binance-kline-response/1", "url": url,
                  "params": {"symbol": "S00", "interval": "1h", "startTime": start,
                             "endTime": end - 1, "limit": 1500},
                  "response_sha256": digest, "accessed_cairo": "2026-11-06T17:30:00+02:00",
                  "server_time_ms": 1793960000000, "rows": len(rows),
                  "first_open_ms": rows[0][0] if rows else None,
                  "last_open_ms": rows[-1][0] if rows else None, "terminal_page": True}
    Path(str(path) + ".provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    if scan:
        return path, rel, M.scan_kline_api(str(path), rel)
    source = {"kind": M.KLINE_API_KIND, "url": url, "archive_checksum": digest,
              "provenance_path": rel + ".provenance.json",
              "provenance_sha256": hashlib.sha256(Path(str(path) + ".provenance.json").read_bytes()).hexdigest()}
    return path, rel, {"source": source}


def test_kline_api_is_checksum_bound_strict_grid_and_duplicate_query_refused(tmp_path):
    path, rel, record = _write_kline_api(tmp_path)
    manifest = _api_manifest(record)
    rows = A.RawArchiveAdapter(manifest, _api_universe(manifest), tmp_path).dataset.rows(record)
    assert [row.open_ms for row in rows] == [D.FUNDING_START_MS, D.FUNDING_START_MS + M.INTERVALS["1h"]]
    meta = M.archive_meta(rel)
    duplicate = record["source"]["url"].replace("&startTime=", "&startTime=1&startTime=")
    assert not M._kline_api_url(duplicate, meta)
    malformed = json.loads(path.read_text()); malformed[1][0] += 1
    with pytest.raises(M.ManifestError, match="alignment|contiguous"):
        M.parse_kline_api(rel, json.dumps(malformed).encode(), meta)


def test_kline_api_cutoff_crossing_path_rejected_before_response_open(tmp_path):
    start = A.CUTOFF_MS - M.INTERVALS["1h"]
    end = A.CUTOFF_MS + M.INTERVALS["1h"]
    path, rel, record = _write_kline_api(tmp_path, start=start, end=end, scan=False)
    path.unlink()
    with pytest.raises(M.ManifestError, match="crosses frozen cutoff; not read"):
        M.read_kline_api(str(path), rel, record["source"])


def test_gap_windows_are_digest_bound_merged_and_never_cross_cutoff():
    report = json.loads((D.ROOT / "contracts/strategy_causal_pack_01/"
                         "precutoff_authority_coverage_preflight.v1.json").read_text())
    windows = D.kline_gap_windows(report)
    assert len(windows) == 220
    assert all(row["end_ms"] <= A.CUTOFF_MS and row["start_ms"] < row["end_ms"] for row in windows)
    report["folds"][0]["gaps"][0]["end_ms"] = A.CUTOFF_MS + 1
    with pytest.raises(D.DailyAuthorityError, match="digest-valid"):
        D.kline_gap_windows(report)


def test_monthly_gap_plan_never_requests_september_2026_or_existing_paths():
    report = json.loads((D.ROOT / "contracts/strategy_causal_pack_01/"
                         "precutoff_authority_coverage_preflight.v1.json").read_text())
    manifest = M.load(str(D.ROOT / "research_evidence/manifests/binance-um-archive-precutoff-daily-v2.json.gz"))
    requests = D.monthly_gap_requests(report, manifest)
    assert len(requests) == 436
    assert all("/monthly/" in row["path"] and not row["path"].endswith("-2026-09.zip") for row in requests)
    assert all(M.archive_meta(row["path"])["month_end_ms"] <= A.CUTOFF_MS for row in requests)


def test_funding_summary_is_digest_bound_and_preserves_empty_symbols():
    path = D.ROOT / "contracts/strategy_causal_pack_01/precutoff_funding_authority_summary.v1.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["digest"] == D._sha(D._canon({k: v for k, v in summary.items() if k != "digest"}))
    assert summary["complete_symbols"] == 60
    assert summary["explicit_incomplete_symbols"] == ["TONUSDT", "VANRYUSDT"]
    empties = [row for row in summary["symbols"] if row["empty_is_explicit_incomplete"]]
    assert [row["symbol"] for row in empties] == ["TONUSDT", "VANRYUSDT"]


def test_coverage_preflight_blocks_replacement_identity_before_any_outcome_run():
    path = D.ROOT / "contracts/strategy_causal_pack_01/precutoff_authority_coverage_preflight.v2.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["digest"] == D._sha(D._canon({k: v for k, v in report.items() if k != "digest"}))
    assert report["guaranteed_period_gaps_by_stream"] == {
        "fundingRate:None": 0, "klines:1h": 50, "klines:4h": 67, "markPriceKlines:1h": 67}
    missing = report["wf3_september"]["daily_grid_missing"]
    assert len(missing) == 1244
    assert all(item["date"] in D.ALLOWED_DATES for item in missing)
    assert report["mode"].startswith("authority metadata/grid only")
