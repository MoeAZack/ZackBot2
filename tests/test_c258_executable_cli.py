"""Actual CLI replay of locally generated prices; no real economic rows are read."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import gzip
import hashlib
import io
import json
import math
import subprocess
import sys
import zipfile

import pytest

from tools.research import c258_executable as E
from tools.research import c258_production_authority as P
from tools.research import authority_v6 as V6
from tools.research import manifest as M

C = E.C


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def doc_bytes(value):
    value = deepcopy(value)
    value.pop("digest", None)
    value["digest"] = C._sha256_bytes(C._canonical_bytes(value))
    return value, C._canonical_bytes(value)


@pytest.fixture(scope="module")
def synthetic_authority(tmp_path_factory):
    directory = tmp_path_factory.mktemp("c258-complete-cli")
    root, store = directory/"authority", directory/"store"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(C.ROOT), str(root)],
                   check=True, capture_output=True)
    git(root, "read-tree", "HEAD")
    git(root, "config", "user.name", "C258 Synthetic")
    git(root, "config", "user.email", "c258@example.invalid")
    # Only immutable authority metadata is copied. Prices below are generated.
    plan_raw = C.PLAN_PATH.read_bytes()
    plan = json.loads(plan_raw)
    write(root/C.PLAN_PATH.relative_to(C.ROOT), plan_raw)
    for key in ("pit_v8", "instrument_classes_v1"):
        relative = plan["base_authorities"][key]["path"]
        raw = C._git(C.ROOT, "show", f"HEAD:{relative}", binary=True)
        write(root/relative, raw)
    pit = json.loads((root/plan["base_authorities"]["pit_v8"]["path"]).read_bytes())
    for monday in V6.NEW_CELLS:
        cell = deepcopy(pit["weeks"][-1])
        cell["monday_ms"] = monday
        cell["monday_utc"] = datetime.fromtimestamp(monday/1000, timezone.utc).isoformat().replace("+00:00", "Z")
        pit["weeks"].append(cell)
    lo, hi = E.frozen_window("development")
    seed = 1734307200000  # Generated-price segment starts 16 Dec 2024 UTC.
    week = max((week for week in pit["weeks"] if week["monday_ms"] <= seed),
               key=lambda week: week["monday_ms"])
    members = [symbol for symbol in week["books"]["crypto"]["members"]
               if symbol != "BTCUSDT"][:20]
    assert len(members) == 20
    records = []
    paths_by_symbol = {}
    def archive(symbol, series, rows):
        grouped = {}
        for row in rows:
            period = datetime.fromtimestamp(row[0]/1000, timezone.utc).strftime("%Y-%m")
            grouped.setdefault(period, []).append(row)
        for period, rows in grouped.items():
            interval = "fundingRate" if series == "fundingRate" else "1h"
            relative = f"um/monthly/{series}/{symbol}/"+("" if series == "fundingRate" else "1h/")+f"{symbol}-{interval}-{period}.zip"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                header = M.FUNDING_HEADER if series == "fundingRate" else M.KLINE_HEADER
                raw_rows = header+b"\n"+b"\n".join(",".join(map(str, row)).encode() for row in rows)+b"\n"
                zipped.writestr(Path(relative).with_suffix(".csv").name, raw_rows)
            raw = buffer.getvalue()
            write(store/relative, raw)
            digest = hashlib.sha256(raw).hexdigest()
            write(Path(str(store/relative)+".ok"), digest.encode())
            records.append({"path": relative, "sha256": digest, "bytes": len(raw), "rows": len(rows),
                "first_open_ms": rows[0][0], "last_open_ms": rows[-1][0],
                "source": {"kind": M.ARCHIVE_KIND, "url": M.archive_url(relative), "archive_checksum": digest}})
            paths_by_symbol.setdefault(symbol, []).append(relative)
    for rank, symbol in enumerate(members+["BTCUSDT"]):
        stamps = range(lo, hi, C.HOUR_MS) if symbol == "BTCUSDT" else range(seed, seed+240*C.HOUR_MS, C.HOUR_MS)
        rows = []
        for hour, stamp in enumerate(stamps):
            slope = .004 if rank < 2 else -.004 if 18 <= rank < 20 else (rank-10)*.00002
            price = (100+math.sin(hour/19) if symbol == "BTCUSDT"
                     else 100*math.exp(slope*min(hour, 160)))
            rows.append([stamp, price, price+1, price-1, price, 1,
                         stamp+C.HOUR_MS-1, 1e9, 1, 1, 1, 0])
        archive(symbol, "klines", rows)
        archive(symbol, "markPriceKlines", rows)
        if symbol != "BTCUSDT":
            settlements = [[stamp, 4, .00001] for stamp in range(seed, seed+240*C.HOUR_MS, 4*C.HOUR_MS)]
            if rank == 0:
                relative = f"um/api/fundingRate/{symbol}/{symbol}-fundingRate-2024-12-16-2024-12-25-p000.json"
                raw = json.dumps([{"symbol": symbol, "fundingTime": row[0], "fundingRate": str(row[2])}
                                  for row in settlements]).encode()
                write(store/relative, raw)
                records.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw), "rows": len(settlements), "first_open_ms": settlements[0][0],
                    "last_open_ms": settlements[-1][0], "source": {"kind": M.FUNDING_API_KIND}})
            else:
                archive(symbol, "fundingRate", settlements)
    manifest, manifest_raw = doc_bytes({"format": "zb-binance-um-archive-manifest/6",
        "authority_id": C.AUTHORITY_ID, "cutoff_exclusive_ms": V6.CUTOFF_MS,
        "active_base_files_v5": records, "files_v6": [], "synthetic_fixture_only": True})
    pit["manifest_digest"] = manifest["digest"]
    pit, pit_raw = doc_bytes(pit)
    snapshot_path = "contracts/strategy_causal_pack_01/c258-synthetic-public-filters.json"
    snapshot_raw = C._canonical_bytes({"symbols": [{"symbol": symbol, "status": "TRADING",
        "contractType": "PERPETUAL", "quoteAsset": "USDT", "underlyingType": "COIN",
        "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.0001"},
                    {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000000"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"}]} for symbol in members]})
    write(root/snapshot_path, snapshot_raw)
    filters, filter_raw = doc_bytes({"authority": "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY",
        "version": 4, "pit_universe_path": C.EXPECTED_AUTHORITY_PATHS["pit_v9"],
        "pit_universe_sha256": hashlib.sha256(pit_raw).hexdigest(), "pit_universe_digest": pit["digest"],
        "fold_bindings": plan["acquisition_sequence"][5]["fold_bindings"],
        "snapshots": [{"captured_at": capture, "server_time": capture, "path": snapshot_path,
                       "sha256": hashlib.sha256(snapshot_raw).hexdigest()}
                      for capture in sorted(set(plan["acquisition_sequence"][5]["fold_bindings"].values()))]})
    streams = ["klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null"]
    coverage, coverage_raw = doc_bytes({"format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
        "authority_version": 4, "cutoff_exclusive_ms": V6.CUTOFF_MS, "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
        "required_streams": streams, "guaranteed_period_gaps": 0, "guaranteed_period_gaps_by_stream": dict.fromkeys(streams, 0),
        "gaps": [], "manifest_digest": manifest["digest"], "pit_universe_digest": pit["digest"],
        "filter_authority_digest": filters["digest"]})
    raw_docs = [gzip.compress(manifest_raw, mtime=0), pit_raw, filter_raw, coverage_raw]
    docs = [manifest, pit, filters, coverage]
    for path, raw in zip(C.EXPECTED_AUTHORITY_PATHS.values(), raw_docs):
        write(root/path, raw)
    for path in P.IMPLEMENTATION_PATHS:
        write(root/path, (C.ROOT/path).read_bytes())
    git(root, "add", *C.EXPECTED_AUTHORITY_PATHS.values(), snapshot_path, *P.IMPLEMENTATION_PATHS)
    git(root, "commit", "-m", "synthetic C258 checksum archive and protected metadata")
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", C.AUTHORITY_PROTECTED_REF, head)
    pins = C.AuthorityPins(head, *(C.ArtifactPin(path, hashlib.sha256(raw).hexdigest(), doc["digest"])
        for path, raw, doc in zip(C.EXPECTED_AUTHORITY_PATHS.values(), raw_docs, docs)))
    pin_path = directory/"pins.json"
    pin_path.write_text(json.dumps(asdict(pins)), encoding="utf-8")
    return dict(root=root, store=store, pins=pins, pin_path=pin_path, directory=directory,
                member=members[3], corrupt_path=paths_by_symbol[members[3]][0])


def run_cli(fixture, path, claims):
    return subprocess.run([sys.executable, "-m", "tools.research.run_cross_sectional_relative_strength_continuation_c258_stagea",
        "--synthetic-evaluate", "--authority-pins", str(fixture["pin_path"]),
        "--authority-root", str(fixture["root"]), "--archive-store", str(fixture["store"]),
        "--claim-directory", str(claims), "--output", str(path)], cwd=C.ROOT,
        capture_output=True, text=True)


def test_actual_cli_checksum_zip_api_book_report_and_development_veto(synthetic_authority, tmp_path):
    output = tmp_path/"result.json"
    run = run_cli(synthetic_authority, output, tmp_path/"claims")
    assert run.returncode == 0, run.stderr
    result = json.loads(output.read_bytes())
    assert result["result"]["verdict"] == "REJECT_DEVELOPMENT", result["result"].get("technical_blocker")
    assert not result["result"]["forward_opened"] and not result["sealed_holdout_opened"]
    fold = result["result"]["folds"][0]
    assert fold["base"]["closed_attempts"] > 0
    assert fold["base"]["fees"] > 0 and fold["stress"]["slippage"] > fold["base"]["slippage"]
    assert fold["pair_and_book_return_correlations"]["pair_return_matrix"]
    assert result["result"]["readiness"]["C257_overlap"] == "UNAVAILABLE_NO_ACCEPTED_C257_TAPE"
    assert any(value == 0 for value in fold["weeks_including_zero"].values())
    assert set(fold["capital_simulations"]) == {"1000", "2000", "3000", "4000"}
    assert result["result"]["trial_ledger"]
    repeated = run_cli(synthetic_authority, tmp_path/"second.json", tmp_path/"claims")
    assert repeated.returncode != 0 and "already claimed" in repeated.stderr


def test_corrupted_pre_entry_symbol_aborts_whole_cli_instead_of_universe_reduction(synthetic_authority, tmp_path):
    path = synthetic_authority["store"]/synthetic_authority["corrupt_path"]
    raw = path.read_bytes()
    path.write_bytes(raw+b"corrupt")
    try:
        output = tmp_path/"corrupt.json"
        run = run_cli(synthetic_authority, output, tmp_path/"claims")
        assert run.returncode == 0, run.stderr
        result = json.loads(output.read_bytes())["result"]
        assert result["verdict"] == "INCOMPLETE"
        assert not result["forward_opened"] and not result["folds"]
    finally:
        path.write_bytes(raw)


def test_protected_amendment_unlocks_ancestor_outputs_and_binds_frozen_implementation(
        synthetic_authority, monkeypatch):
    root, original_pins = synthetic_authority["root"], synthetic_authority["pins"]
    contract_path = C.CONTRACT_PATH.relative_to(C.ROOT).as_posix()
    # Authority publication may predate candidate integration. The output tree
    # intentionally lacks the candidate contract; accepted integration restores
    # its exact original bytes before publishing the mechanical amendment.
    git(root, "update-index", "--force-remove", contract_path)
    git(root, "commit", "-m", "synthetic authority output tree before candidate integration")
    pins = replace(original_pins, protected_head=git(root, "rev-parse", "HEAD"))
    write(root/contract_path, C.CONTRACT_PATH.read_bytes())
    git(root, "add", contract_path)
    git(root, "commit", "-m", "synthetic independently accepted candidate integration")
    implementation_head = git(root, "rev-parse", "HEAD")
    evidence_path = "docs/reviews/c258-synthetic-independent-acceptance.json"
    evidence = C._canonical_bytes({"accepted_implementation_head": implementation_head,
                                  "verdict": "PASS", "independent": True})
    write(root/evidence_path, evidence)
    amendment = {"format": "zb-c258-stagea-execution-authority/1", "candidate_id": 258,
        "contract_sha256": C.CONTRACT_SHA256, "default_off": True, "paper_testnet_only": True,
        "authority_pins": asdict(pins), "accepted_implementation_head": implementation_head,
        "implementation_blobs": {path: C._sha256(C.ROOT/path) for path in P.IMPLEMENTATION_PATHS},
        "independent_acceptance": {"path": evidence_path, "sha256": C._sha256_bytes(evidence)}}
    write(root/P.AMENDMENT_PATH, C._canonical_bytes(amendment))
    git(root, "add", P.AMENDMENT_PATH, evidence_path)
    git(root, "commit", "-m", "synthetic mechanical amendment after accepted implementation")
    amended_head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", C.AUTHORITY_PROTECTED_REF, amended_head)
    try:
        # The fixture index intentionally omits unrelated working files. Only
        # that status probe is replaced; protected refs/blobs use real Git.
        real_git = C._git
        def fixture_git(path, *args, **kwargs):
            if Path(path) == root and args == ("status", "--porcelain"):
                return ""
            return real_git(path, *args, **kwargs)
        monkeypatch.setattr(C, "_git", fixture_git)
        resolved, _, _ = P.protected_amendment(root)
        assert resolved == pins and amended_head != pins.protected_head
        assert C.validate_authority_pins(pins, root=root)["manifest_v6"]["synthetic_fixture_only"]
        with pytest.raises(C.EvaluationLocked, match="synthetic source"):
            P.authorize_production(root)
        tampered = deepcopy(amendment)
        tampered["implementation_blobs"]["tools/research/manifest.py"] = "0"*64
        write(root/P.AMENDMENT_PATH, C._canonical_bytes(tampered))
        git(root, "add", P.AMENDMENT_PATH)
        git(root, "commit", "-m", "hostile changed accepted implementation pin")
        git(root, "update-ref", C.AUTHORITY_PROTECTED_REF, git(root, "rev-parse", "HEAD"))
        with pytest.raises(C.EvaluationLocked, match="invalid protected"):
            P.authorize_production(root)
        write(root/P.AMENDMENT_PATH, C._canonical_bytes(amendment))
        write(root/contract_path, b"{}")
        git(root, "add", P.AMENDMENT_PATH, contract_path)
        git(root, "commit", "-m", "hostile changed protected frozen contract")
        git(root, "update-ref", C.AUTHORITY_PROTECTED_REF, git(root, "rev-parse", "HEAD"))
        with pytest.raises(C.EvaluationLocked, match="invalid protected"):
            P.authorize_production(root)
    finally:
        git(root, "update-ref", C.AUTHORITY_PROTECTED_REF, original_pins.protected_head)


@pytest.mark.parametrize("override", [["--mean-R", "999"], ["--fold", "sealed_holdout"],
                                       ["--provided-result", "forged.json"]])
def test_cli_has_no_caller_metric_or_holdout_relabel_input(override, tmp_path):
    result = subprocess.run([sys.executable, "-m",
        "tools.research.run_cross_sectional_relative_strength_continuation_c258_stagea", *override],
        cwd=C.ROOT, capture_output=True, text=True)
    assert result.returncode != 0 and "unrecognized arguments" in result.stderr
    assert not list(tmp_path.iterdir())
