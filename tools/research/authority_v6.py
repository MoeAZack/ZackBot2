"""Deterministic acquisition/build pipeline for pre-cutoff authority v6.

This module is deliberately candidate-blind.  It knows only the frozen authority
extension plan, checksum-bound Binance Vision artifacts, bounded funding-history
responses, PIT ranking inputs, frozen exchange-filter snapshots, and coverage grids.
It never imports a strategy runner or an outcome/identity ledger.

The public functions accept injected fetchers and in-memory rows so hostile fixture
tests can exercise every boundary without touching the network or real market data.
The CLI exposes only metadata planning and acquisition/build operations; no evaluation
entry point exists here.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from tools.research import manifest as M
from tools.research import universe as U


ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
PLAN_SHA256 = "30080d16132348fabceb91bffe110f43ee33824c4496622f736ee6a6c4d7af60"
CUTOFF_MS = 1_793_836_800_000
API_START_MS = 1_793_491_200_000
API_END_MS = CUTOFF_MS - 1
VISION_ORIGIN = "https://data.binance.vision"
FUNDING_ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"
NEW_CELLS = (
    1_791_158_400_000, 1_791_763_200_000, 1_792_368_000_000,
    1_792_972_800_000, 1_793_577_600_000,
)
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SYMBOL = re.compile(r"^[A-Z0-9]+$")
CHECKSUM = re.compile(rb"^([0-9a-f]{64})  ([^/\r\n]+)\r?\n?$")


class AuthorityV6Error(ValueError):
    """Fail-closed authority-v6 refusal."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def content_digest(value: Mapping) -> str:
    return sha256(canonical({k: v for k, v in value.items() if k != "digest"}))


def _load_strict(raw: bytes, label: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=lambda value: (
            _ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuthorityV6Error(f"{label} is not strict UTF-8 JSON") from exc


def load_frozen_plan(path: Path = PLAN_PATH) -> dict:
    raw = path.read_bytes()
    # The plan is also structurally checked because an implementation branch may be
    # mechanically rebased while retaining these exact frozen economics/boundaries.
    plan = _load_strict(raw, "authority-v6 plan")
    if not isinstance(plan, dict):
        raise AuthorityV6Error("authority-v6 plan must be an object")
    if sha256(raw) != PLAN_SHA256:
        raise AuthorityV6Error("authority-v6 frozen plan byte checksum changed")
    if (plan.get("authority_id"), plan.get("version"), plan.get("status")) != (
            "BINANCE-UM-PRECUTOFF-AUTHORITY-V6", 6, "FROZEN_METADATA_ONLY_NOT_ACQUIRED"):
        raise AuthorityV6Error("authority-v6 plan identity/status changed")
    if plan.get("cutoff", {}).get("exclusive_ms") != CUTOFF_MS:
        raise AuthorityV6Error("authority-v6 cutoff changed")
    api = plan.get("allowed_sources", {}).get("binance_usdm_funding_api", {})
    if (api.get("first_start_ms"), api.get("last_end_ms"), api.get("limit")) != (
            API_START_MS, API_END_MS, 1000):
        raise AuthorityV6Error("authority-v6 bounded funding API contract changed")
    return plan


def _load_bound_json(root: Path, binding: Mapping, label: str) -> tuple[dict, bytes]:
    rel = binding.get("path")
    if not isinstance(rel, str) or Path(rel).is_absolute():
        raise AuthorityV6Error(f"{label} path binding is invalid")
    path = (root.resolve() / Path(*rel.split("/"))).resolve()
    if root.resolve() not in path.parents:
        raise AuthorityV6Error(f"{label} path escapes repository root")
    raw = path.read_bytes()
    if sha256(raw) != binding.get("byte_sha256"):
        raise AuthorityV6Error(f"{label} byte checksum changed")
    value = _load_strict(raw, label)
    if not isinstance(value, dict):
        raise AuthorityV6Error(f"{label} must be an object")
    expected_content = binding.get("content_digest")
    if expected_content is not None and value.get("digest") != expected_content:
        raise AuthorityV6Error(f"{label} content digest changed")
    return value, raw


def frozen_seed_symbols(root: Path = ROOT) -> tuple[str, ...]:
    """Derive the only legal seed symbols from exact frozen bytes."""
    plan = load_frozen_plan(root / PLAN_PATH.relative_to(ROOT))
    pit, _ = _load_bound_json(root, plan["base_authorities"]["pit_v8"], "PIT-v8")
    classes, _ = _load_bound_json(
        root, plan["base_authorities"]["instrument_classes_v1"], "instrument classes")
    return seed_symbols(pit, classes)


def _validate_pit_v9_prefix(pit_v9: Mapping, root: Path = ROOT) -> None:
    plan = load_frozen_plan(root / PLAN_PATH.relative_to(ROOT))
    base, _ = _load_bound_json(root, plan["base_authorities"]["pit_v8"], "PIT-v8")
    if pit_v9.get("digest") != U.digest_of(dict(pit_v9)):
        raise AuthorityV6Error("PIT-v9 digest mismatch")
    if pit_v9.get("rule") != base.get("rule"):
        raise AuthorityV6Error("PIT-v9 changed the frozen ranking rule")
    base_weeks = base.get("weeks", ())
    weeks = pit_v9.get("weeks", ())
    if weeks[:len(base_weeks)] != base_weeks:
        raise AuthorityV6Error("PIT-v9 changed/reordered a PIT-v8 cell")
    tail = [week.get("monday_ms") for week in weeks[len(base_weeks):]]
    if tail != list(NEW_CELLS):
        raise AuthorityV6Error("PIT-v9 extension cells differ from the frozen five-cell plan")
    frozen_symbols = set(frozen_seed_symbols(root))
    additions = _symbols_from_pit(pit_v9) - frozen_symbols
    if additions:
        raise AuthorityV6Error(f"PIT-v9 contains symbols outside frozen seed bindings: {sorted(additions)[:3]}")


def frozen_pit_union_symbols(pit_v9: Mapping, root: Path = ROOT) -> tuple[str, ...]:
    _validate_pit_v9_prefix(pit_v9, root)
    return pit_union_symbols(pit_v9)


def _symbols_from_pit(pit: Mapping) -> set[str]:
    out: set[str] = set()
    for row in pit.get("symbols", ()):
        if isinstance(row, Mapping) and isinstance(row.get("symbol"), str):
            out.add(row["symbol"])
    for week in pit.get("weeks", ()):
        for book in (week.get("books") or {}).values():
            out.update(s for s in book.get("members", ()) if isinstance(s, str))
        for key in ("vetoes", "gaps"):
            out.update(row[0] for row in week.get(key, ())
                       if isinstance(row, list) and row and isinstance(row[0], str))
        out.update(row["symbol"] for row in week.get("not_addressable", ())
                   if isinstance(row, Mapping) and isinstance(row.get("symbol"), str))
    return out


def _vision_symbol(symbol: object) -> bool:
    return (isinstance(symbol, str) and bool(symbol)
            and all(("A" <= ch <= "Z") or ("0" <= ch <= "9") or ord(ch) > 127
                    for ch in symbol))


def seed_symbols(pit_v8: Mapping, instrument_classes: Mapping) -> tuple[str, ...]:
    """Frozen seed union; archive presence/current listings cannot affect it."""
    symbols = _symbols_from_pit(pit_v8)
    symbols.update(row["symbol"] for row in instrument_classes.get("entries", ())
                   if isinstance(row, Mapping) and isinstance(row.get("symbol"), str))
    if not symbols or any(not _vision_symbol(symbol) for symbol in symbols):
        raise AuthorityV6Error("PIT seed contains an invalid archive-path symbol")
    return tuple(sorted(symbols))


def pit_union_symbols(pit_v9: Mapping) -> tuple[str, ...]:
    """Symbols needed by a PIT-v9 cell or its explicit audit/veto evidence."""
    symbols: set[str] = set()
    for week in pit_v9.get("weeks", ()):
        if week.get("monday_ms", CUTOFF_MS) > NEW_CELLS[-1]:
            continue
        for book in (week.get("books") or {}).values():
            symbols.update(book.get("members", ()))
        for key in ("vetoes", "gaps"):
            symbols.update(row[0] for row in week.get(key, ()) if isinstance(row, list) and row)
        symbols.update(row.get("symbol") for row in week.get("not_addressable", ())
                       if isinstance(row, Mapping))
    if None in symbols or not symbols or any(not _vision_symbol(s) for s in symbols):
        raise AuthorityV6Error("PIT-v9 union contains an invalid archive-path symbol")
    return tuple(sorted(symbols))


@dataclass(frozen=True)
class VisionRequest:
    path: str
    url: str
    checksum_url: str
    series: str
    interval: str | None
    symbol: str
    period_start_ms: int
    period_end_ms: int


def _vision_request(series: str, interval: str | None, cadence: str,
                    symbol: str, period: str) -> VisionRequest:
    if cadence not in ("monthly", "daily") or series not in (
            "klines", "markPriceKlines", "fundingRate"):
        raise AuthorityV6Error("unsupported Vision coordinate")
    if not _vision_symbol(symbol):
        raise AuthorityV6Error("invalid Vision symbol")
    middle = f"/{interval}" if interval else ""
    leaf_interval = interval or "fundingRate"
    rel = f"um/{cadence}/{series}/{symbol}{middle}/{symbol}-{leaf_interval}-{period}.zip"
    meta = M.archive_meta(rel)
    start, end = meta["month_start_ms"], meta["month_end_ms"]
    if end > CUTOFF_MS or (cadence == "monthly" and period == "2026-11"):
        raise AuthorityV6Error("Vision artifact crosses the exclusive cutoff")
    path = "/data/futures/" + rel
    url = VISION_ORIGIN + urllib.parse.quote(path, safe="/")
    return VisionRequest(rel, url, url + ".CHECKSUM", series, interval, symbol, start, end)


def daily_seed_requests(symbols: Sequence[str]) -> tuple[VisionRequest, ...]:
    requests = []
    for symbol in sorted(set(symbols)):
        for month in ("2026-09", "2026-10"):
            requests.append(_vision_request("klines", "1d", "monthly", symbol, month))
        requests.append(_vision_request("klines", "1d", "daily", symbol, "2026-11-01"))
    return tuple(sorted(requests, key=lambda row: row.path))


def intraday_requests(symbols: Sequence[str]) -> tuple[VisionRequest, ...]:
    requests = []
    for symbol in sorted(set(symbols)):
        for series, interval in (("klines", "1h"), ("markPriceKlines", "1h"),
                                 ("fundingRate", None)):
            for month in ("2026-09", "2026-10"):
                requests.append(_vision_request(series, interval, "monthly", symbol, month))
            if series != "fundingRate":
                for day in ("2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"):
                    requests.append(_vision_request(series, interval, "daily", symbol, day))
    return tuple(sorted(requests, key=lambda row: row.path))


def parse_official_checksum(raw: bytes, basename: str) -> str:
    match = CHECKSUM.fullmatch(raw)
    if not match or match.group(2) != basename.encode("utf-8", "strict"):
        raise AuthorityV6Error("official checksum is malformed or bound to another basename")
    return match.group(1).decode("ascii")


def _safe_store_path(store: Path, rel: str) -> Path:
    M.check_rel_path(rel)
    root = store.resolve()
    path = (root / Path(*rel.split("/"))).resolve()
    if path == root or root not in path.parents:
        raise AuthorityV6Error("artifact path escapes the authority store")
    return path


def _write_atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(raw)
    os.replace(part, path)


def _append_ledger(path: Path, record: Mapping) -> None:
    """Append-only JSONL. A failed retry never erases an earlier failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical(record) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def _next_funding_attempt(ledger: Path, artifact_path: str) -> int:
    """Allocate an attempt identity from durable ledger history, including no-byte failures."""
    attempts = []
    if ledger.exists():
        for line in ledger.read_bytes().splitlines():
            row = _load_strict(line, "acquisition ledger row")
            if (isinstance(row, dict) and row.get("artifact_path") == artifact_path
                    and type(row.get("attempt")) is int and row["attempt"] >= 0):
                attempts.append(row["attempt"])
    return max(attempts, default=-1) + 1


def _next_vision_attempt(ledger: Path, path: str) -> int:
    attempts = []
    if ledger.exists():
        for line in ledger.read_bytes().splitlines():
            row = _load_strict(line, "acquisition ledger row")
            if (isinstance(row, dict) and row.get("kind") == "VISION"
                    and row.get("path") == path and type(row.get("attempt")) is int
                    and row["attempt"] >= 0):
                attempts.append(row["attempt"])
    return max(attempts, default=-1) + 1


Fetch = Callable[[str], tuple[int, bytes, str]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # pragma: no cover - network only
        return None


def http_fetch(url: str) -> tuple[int, bytes, str]:  # pragma: no cover - network only
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=60) as response:
            return response.status, response.read(), response.geturl()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.geturl()


def _allowed_vision_coordinate(request: VisionRequest) -> bool:
    meta = M.archive_meta(request.path)
    parts = request.path.split("/")
    cadence = parts[1]
    # Filename parsing by metadata is authoritative; use suffix tests only for the
    # small frozen period sets (the symbol itself may contain hyphens only if Binance
    # ever allows them, which _vision_symbol currently refuses).
    if cadence == "monthly":
        allowed_period = request.path.endswith(("-2026-09.zip", "-2026-10.zip"))
    else:
        allowed_period = request.path.endswith(tuple(f"-{day}.zip" for day in (
            "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04")))
    allowed_series = (
        request.series == "klines" and request.interval == "1d"
        and (cadence == "monthly" or request.path.endswith("-2026-11-01.zip"))
    ) or (
        request.series in ("klines", "markPriceKlines") and request.interval == "1h"
    ) or (
        request.series == "fundingRate" and request.interval is None and cadence == "monthly"
    )
    return (allowed_period and allowed_series and meta["series"] == request.series
            and meta["interval"] == request.interval and meta["symbol"] == request.symbol
            and request.period_start_ms == meta["month_start_ms"]
            and request.period_end_ms == meta["month_end_ms"]
            and request.url == M.archive_url(request.path)
            and request.checksum_url == request.url + ".CHECKSUM")


def acquire_vision(requests: Sequence[VisionRequest], store: Path, ledger: Path,
                   fetch: Fetch = http_fetch, *, max_total_bytes: int,
                   phase: str, pit_v9: Mapping | None = None, root: Path = ROOT) -> dict:
    """Acquire exact allowlisted files, checksum first; 404 is explicit evidence."""
    if max_total_bytes <= 0:
        raise AuthorityV6Error("a positive acquisition byte cap is required")
    total = 0
    result = {"verified": [], "official_404": [], "checksum_failure": []}
    seen: set[str] = set()
    if phase == "seed":
        frozen_symbols = set(frozen_seed_symbols(root))
    elif phase == "intraday" and pit_v9 is not None:
        frozen_symbols = set(frozen_pit_union_symbols(pit_v9, root))
    else:
        raise AuthorityV6Error("Vision acquisition phase/PIT binding is invalid")
    for request in requests:
        if request.symbol not in frozen_symbols or not _allowed_vision_coordinate(request):
            raise AuthorityV6Error("Vision request is outside the frozen coordinate/symbol allowlist")
        if request.path in seen:
            raise AuthorityV6Error("duplicate Vision request")
        seen.add(request.path)
        attempt = _next_vision_attempt(ledger, request.path)
        base_record = {"kind": "VISION", "path": request.path, "attempt": attempt,
                       "url": request.url, "checksum_url": request.checksum_url}
        try:
            status, checksum_raw, final = fetch(request.checksum_url)
        except Exception as exc:
            _append_ledger(ledger, {**base_record, "decision": "CHECKSUM_FETCH_FAILED",
                                    "reason": type(exc).__name__})
            raise AuthorityV6Error("Vision checksum fetch failed") from exc
        if final != request.checksum_url:
            _append_ledger(ledger, {**base_record, "decision": "CHECKSUM_FAILURE", "reason": "redirect"})
            raise AuthorityV6Error("Vision checksum redirect refused")
        if status == 404:
            _append_ledger(ledger, {**base_record, "decision": "OFFICIAL_404"})
            result["official_404"].append(request.path)
            continue
        if status != 200:
            _append_ledger(ledger, {**base_record, "decision": "CHECKSUM_FAILURE",
                                    "reason": f"checksum_http_{status}"})
            raise AuthorityV6Error(f"Vision checksum HTTP {status}")
        target = _safe_store_path(store, request.path)
        checksum_target = Path(str(target) + ".CHECKSUM")
        _write_atomic(checksum_target, checksum_raw)
        checksum_raw_sha = sha256(checksum_raw)
        _append_ledger(ledger, {**base_record, "decision": "CHECKSUM_RAW_PRESERVED",
                                "checksum_path": request.path + ".CHECKSUM",
                                "checksum_raw_bytes": len(checksum_raw),
                                "checksum_raw_sha256": checksum_raw_sha})
        try:
            expected = parse_official_checksum(checksum_raw, Path(request.path).name)
        except AuthorityV6Error as exc:
            _append_ledger(ledger, {**base_record, "decision": "CHECKSUM_FAILURE",
                                    "reason": "malformed_checksum"})
            result["checksum_failure"].append(request.path)
            raise
        try:
            status, raw, final = fetch(request.url)
        except Exception as exc:
            _append_ledger(ledger, {**base_record, "decision": "ARCHIVE_FETCH_FAILED",
                                    "reason": type(exc).__name__,
                                    "expected_sha256": expected})
            raise AuthorityV6Error("Vision archive fetch failed") from exc
        actual = sha256(raw)
        if final != request.url or status != 200 or actual != expected:
            reason = "redirect" if final != request.url else (
                f"archive_http_{status}" if status != 200 else "sha256_mismatch")
            _append_ledger(ledger, {**base_record, "decision": "CHECKSUM_FAILURE", "reason": reason,
                                    "expected_sha256": expected, "observed_sha256": actual})
            result["checksum_failure"].append(request.path)
            raise AuthorityV6Error(f"Vision archive refused: {reason}")
        total += len(raw)
        if total > max_total_bytes:
            _append_ledger(ledger, {**base_record, "decision": "CHECKSUM_FAILURE",
                                    "reason": "byte_cap_exceeded"})
            raise AuthorityV6Error("Vision acquisition byte cap exceeded")
        _write_atomic(target, raw)
        _write_atomic(Path(str(target) + ".ok"), (expected + "\n").encode("ascii"))
        _append_ledger(ledger, {**base_record, "decision": "PRESENT_CHECKSUM_VERIFIED",
                                "raw_bytes": len(raw), "raw_sha256": actual,
                                "checksum_raw_sha256": checksum_raw_sha})
        result["verified"].append(request.path)
    result["bytes"] = total
    for key in ("verified", "official_404", "checksum_failure"):
        result[key].sort()
    return result


def funding_url(symbol: str, start_ms: int) -> str:
    if not SYMBOL.fullmatch(symbol) or not API_START_MS <= start_ms <= API_END_MS:
        raise AuthorityV6Error("funding request is outside the frozen symbol/time allowlist")
    query = urllib.parse.urlencode((
        ("symbol", symbol), ("startTime", start_ms), ("endTime", API_END_MS), ("limit", 1000)))
    return FUNDING_ENDPOINT + "?" + query


def parse_funding_page(raw: bytes, symbol: str, start_ms: int) -> list[dict]:
    rows = _load_strict(raw, "funding response")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise AuthorityV6Error("funding response must be an array of at most 1000 rows")
    times = []
    for row in rows:
        if (not isinstance(row, dict) or row.get("symbol") != symbol
                or type(row.get("fundingTime")) is not int
                or not isinstance(row.get("fundingRate"), str)):
            raise AuthorityV6Error("malformed or cross-symbol funding row")
        try:
            rate = Decimal(row["fundingRate"])
        except Exception as exc:
            raise AuthorityV6Error("malformed funding rate") from exc
        if not rate.is_finite() or not start_ms <= row["fundingTime"] <= API_END_MS:
            raise AuthorityV6Error("funding row crosses request/global cutoff bounds")
        times.append(row["fundingTime"])
    if times != sorted(set(times)):
        raise AuthorityV6Error("funding page timestamps are duplicate or non-monotonic")
    return rows


def capture_funding_residual(symbols: Sequence[str], store: Path, ledger: Path,
                             fetch: Fetch = http_fetch, *, max_total_bytes: int,
                             pit_v9: Mapping, root: Path = ROOT,
                             accessed_cairo: str = "1970-01-01T00:00:00+02:00") -> dict:
    """Capture raw Nov 1..4 pages. Empty is preserved and marked incomplete."""
    if max_total_bytes <= 0:
        raise AuthorityV6Error("a positive API byte cap is required")
    frozen_symbols = set(frozen_pit_union_symbols(pit_v9, root))
    requested_symbols = set(symbols)
    if not requested_symbols or not requested_symbols <= frozen_symbols:
        raise AuthorityV6Error("funding symbols are not a subset of the exact PIT-v9 union")
    total = 0
    completed, incomplete = [], []
    for symbol in sorted(set(symbols)):
        if not SYMBOL.fullmatch(symbol):
            raise AuthorityV6Error("invalid residual funding symbol")
        start, page, previous = API_START_MS, 0, None
        while True:
            if page >= 16:
                raise AuthorityV6Error("funding pagination exceeded frozen page cap")
            url = funding_url(symbol, start)
            record = {"kind": "FUNDING_API", "symbol": symbol, "page": page,
                      "url": url, "requested_start_ms": start, "requested_end_ms": API_END_MS}
            rel = (f"um/api/fundingRate/{symbol}/"
                   f"{symbol}-fundingRate-2026-11-01-2026-11-04-p{page:03d}.json")
            target = _safe_store_path(store, rel)
            attempt = _next_funding_attempt(ledger, rel)
            attempt_rel = rel + f".attempt-{attempt:03d}.raw"
            attempt_target = _safe_store_path(store, attempt_rel)
            if attempt_target.exists():
                raise AuthorityV6Error("funding attempt allocation collided with preserved bytes")
            attempt_record = {**record, "path": attempt_rel, "artifact_path": rel,
                              "attempt": attempt}
            try:
                status, raw, final = fetch(url)
            except Exception as exc:
                _append_ledger(ledger, {**attempt_record, "decision": "FETCH_FAILED",
                                        "reason": type(exc).__name__})
                raise AuthorityV6Error("bounded funding fetch failed") from exc
            _write_atomic(attempt_target, raw)  # append-only preservation precedes parsing
            raw_digest = sha256(raw)
            _append_ledger(ledger, {**attempt_record, "decision": "RAW_RESPONSE_PRESERVED",
                                    "http_status": status, "final_url": final,
                                    "raw_bytes": len(raw), "raw_sha256": raw_digest})
            if final != url or status != 200:
                _append_ledger(ledger, {**attempt_record, "decision": "TRANSPORT_REFUSED",
                                        "reason": "redirect" if final != url else f"http_{status}"})
                raise AuthorityV6Error("bounded funding request redirected or failed")
            total += len(raw)
            if total > max_total_bytes:
                _append_ledger(ledger, {**attempt_record, "decision": "CUTOFF_REFUSED",
                                        "reason": "byte_cap_exceeded"})
                raise AuthorityV6Error("funding acquisition byte cap exceeded")
            try:
                rows = parse_funding_page(raw, symbol, start)
            except AuthorityV6Error as exc:
                _append_ledger(ledger, {**attempt_record, "decision": "PARSER_SCHEMA_CUTOFF_REFUSED",
                                        "reason": str(exc), "raw_sha256": raw_digest})
                raise
            _append_ledger(ledger, {**attempt_record, "decision": "PARSER_SCHEMA_CUTOFF_ACCEPTED",
                                    "raw_sha256": raw_digest, "rows": len(rows)})
            times = [row["fundingTime"] for row in rows]
            if previous is not None and times and times[0] <= previous:
                _append_ledger(ledger, {**attempt_record, "decision": "PARSER_SCHEMA_CUTOFF_REFUSED",
                                        "reason": "cross_page_duplicate"})
                raise AuthorityV6Error("funding pages overlap or regress")
            digest = raw_digest
            provenance = {
                "format": "zb-bounded-funding-response/2", "url": url,
                "params": {"symbol": symbol, "startTime": start,
                           "endTime": API_END_MS, "limit": 1000},
                "response_sha256": digest, "raw_bytes": len(raw), "rows": len(rows),
                "first_funding_time_ms": times[0] if times else None,
                "last_funding_time_ms": times[-1] if times else None,
                "terminal_page": len(rows) < 1000, "accessed_cairo": accessed_cairo,
            }
            if target.exists() and target.read_bytes() != raw:
                _append_ledger(ledger, {**attempt_record, "decision": "CANONICAL_WRITE_REFUSED",
                                        "reason": "differing_existing_bytes"})
                raise AuthorityV6Error("funding canonical page already exists with different bytes")
            if not target.exists():
                _write_atomic(target, raw)
            _write_atomic(Path(str(target) + ".ok"), (digest + "\n").encode("ascii"))
            _write_atomic(Path(str(target) + ".provenance.json"), canonical(provenance))
            decision = "INCOMPLETE_EMPTY" if not rows else "PRESERVED"
            _append_ledger(ledger, {**record, "path": rel, "raw_attempt_path": attempt_rel,
                                    "decision": decision,
                                    "raw_bytes": len(raw), "raw_sha256": digest, "rows": len(rows)})
            if not rows:
                incomplete.append(symbol)
                break
            previous = times[-1]
            if len(rows) < 1000:
                completed.append(symbol)
                break
            start = previous + 1
            if start > API_END_MS:
                completed.append(symbol)
                break
            page += 1
    return {"completed": sorted(completed), "incomplete": sorted(incomplete), "bytes": total}


def _record_from_fixture(path: str, *, series: str, interval: str | None, symbol: str,
                         times: Sequence[int], raw: bytes = b"fixture") -> dict:
    """Fixture-only helper used by tests; not admitted by production build."""
    return {"path": path, "series": series, "interval": interval, "symbol": symbol,
            "declared_start_ms": min(times) if times else 0,
            "declared_end_ms": (max(times) + (DAY_MS if interval == "1d" else HOUR_MS)) if times else 0,
            "observed_first_ms": min(times) if times else None,
            "observed_last_ms": max(times) if times else None,
            "rows": len(times), "raw_bytes": len(raw), "raw_sha256": sha256(raw),
            "parsed_content_digest": sha256(canonical(list(times))),
            "source": {"kind": "fixture", "url": None}, "times": list(times)}


def _vision_acquisition_link(ledger: Path, path: str,
                             raw_sha256: str | None) -> tuple[dict | None, str | None]:
    if not ledger.exists():
        return None, None
    matches = []
    for line in ledger.read_bytes().splitlines():
        row = _load_strict(line, "acquisition ledger row")
        if (isinstance(row, dict) and row.get("decision") == "PRESENT_CHECKSUM_VERIFIED"
                and row.get("kind") == "VISION" and row.get("path") == path
                and type(row.get("attempt")) is int and row["attempt"] >= 0
                and row.get("url") == M.archive_url(path)
                and row.get("checksum_url") == M.archive_url(path) + ".CHECKSUM"
                and (raw_sha256 is None or row.get("raw_sha256") == raw_sha256)):
            matches.append((row, sha256(canonical(row))))
    return matches[-1] if matches else (None, None)


def _latest_vision_acquisition(ledger: Path, path: str) -> tuple[dict | None, str | None]:
    """Latest admitted attempt for failure attribution even if stored bytes were corrupted."""
    if not ledger.exists():
        return None, None
    matches = []
    for line in ledger.read_bytes().splitlines():
        row = _load_strict(line, "acquisition ledger row")
        if (isinstance(row, dict) and row.get("decision") == "PRESENT_CHECKSUM_VERIFIED"
                and row.get("kind") == "VISION" and row.get("path") == path
                and type(row.get("attempt")) is int and row["attempt"] >= 0
                and row.get("url") == M.archive_url(path)
                and row.get("checksum_url") == M.archive_url(path) + ".CHECKSUM"):
            matches.append((row, sha256(canonical(row))))
    return matches[-1] if matches else (None, None)


def scan_v6_record(store: Path, path: str, ledger: Path) -> dict:
    """Re-verify and parse one already-acquired allowlisted artifact.

    This function is intentionally separate from acquisition: no response becomes
    authority merely because it was downloaded.  The checksum/provenance sidecars are
    re-read, schemas are validated, and declared/observed bounds are checked before a
    manifest record is returned.
    """
    target = _safe_store_path(store, path)
    is_vision = path.endswith(".zip")
    try:
        raw_sha = sha256(target.read_bytes())
    except OSError:
        raw_sha = None
    try:
        checksum_raw_sha = sha256(Path(str(target) + ".CHECKSUM").read_bytes())
    except OSError:
        checksum_raw_sha = None
    acquisition_row, acquisition_link = (_vision_acquisition_link(ledger, path, raw_sha)
                                         if is_vision else (None, None))
    try:
        record = _scan_v6_record_unledgered(store, path)
    except Exception as exc:
        if is_vision:
            failure_row, failure_link = ((acquisition_row, acquisition_link)
                                         if acquisition_row is not None
                                         else _latest_vision_acquisition(ledger, path))
            _append_ledger(ledger, {
                "kind": "VISION", "path": path,
                "decision": "VISION_PARSER_SCHEMA_CUTOFF_REFUSED",
                "raw_sha256": raw_sha, "observed_raw_sha256": raw_sha,
                "expected_raw_sha256": failure_row.get("raw_sha256") if failure_row else None,
                "checksum_raw_sha256": checksum_raw_sha,
                "acquisition_record_sha256": failure_link,
                "attempt": failure_row.get("attempt") if failure_row else None,
                "acquisition_attempt": failure_row.get("attempt") if failure_row else None,
                "reason": type(exc).__name__, "detail": str(exc),
            })
        raise
    if is_vision:
        if acquisition_link is None:
            _append_ledger(ledger, {
                "kind": "VISION", "path": path,
                "decision": "VISION_PARSER_SCHEMA_CUTOFF_REFUSED",
                "raw_sha256": record["raw_sha256"],
                "checksum_raw_sha256": record["source"]["checksum_raw_sha256"],
                "acquisition_record_sha256": None,
                "attempt": None, "acquisition_attempt": None,
                "reason": "missing_acquisition_link",
            })
            raise AuthorityV6Error("Vision scan lacks exact acquisition-ledger link")
        _append_ledger(ledger, {
            "kind": "VISION", "path": path,
            "decision": "VISION_PARSER_SCHEMA_CUTOFF_ACCEPTED",
            "raw_sha256": record["raw_sha256"],
            "checksum_raw_sha256": record["source"]["checksum_raw_sha256"],
            "acquisition_record_sha256": acquisition_link,
            "attempt": acquisition_row["attempt"],
            "acquisition_attempt": acquisition_row["attempt"],
            "source_url": acquisition_row["url"],
            "checksum_url": acquisition_row["checksum_url"],
            "raw_bytes": record["raw_bytes"],
            "parsed_content_digest": record["parsed_content_digest"],
            "rows": record["rows"],
            "observed_first_ms": record["observed_first_ms"],
            "observed_last_ms": record["observed_last_ms"],
            "declared_start_ms": record["declared_start_ms"],
            "declared_end_ms": record["declared_end_ms"],
        })
    return record


def _scan_v6_record_unledgered(store: Path, path: str) -> dict:
    target = _safe_store_path(store, path)
    meta = M.archive_meta(path)
    declared_start = meta["month_start_ms"]
    declared_end = meta["month_end_ms"]
    if declared_end > CUTOFF_MS:
        raise AuthorityV6Error("artifact declared period crosses the exclusive cutoff")
    if path.endswith(".zip"):
        try:
            checksum_raw = Path(str(target) + ".CHECKSUM").read_bytes()
        except OSError as exc:
            raise AuthorityV6Error("official CHECKSUM raw bytes are missing") from exc
        published_from_raw = parse_official_checksum(checksum_raw, target.name)
        raw, published, csv_raw = M.read_archive(str(target), path)
        if published != published_from_raw:
            raise AuthorityV6Error("derived .ok does not match preserved official CHECKSUM")
        rows = M.parse_archive_csv(path, csv_raw, meta)
        source = {"kind": "binance-vision-archive", "url": M.archive_url(path),
                  "archive_checksum": published,
                  "checksum_path": path + ".CHECKSUM",
                  "checksum_raw_sha256": sha256(checksum_raw)}
        normalized = _semantic_rows(meta, rows)
    elif "/api/fundingRate/" in path and path.endswith(".json"):
        raw = target.read_bytes()
        sidecar = Path(str(target) + ".ok").read_text(encoding="ascii").strip()
        provenance_raw = Path(str(target) + ".provenance.json").read_bytes()
        provenance = _load_strict(provenance_raw, "funding provenance")
        actual = sha256(raw)
        if sidecar != actual or provenance.get("response_sha256") != actual:
            raise AuthorityV6Error("funding response checksum/provenance mismatch")
        query = parse_qs_strict(provenance.get("url", ""))
        try:
            provenance_bounds_ok = (int(query.get("endTime", -1)) == API_END_MS
                                    and API_START_MS <= int(query.get("startTime", -1)) <= API_END_MS)
        except (TypeError, ValueError):
            provenance_bounds_ok = False
        if (urlsplit_identity(provenance.get("url")) != FUNDING_ENDPOINT
                or set(query) != {"symbol", "startTime", "endTime", "limit"}
                or query.get("symbol") != meta["symbol"] or query.get("limit") != "1000"
                or not provenance_bounds_ok):
            raise AuthorityV6Error("funding provenance URL is not exactly bounded")
        start_ms = int(query["startTime"])
        rows = parse_funding_page(raw, meta["symbol"], start_ms)
        times_check = [row["fundingTime"] for row in rows]
        params = provenance.get("params")
        expected_params = {"symbol": meta["symbol"], "startTime": start_ms,
                           "endTime": API_END_MS, "limit": 1000}
        if (provenance.get("format") != "zb-bounded-funding-response/2"
                or params != expected_params or provenance.get("raw_bytes") != len(raw)
                or provenance.get("rows") != len(rows)
                or provenance.get("first_funding_time_ms") != (times_check[0] if times_check else None)
                or provenance.get("last_funding_time_ms") != (times_check[-1] if times_check else None)
                or provenance.get("terminal_page") != (len(rows) < 1000)):
            raise AuthorityV6Error("funding provenance count/pagination binding mismatch")
        source = {"kind": M.FUNDING_API_KIND, "url": provenance["url"],
                  "archive_checksum": actual, "provenance_path": path + ".provenance.json",
                  "provenance_sha256": sha256(provenance_raw)}
        normalized = _semantic_rows(meta, rows)
    else:
        raise AuthorityV6Error("path is not an allowlisted authority-v6 artifact type")
    times = [int(row[0]) if isinstance(row, list) else row["fundingTime"] for row in rows]
    if any(time_ms >= CUTOFF_MS for time_ms in times):
        raise AuthorityV6Error("artifact contains a row at/after the exclusive cutoff")
    row_digests = {str(time_ms): sha256(canonical(row))
                   for time_ms, row in zip(times, normalized)}
    if len(row_digests) != len(times):
        raise AuthorityV6Error("artifact contains duplicate timestamps")
    record = {
        "path": path, "series": meta["series"], "interval": meta["interval"],
        "symbol": meta["symbol"], "declared_start_ms": declared_start,
        "declared_end_ms": declared_end,
        "observed_first_ms": times[0] if times else None,
        "observed_last_ms": times[-1] if times else None,
        "rows": len(rows), "raw_bytes": len(raw), "raw_sha256": sha256(raw),
        "parsed_content_digest": sha256(canonical(normalized)),
        "row_digests": row_digests, "source": source, "times": times,
    }
    if isinstance(source, dict) and source.get("kind") == M.FUNDING_API_KIND:
        query = parse_qs_strict(source["url"])
        record["requested_start_ms"] = int(query["startTime"])
        record["requested_end_ms"] = int(query["endTime"])
    return record


def _semantic_rows(meta: Mapping, rows: Sequence) -> list[object]:
    """Source-neutral row identity used for archive/API conflict checks."""
    normalized = []
    if meta["series"] == "fundingRate":
        for row in rows:
            if isinstance(row, Mapping):
                time_ms, rate = row["fundingTime"], row["fundingRate"]
            else:
                time_ms = int(row[0])
                rate = row[2].decode("ascii") if isinstance(row[2], bytes) else str(row[2])
            normalized.append({"fundingTime": int(time_ms),
                               "fundingRate": format(Decimal(rate), "f")})
    else:
        for row in rows:
            values = [field.decode("ascii") if isinstance(field, bytes) else str(field)
                      for field in row]
            normalized.append(values)
    return normalized


def scan_base_row_evidence(store: Path, record: Mapping) -> dict:
    """Re-read one exact base-v5 source and return timestamp/content evidence."""
    path = record["path"]
    target = _safe_store_path(store, path)
    source = record["source"]
    if source["kind"] == "binance-vision-archive":
        raw, published, csv_raw = M.read_archive(str(target), path)
        if published != record["sha256"]:
            raise AuthorityV6Error("base archive checksum differs from manifest-v5")
        meta = M.archive_meta(path)
        rows = M.parse_archive_csv(path, csv_raw, meta)
    elif source["kind"] == M.FUNDING_API_KIND:
        raw, rows = M.read_funding_api(str(target), path, source)
        meta = M.archive_meta(path)
    elif source["kind"] == M.KLINE_API_KIND:
        raw, rows = M.read_kline_api(str(target), path, source)
        meta = M.archive_meta(path)
    else:
        raise AuthorityV6Error("unsupported base-v5 source for row evidence")
    if sha256(raw) != record["sha256"]:
        raise AuthorityV6Error("base-v5 source bytes differ from exact manifest")
    normalized = _semantic_rows(meta, rows)
    times = [int(row[0]) if not isinstance(row, Mapping) else row["fundingTime"] for row in rows]
    digests = {str(time_ms): sha256(canonical(row))
               for time_ms, row in zip(times, normalized)}
    if len(digests) != len(times):
        raise AuthorityV6Error("base-v5 source has duplicate timestamps")
    return {"source_sha256": record["sha256"], "row_digests": digests}


def parse_qs_strict(url: str) -> dict[str, str]:
    try:
        parsed = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise AuthorityV6Error("malformed source query") from exc
    if len(pairs) != len({key for key, _ in pairs}):
        raise AuthorityV6Error("duplicate source query key")
    return dict(pairs)


def urlsplit_identity(url: object) -> str:
    if not isinstance(url, str):
        return ""
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def build_manifest_v6_from_store(base_path: Path, paths: Sequence[str], store: Path,
                                 acquisition_ledger: Path, pit_v9: Mapping) -> dict:
    base_raw = base_path.read_bytes()
    records = [scan_v6_record(store, path, acquisition_ledger) for path in sorted(set(paths))]
    base = _load_strict(gzip.decompress(base_raw), "base manifest-v5")
    evidence = {}
    for old in base.get("files", ()):
        for new in records:
            if ((old.get("series"), old.get("interval"), old.get("symbol")) ==
                    (new.get("series"), new.get("interval"), new.get("symbol"))
                    and old.get("first_open_ms") is not None
                    and new.get("observed_first_ms") is not None
                    and not (old["last_open_ms"] < new["observed_first_ms"]
                             or new["observed_last_ms"] < old["first_open_ms"])):
                evidence[old["path"]] = scan_base_row_evidence(store, old)
                break
    return build_manifest_v6(base_raw, records, acquisition_ledger.read_bytes(), pit_v9,
                             verified_base_rows=evidence)


def write_json_immutable(value: Mapping, path: Path, *, gzip_output: bool = False) -> str:
    """Canonical write-once output; gzip is reproducible (mtime zero, no filename)."""
    raw = canonical(value)
    if gzip_output:
        buffer = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buffer,
                           compresslevel=9, mtime=0) as handle:
            handle.write(raw)
        raw = buffer.getvalue()
    if path.exists():
        if path.read_bytes() != raw:
            raise AuthorityV6Error(f"immutable output differs: {path}")
        return sha256(raw)
    _write_atomic(path, raw)
    return sha256(raw)


def _validate_ledger_raw(raw: bytes) -> tuple[str, list[dict]]:
    if not raw or not raw.endswith(b"\n"):
        raise AuthorityV6Error("acquisition ledger must be nonempty newline-terminated JSONL")
    rows = []
    for line in raw.splitlines():
        row = _load_strict(line, "acquisition ledger row")
        if not isinstance(row, dict) or not isinstance(row.get("decision"), str):
            raise AuthorityV6Error("acquisition ledger row is malformed")
        rows.append(row)
    current_attempt: dict[str, int] = {}
    present_by_hash: dict[str, tuple[int, dict]] = {}
    present_keys: set[tuple[str, int]] = set()
    for index, row in enumerate(rows):
        if row.get("kind") != "VISION":
            continue
        path, attempt = row.get("path"), row.get("attempt")
        if not isinstance(path, str) or type(attempt) is not int or attempt < 0:
            raise AuthorityV6Error("Vision ledger attempt identity is malformed")
        prior = current_attempt.get(path)
        if prior is None:
            if attempt != 0:
                raise AuthorityV6Error("Vision attempt history must start at zero")
            current_attempt[path] = 0
        elif attempt == prior + 1:
            current_attempt[path] = attempt
        elif attempt != prior:
            raise AuthorityV6Error("Vision attempt history has a gap or regression")
        decision = row["decision"]
        if decision == "PRESENT_CHECKSUM_VERIFIED":
            key = (path, attempt)
            if key in present_keys:
                raise AuthorityV6Error("Vision attempt has duplicate admitted acquisition rows")
            present_keys.add(key)
            present_by_hash[sha256(canonical(row))] = (index, row)
        if decision in ("VISION_PARSER_SCHEMA_CUTOFF_ACCEPTED",
                        "VISION_PARSER_SCHEMA_CUTOFF_REFUSED"):
            linked = present_by_hash.get(row.get("acquisition_record_sha256"))
            if (linked is None or linked[0] >= index or linked[1].get("path") != path
                    or linked[1].get("attempt") != attempt
                    or row.get("acquisition_attempt") != attempt):
                raise AuthorityV6Error("Vision scan verdict is not linked to its prior acquisition attempt")
    return sha256(raw), rows


def _validate_new_record_source(record: Mapping) -> None:
    source = record.get("source")
    if not isinstance(source, Mapping):
        raise AuthorityV6Error("new manifest record source is missing")
    kind = source.get("kind")
    path = record.get("path")
    if kind == "binance-vision-archive":
        if (not isinstance(path, str) or not path.endswith(".zip")
                or source.get("url") != M.archive_url(path)
                or source.get("checksum_path") != path + ".CHECKSUM"
                or not HEX64.fullmatch(source.get("checksum_raw_sha256", ""))
                or source.get("archive_checksum") != record.get("raw_sha256")):
            raise AuthorityV6Error("Vision manifest source is not URL/checksum-byte bound")
        meta = M.archive_meta(path)
        req = VisionRequest(path, source["url"], source["url"] + ".CHECKSUM",
                            meta["series"], meta["interval"], meta["symbol"],
                            meta["month_start_ms"], meta["month_end_ms"])
        if not _allowed_vision_coordinate(req):
            raise AuthorityV6Error("Vision manifest source is outside the frozen coordinates")
    elif kind == M.FUNDING_API_KIND:
        if not isinstance(path, str) or "/api/fundingRate/" not in path:
            raise AuthorityV6Error("funding API manifest path is invalid")
        query = parse_qs_strict(source.get("url", ""))
        try:
            start_ms, end_ms = int(query.get("startTime", -1)), int(query.get("endTime", -1))
        except (TypeError, ValueError):
            start_ms = end_ms = -1
        if (urlsplit_identity(source.get("url")) != FUNDING_ENDPOINT
                or set(query) != {"symbol", "startTime", "endTime", "limit"}
                or query.get("symbol") != record.get("symbol") or query.get("limit") != "1000"
                or end_ms != API_END_MS or not API_START_MS <= start_ms <= API_END_MS
                or source.get("archive_checksum") != record.get("raw_sha256")
                or not HEX64.fullmatch(source.get("provenance_sha256", ""))):
            raise AuthorityV6Error("funding API manifest source is not exactly bounded")
    else:
        raise AuthorityV6Error("fixture/unallowlisted manifest source is forbidden")


def build_manifest_v6(base_manifest_raw: bytes, new_records: Sequence[Mapping],
                      acquisition_ledger_raw: bytes, pit_v9: Mapping, *,
                      verified_base_rows: Mapping[str, Mapping] | None = None) -> dict:
    """Bind verified records; reject an entire cross-cutoff or conflicting artifact.

    Base-v5 file dictionaries are copied without mutation.  New v6 records carry the
    expanded provenance/bounds fields. Duplicate new timestamps are refused even when
    identical; base supersession requires a separately verified row-identical proof.
    """
    plan = load_frozen_plan()
    expected_base = plan["base_authorities"]["manifest_v5"]
    if sha256(base_manifest_raw) != expected_base["byte_sha256"]:
        raise AuthorityV6Error("base manifest-v5 bytes changed")
    try:
        decoded = gzip.decompress(base_manifest_raw)
    except OSError as exc:
        raise AuthorityV6Error("base manifest-v5 gzip is malformed") from exc
    base_manifest = _load_strict(decoded, "base manifest-v5")
    if not isinstance(base_manifest, dict):
        raise AuthorityV6Error("base manifest-v5 must be an object")
    try:
        M.validate(base_manifest)
    except M.ManifestError as exc:
        raise AuthorityV6Error(f"base manifest-v5 source/schema invalid: {exc}") from exc
    if (base_manifest.get("digest") != expected_base["content_digest"]
            or base_manifest.get("digest") != M.digest_of(dict(base_manifest))):
        raise AuthorityV6Error("base manifest-v5 identity/content changed")
    acquisition_ledger_sha256, ledger_rows = _validate_ledger_raw(acquisition_ledger_raw)
    seed_allowlist = set(frozen_seed_symbols())
    pit_allowlist = set(frozen_pit_union_symbols(pit_v9))
    paths = {row.get("path") for row in base_manifest.get("files", ())}
    accepted = []
    occupied: dict[tuple, tuple[str, str]] = {}
    for record in sorted((copy.deepcopy(dict(row)) for row in new_records),
                         key=lambda row: (row.get("series", ""), row.get("interval") or "",
                                          row.get("symbol", ""), row.get("declared_start_ms", -1),
                                          row.get("path", ""))):
        path = record.get("path")
        if path in paths or not isinstance(path, str):
            raise AuthorityV6Error("manifest path is duplicate or invalid")
        paths.add(path)
        _validate_new_record_source(record)
        allowed = seed_allowlist if record.get("interval") == "1d" else pit_allowlist
        if record.get("symbol") not in allowed:
            raise AuthorityV6Error("manifest record symbol is outside exact frozen authority allowlist")
        source_kind = record["source"]["kind"]
        if source_kind == "binance-vision-archive":
            ok = False
            for scan_index, scan in enumerate(ledger_rows):
                if (scan.get("decision") != "VISION_PARSER_SCHEMA_CUTOFF_ACCEPTED"
                        or scan.get("path") != path):
                    continue
                link = scan.get("acquisition_record_sha256")
                linked = [(index, row) for index, row in enumerate(ledger_rows[:scan_index])
                          if sha256(canonical(row)) == link]
                if len(linked) != 1:
                    continue
                _, acquired = linked[0]
                source = record["source"]
                if (acquired.get("decision") == "PRESENT_CHECKSUM_VERIFIED"
                        and acquired.get("kind") == "VISION"
                        and acquired.get("path") == path
                        and type(acquired.get("attempt")) is int
                        and acquired.get("attempt") == scan.get("acquisition_attempt")
                        and acquired.get("url") == source.get("url")
                        and acquired.get("checksum_url") == source.get("url") + ".CHECKSUM"
                        and scan.get("source_url") == source.get("url")
                        and scan.get("checksum_url") == source.get("url") + ".CHECKSUM"
                        and acquired.get("raw_sha256") == scan.get("raw_sha256")
                        == record.get("raw_sha256")
                        and acquired.get("raw_bytes") == scan.get("raw_bytes")
                        == record.get("raw_bytes")
                        and acquired.get("checksum_raw_sha256")
                        == scan.get("checksum_raw_sha256")
                        == source.get("checksum_raw_sha256")
                        and scan.get("parsed_content_digest") == record.get("parsed_content_digest")
                        and scan.get("rows") == record.get("rows")
                        and scan.get("observed_first_ms") == record.get("observed_first_ms")
                        and scan.get("observed_last_ms") == record.get("observed_last_ms")
                        and scan.get("declared_start_ms") == record.get("declared_start_ms")
                        and scan.get("declared_end_ms") == record.get("declared_end_ms")):
                    ok = True
                    break
        else:
            evidence = [row for row in ledger_rows
                        if row.get("decision") in ("PRESERVED", "INCOMPLETE_EMPTY")
                        and row.get("path") == path]
            ok = any(row.get("raw_sha256") == record.get("raw_sha256") for row in evidence)
        if not ok:
            raise AuthorityV6Error("manifest record lacks matching acquisition-ledger byte evidence")
        declared_end = record.get("declared_end_ms")
        first, last = record.get("observed_first_ms"), record.get("observed_last_ms")
        if (type(declared_end) is not int or declared_end > CUTOFF_MS
                or (first is not None and (type(first) is not int or first >= CUTOFF_MS))
                or (last is not None and (type(last) is not int or last >= CUTOFF_MS))):
            raise AuthorityV6Error("cross-cutoff artifact is rejected in full")
        if not HEX64.fullmatch(record.get("raw_sha256", "")) or not HEX64.fullmatch(
                record.get("parsed_content_digest", "")):
            raise AuthorityV6Error("new manifest record lacks byte/content binding")
        times = record.pop("times", None)
        if not isinstance(times, list) or times != sorted(set(times)):
            raise AuthorityV6Error("record times are absent, duplicate or non-monotonic")
        declared_start = record.get("declared_start_ms")
        if (type(declared_start) is not int
                or any(not declared_start <= time_ms < declared_end for time_ms in times)
                or record.get("rows") != len(times)
                or record.get("observed_first_ms") != (times[0] if times else None)
                or record.get("observed_last_ms") != (times[-1] if times else None)):
            raise AuthorityV6Error("record rows/timestamps escape declared bounds or counts")
        row_digests = record.get("row_digests")
        if not isinstance(row_digests, dict) or set(row_digests) != {str(t) for t in times}:
            raise AuthorityV6Error("record row digests do not bind every timestamp")
        for time_ms in times:
            key = (record["series"], record["interval"], record["symbol"], time_ms)
            if key in occupied:
                raise AuthorityV6Error("duplicate archive/API authority timestamp")
            occupied[key] = (row_digests[str(time_ms)], path)
        accepted.append(record)
    # A base range may become inactive only after exact source reread proves that
    # every old timestamp and row is present, identical, in admitted Vision rows.
    # Declared archive bounds are never evidence of observed row coverage.
    superseded_base = []
    verified_base_rows = verified_base_rows or {}
    for old in base_manifest["files"]:
        overlapping = [new for new in accepted
                       if ((old["series"], old["interval"], old["symbol"]) ==
                           (new["series"], new["interval"], new["symbol"])
                           and old["first_open_ms"] is not None
                           and new["observed_first_ms"] is not None
                           and not (old["last_open_ms"] < new["observed_first_ms"]
                                    or new["observed_last_ms"] < old["first_open_ms"]))]
        if not overlapping:
            continue
        if any(new["source"]["kind"] != "binance-vision-archive" for new in overlapping):
            raise AuthorityV6Error("base/new authority overlap is not a Vision replacement")
        proof = verified_base_rows.get(old["path"])
        if (not isinstance(proof, Mapping)
                or proof.get("source_sha256") != old["sha256"]
                or not isinstance(proof.get("row_digests"), Mapping)):
            raise AuthorityV6Error("base supersession lacks exact source-row evidence")
        old_rows = proof["row_digests"]
        if (len(old_rows) != old["rows"]
                or any(not isinstance(key, str) or not key.isdigit()
                       or not HEX64.fullmatch(value) for key, value in old_rows.items())):
            raise AuthorityV6Error("base supersession row evidence is malformed or incomplete")
        old_times = sorted(int(key) for key in old_rows)
        if (not old_times or old_times[0] != old["first_open_ms"]
                or old_times[-1] != old["last_open_ms"]):
            raise AuthorityV6Error("base supersession evidence bounds differ from manifest-v5")
        if old["interval"] is not None:
            step = M.INTERVALS[old["interval"]]
            expected = list(range(old["first_open_ms"], old["last_open_ms"] + step, step))
            if old_times != expected:
                raise AuthorityV6Error("base supersession evidence has a missing interior grid row")
        replacement = {}
        for new in overlapping:
            for key, digest in new["row_digests"].items():
                time_ms = int(key)
                if old["first_open_ms"] <= time_ms <= old["last_open_ms"]:
                    replacement[key] = digest
        missing = [key for key in old_rows if key not in replacement]
        if missing:
            raise AuthorityV6Error("Vision replacement does not prove every exact base timestamp")
        conflicts = [key for key, digest in old_rows.items()
                     if replacement.get(key) != digest]
        if conflicts:
            raise AuthorityV6Error("Vision replacement conflicts with exact base row content")
        superseded_base.append(old["path"])
    active_base = [copy.deepcopy(row) for row in base_manifest["files"]
                   if row["path"] not in set(superseded_base)]
    manifest = {
        "format": "zb-binance-um-archive-manifest/6",
        "authority_id": "BINANCE-UM-PRECUTOFF-AUTHORITY-V6",
        "cutoff_exclusive_ms": CUTOFF_MS,
        "base_manifest": {"byte_sha256": sha256(base_manifest_raw),
                          "content_digest": base_manifest["digest"]},
        "acquisition_ledger_sha256": acquisition_ledger_sha256,
        "precedence": "checksum-verified Vision archive; bounded Nov funding API only when archive absent",
        "base_files_v5": copy.deepcopy(base_manifest["files"]),
        "superseded_base_paths": sorted(superseded_base),
        "active_base_files_v5": active_base,
        "files_v6": accepted,
    }
    manifest["digest"] = content_digest(manifest)
    return manifest


def _pit_semantic(value: Mapping) -> dict:
    result = copy.deepcopy(dict(value))
    result.pop("digest", None)
    result.pop("manifest_digest", None)
    return result


def build_pit_v9_first_pass(base_pit: Mapping, daily: Mapping[str, Mapping[int, Decimal]],
                            classes: Mapping, *, provisional_manifest_digest: str,
                            root: Path = ROOT) -> dict:
    plan = load_frozen_plan(root / PLAN_PATH.relative_to(ROOT))
    exact_pit, _ = _load_bound_json(root, plan["base_authorities"]["pit_v8"], "PIT-v8")
    exact_classes, _ = _load_bound_json(
        root, plan["base_authorities"]["instrument_classes_v1"], "instrument classes")
    if dict(base_pit) != exact_pit or dict(classes) != exact_classes:
        raise AuthorityV6Error("PIT-v9 inputs differ from exact frozen PIT-v8/classes bindings")
    if set(daily) != set(frozen_seed_symbols(root)):
        raise AuthorityV6Error("PIT-v9 daily rows do not cover the exact frozen seed symbol set")
    if base_pit.get("digest") != U.digest_of(dict(base_pit)):
        raise AuthorityV6Error("PIT-v8 digest mismatch")
    rebuilt = U.build({s: dict(rows) for s, rows in daily.items()},
                      manifest_digest=provisional_manifest_digest,
                      classes=dict(classes), top_n=40,
                      universe_id="PIT-TOP40-QV30D-V9")
    old_by_time = {week["monday_ms"]: week for week in base_pit.get("weeks", ())}
    new_by_time = {week["monday_ms"]: week for week in rebuilt.get("weeks", ())}
    for time_ms, old in old_by_time.items():
        if time_ms > max(old_by_time):
            continue
        if new_by_time.get(time_ms) != old:
            raise AuthorityV6Error("PIT-v9 rebuild changed a frozen PIT-v8 weekly cell")
    missing = [time_ms for time_ms in NEW_CELLS if time_ms not in new_by_time]
    if missing:
        raise AuthorityV6Error(f"PIT-v9 is missing frozen new cells: {missing}")
    out = copy.deepcopy(rebuilt)
    out["format"] = base_pit["format"]
    out["universe_id"] = "PIT-TOP40-QV30D-V9"
    out["rule"] = copy.deepcopy(base_pit["rule"])
    out["weeks"] = [copy.deepcopy(week) for week in base_pit["weeks"]]
    out["weeks"].extend(copy.deepcopy(new_by_time[time_ms]) for time_ms in NEW_CELLS)
    out["manifest_digest"] = provisional_manifest_digest
    out["digest"] = U.digest_of(out)
    return out


def finalize_pit_v9(first_pass: Mapping, replay: Mapping, *, manifest_digest: str) -> dict:
    if not HEX64.fullmatch(manifest_digest):
        raise AuthorityV6Error("PIT-v9 requires a manifest-v6 content digest")
    _validate_pit_v9_prefix(first_pass)
    _validate_pit_v9_prefix(replay)
    if _pit_semantic(first_pass) != _pit_semantic(replay):
        raise AuthorityV6Error("PIT-v9 replay changed membership/ranking/score/veto semantics")
    out = copy.deepcopy(dict(first_pass))
    out["manifest_digest"] = manifest_digest
    out["digest"] = U.digest_of(out)
    return out


def _snapshot_eligible(payload: Mapping) -> frozenset[str]:
    eligible = set()
    for row in payload.get("symbols", ()):
        if not isinstance(row, Mapping):
            continue
        if not (row.get("status") == "TRADING" and row.get("contractType") == "PERPETUAL"
                and row.get("quoteAsset") == "USDT" and row.get("underlyingType") == "COIN"):
            continue
        filters = {item.get("filterType"): item for item in row.get("filters", ())
                   if isinstance(item, Mapping)}
        if set(("PRICE_FILTER", "MARKET_LOT_SIZE", "MIN_NOTIONAL")) - set(filters):
            continue
        try:
            values = (Decimal(filters["PRICE_FILTER"]["tickSize"]),
                      Decimal(filters["MARKET_LOT_SIZE"]["stepSize"]),
                      Decimal(filters["MARKET_LOT_SIZE"]["minQty"]),
                      Decimal(filters["MARKET_LOT_SIZE"]["maxQty"]),
                      Decimal(filters["MIN_NOTIONAL"].get("notional",
                                                          filters["MIN_NOTIONAL"].get("minNotional"))))
        except Exception:
            continue
        if all(value.is_finite() and value > 0 for value in values):
            eligible.add(row.get("symbol"))
    return frozenset(symbol for symbol in eligible if isinstance(symbol, str))


def _weekly_membership_cells(pit: Mapping, eligible: frozenset[str], start_ms: int,
                             end_ms: int) -> list[dict]:
    active = max((week for week in pit.get("weeks", ()) if week["monday_ms"] <= start_ms),
                 key=lambda week: week["monday_ms"], default=None)
    if active is None:
        raise AuthorityV6Error("PIT cell unavailable at segment start")
    weeks = [active] + [week for week in pit.get("weeks", ())
                        if start_ms < week["monday_ms"] < end_ms]
    cells = []
    for week in weeks:
        members = [symbol for symbol in week["books"]["crypto"]["members"] if symbol in eligible][:20]
        if len(members) != 20 or len(set(members)) != 20:
            raise AuthorityV6Error("filter-eligible CORE-20 cell is incomplete")
        cells.append({"monday_utc": week["monday_utc"], "members": members})
    return cells


def _iso_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc).timestamp() * 1000)


def build_filter_v4(base_filter_raw: bytes, pit_v9: Mapping, snapshot_payloads: Mapping[str, bytes],
                    *, pit_byte_sha256: str) -> dict:
    plan = load_frozen_plan()
    expected = plan["base_authorities"]["filter_v3"]
    if sha256(base_filter_raw) != expected["byte_sha256"]:
        raise AuthorityV6Error("filter-v3 exact bytes changed")
    base_filter = _load_strict(base_filter_raw, "filter-v3")
    if not isinstance(base_filter, dict):
        raise AuthorityV6Error("filter-v3 must be an object")
    if base_filter.get("version") != 3:
        raise AuthorityV6Error("filter-v4 must rebind exact filter-v3")
    _validate_pit_v9_prefix(pit_v9)
    if sha256(canonical(pit_v9)) != pit_byte_sha256:
        raise AuthorityV6Error("PIT-v9 byte binding does not match supplied bytes")
    snapshots = copy.deepcopy(base_filter.get("snapshots"))
    eligible_by_capture = {}
    for row in snapshots:
        capture = row["captured_at"]
        payload_raw = snapshot_payloads.get(capture)
        if payload_raw is None:
            raise AuthorityV6Error("frozen filter snapshot payload is missing")
        if not isinstance(payload_raw, bytes) or sha256(payload_raw) != row.get("sha256"):
            raise AuthorityV6Error("frozen filter snapshot byte checksum mismatch")
        payload = _load_strict(payload_raw, "frozen filter snapshot")
        if not isinstance(payload, Mapping):
            raise AuthorityV6Error("frozen filter snapshot must be an object")
        eligible_by_capture[capture] = _snapshot_eligible(payload)
    splits = plan["walk_forward_splits"]
    segments = {
        "development_eligible_segment": splits["development_filter_eligible_segment"],
        "wf1": splits["forward_folds"][0], "wf2": splits["forward_folds"][1],
        "wf3": splits["forward_folds"][2], "sealed_holdout": splits["sealed_holdout"],
    }
    bindings = plan["acquisition_sequence"][5]["fold_bindings"]
    membership, membership_cells = {}, {}
    for name, span in segments.items():
        start_text, end_text = span.split("/")
        capture = bindings[name]
        if capture not in eligible_by_capture:
            raise AuthorityV6Error("filter-v4 binding has no frozen snapshot")
        cells = _weekly_membership_cells(
            pit_v9, eligible_by_capture[capture], _iso_ms(start_text), _iso_ms(end_text))
        membership_cells[name] = cells
        membership[name] = sha256(canonical(cells))
    result = {
        "authority": base_filter["authority"], "version": 4, "status": "FROZEN",
        "signed_by": "codex", "source_policy": base_filter["source_policy"],
        "fold_policy": base_filter["fold_policy"], "filter_policy": base_filter["filter_policy"],
        "identity_effect": "snapshot/filter semantics unchanged; PIT-v9 identity and memberships rebound",
        "known_limitation": base_filter["known_limitation"],
        "supersedes": {"version": 3,
                       "pit_universe_path": base_filter["pit_universe_path"],
                       "pit_universe_sha256": base_filter["pit_universe_sha256"],
                       "pit_universe_digest": base_filter["pit_universe_digest"]},
        "rebind_scope": {"pit_identity_only": True, "snapshot_rows_changed": False,
                         "fold_bindings_changed": False, "market_rows_read": False,
                         "candidate_outcomes_read": False},
        "pit_universe_path": "research_evidence/universe/pit-top40-qv30d-precutoff-v9.json",
        "pit_universe_sha256": pit_byte_sha256, "pit_universe_digest": pit_v9["digest"],
        "snapshots": snapshots, "fold_bindings": copy.deepcopy(bindings),
        "weekly_membership": membership_cells, "weekly_membership_sha256": membership,
        "forbidden": copy.deepcopy(base_filter["forbidden"]),
    }
    result["digest"] = content_digest(result)
    return result


def derive_funding_expected(events: Sequence[tuple[int, int | None]], *,
                            start_ms: int, end_ms: int) -> tuple[int, ...]:
    """Derive a fail-closed funding grid from explicit prior cadence authority.

    Archive rows declare ``funding_interval_hours``.  Funding-history API rows do
    not, so the last explicit archive cadence remains authoritative until another
    explicit cadence row says otherwise.  A missing expected API settlement is
    therefore exposed as a coverage gap, never reinterpreted as a longer cadence.
    """
    if not (type(start_ms) is int and type(end_ms) is int and start_ms < end_ms <= CUTOFF_MS):
        raise AuthorityV6Error("invalid funding coverage bounds")
    ordered = sorted(events)
    if len({time_ms for time_ms, _ in ordered}) != len(ordered):
        raise AuthorityV6Error("duplicate funding event timestamp")
    if any(type(time_ms) is not int or time_ms >= CUTOFF_MS
           or (hours is not None and (type(hours) is not int or hours not in (1, 2, 4, 8)))
           for time_ms, hours in ordered):
        raise AuthorityV6Error("funding event/cadence is malformed or crosses cutoff")
    prior = [(time_ms, hours) for time_ms, hours in ordered if time_ms < start_ms and hours is not None]
    if not prior:
        raise AuthorityV6Error("no explicit pre-window funding cadence authority")
    cursor, interval_hours = prior[-1]
    actual = {time_ms: hours for time_ms, hours in ordered}
    expected = []
    while True:
        cursor += interval_hours * HOUR_MS
        if cursor >= end_ms:
            break
        if cursor >= start_ms:
            expected.append(cursor)
        if cursor in actual and actual[cursor] is not None:
            interval_hours = actual[cursor]
    return tuple(expected)


def coverage_v4(pit: Mapping, filter_v4: Mapping,
                rows: Mapping[tuple[str, str | None, str], Sequence[int]],
                funding_events: Mapping[str, Sequence[tuple[int, int | None]]], *,
                manifest_digest: str) -> dict:
    """Build the exact missing-row report from a resolved authority-row index.

    Funding expectations are derived here from admitted events plus explicit archive
    cadence.  A caller cannot provide a sparse expected grid.
    """
    plan = load_frozen_plan()
    if not HEX64.fullmatch(manifest_digest):
        raise AuthorityV6Error("coverage-v4 requires the manifest-v6 digest")
    _validate_pit_v9_prefix(pit)
    if filter_v4.get("digest") != content_digest(filter_v4):
        raise AuthorityV6Error("filter-v4 digest mismatch")
    gaps = []
    # PIT ranking authority: every symbol audited in each new weekly cell needs its
    # complete causal 30-day daily window.
    for week in pit.get("weeks", ()):
        if week.get("monday_ms") not in NEW_CELLS:
            continue
        symbols = set()
        for book in week["books"].values():
            symbols.update(book["members"])
        symbols.update(row[0] for row in week.get("vetoes", ()))
        symbols.update(row[0] for row in week.get("gaps", ()))
        symbols.update(row.get("symbol") for row in week.get("not_addressable", ())
                       if isinstance(row, Mapping) and isinstance(row.get("symbol"), str))
        for symbol in sorted(symbols):
            have = set(rows.get(("klines", "1d", symbol), ()))
            for time_ms in range(week["monday_ms"] - 30 * DAY_MS, week["monday_ms"], DAY_MS):
                if time_ms not in have:
                    gaps.append({"stream": "klines:1d", "symbol": symbol,
                                 "start_ms": time_ms, "end_ms": time_ms + DAY_MS})
    # Evaluation streams: union of filter-eligible members at each segment cell.
    membership = filter_v4.get("weekly_membership")
    membership_sha = filter_v4.get("weekly_membership_sha256")
    if not isinstance(membership, dict) or not isinstance(membership_sha, dict):
        raise AuthorityV6Error("filter-v4 exact weekly membership is missing")
    if set(membership) != set(membership_sha):
        raise AuthorityV6Error("filter-v4 membership/hash segment sets differ")
    for name, cells in membership.items():
        if sha256(canonical(cells)) != membership_sha[name]:
            raise AuthorityV6Error(f"filter-v4 {name} membership digest mismatch")
        for cell in cells:
            members = cell.get("members") if isinstance(cell, Mapping) else None
            if not isinstance(members, list) or len(members) != 20 or len(set(members)) != 20:
                raise AuthorityV6Error(f"filter-v4 {name} has an incomplete membership cell")
    holdout_cells = membership.get("sealed_holdout")
    if not holdout_cells:
        raise AuthorityV6Error("filter-v4 sealed-holdout membership is missing")
    crypto_union = {symbol for cell in holdout_cells for symbol in cell["members"]}
    holdout_start = _iso_ms(plan["walk_forward_splits"]["sealed_holdout"].split("/")[0])
    for symbol in sorted(crypto_union):
        for series in ("klines", "markPriceKlines"):
            have = set(rows.get((series, "1h", symbol), ()))
            # Only the extension segment is checked here; v5 coverage remains separately bound.
            for time_ms in range(holdout_start, CUTOFF_MS, HOUR_MS):
                if time_ms not in have:
                    gaps.append({"stream": f"{series}:1h", "symbol": symbol,
                                 "start_ms": time_ms, "end_ms": time_ms + HOUR_MS})
        events = funding_events.get(symbol)
        have_funding = set(rows.get(("fundingRate", None, symbol), ()))
        if not events:
            gaps.append({"stream": "fundingRate:null", "symbol": symbol,
                         "start_ms": holdout_start, "end_ms": CUTOFF_MS,
                         "reason": "MISSING_EXPLICIT_CADENCE_AUTHORITY"})
            expected_funding = ()
        else:
            expected_funding = derive_funding_expected(
                events, start_ms=holdout_start, end_ms=CUTOFF_MS)
        for time_ms in expected_funding:
            if time_ms >= CUTOFF_MS:
                raise AuthorityV6Error("funding expected grid crosses cutoff")
            if time_ms not in have_funding:
                gaps.append({"stream": "fundingRate:null", "symbol": symbol,
                             "start_ms": time_ms, "end_ms": time_ms + 1})
    gaps.sort(key=lambda row: (row["stream"], row["symbol"], row["start_ms"]))
    by_stream = {stream: 0 for stream in (
        "klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null")}
    for gap in gaps:
        by_stream[gap["stream"]] += 1
    result = {
        "format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
        "authority_version": 4, "cutoff_exclusive_ms": CUTOFF_MS,
        "manifest_digest": manifest_digest, "pit_universe_digest": pit.get("digest"),
        "filter_authority_digest": filter_v4.get("digest"),
        "required_streams": list(by_stream), "guaranteed_period_gaps_by_stream": by_stream,
        "guaranteed_period_gaps": len(gaps), "gaps": gaps,
        "required_result": "ZERO_GUARANTEED_PERIOD_GAPS",
    }
    result["digest"] = content_digest(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            frozen = load_frozen_plan()
            symbols = frozen_seed_symbols()
            print(json.dumps({"authority_id": frozen["authority_id"], "symbols": len(symbols),
                              "daily_seed_requests": len(daily_seed_requests(symbols)),
                              "network_performed": False}, sort_keys=True))
            return 0
    except (AuthorityV6Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
