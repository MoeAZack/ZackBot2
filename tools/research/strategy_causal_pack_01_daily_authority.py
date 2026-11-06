"""Pre-cutoff Binance Vision daily-archive authority for STRATEGY-CAUSAL-PACK-01.

The plan path reads repository metadata only.  It expands a fixed allowlist of UTC dates
(2026-09-01..2026-09-27), streams, and causal wf3 symbols into official public URLs.
The optional fetch path verifies each sibling `.CHECKSUM` before atomically keeping a zip
and its manifest `.ok` sidecar.  The build path then parses only those verified allowlisted
zips and derives a new immutable manifest from monthly files whose declared periods end no
later than the frozen cutoff.  No date, caller symbol, or caller stream can widen the plan.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from tools.research import binance_archive_fill as BAF
from tools.research import manifest as M
from tools.research import strategy_causal_pack_01_archive as A
from tools.research import universe as U

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts/strategy_causal_pack_01/strategy_causal_pack_01_contract.v1.json"
FILTER_AUTHORITY = ROOT / "contracts/strategy_causal_pack_01/exchange_filter_authority.v2.json"
KLINE_GAP_PLAN = ROOT / "contracts/strategy_causal_pack_01/precutoff_kline_gap_plan.v1.json"
BASE_MANIFEST = ROOT / "research_evidence/manifests/binance-um-archive-v1.json.gz"
BASE_UNIVERSE = ROOT / "research_evidence/universe/pit-top40-qv30d-v4.json"
CUTOFF = "2026-09-28T00:00:00Z"
CUTOFF_MS = A.CUTOFF_MS
FUNDING_START_MS = 1_788_220_800_000
FUNDING_END_MS = CUTOFF_MS - 1
ALLOWED_DATES = tuple(f"2026-09-{day:02d}" for day in range(1, 28))
STREAMS = (
    ("klines", "4h"),
    ("klines", "1h"),
    ("markPriceKlines", "1h"),
    ("fundingRate", None),
)
FORMAT = "zb-strategy-causal-pack-01-daily-authority-plan/1"
SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")
KLINE_LIMIT = 1500


class DailyAuthorityError(ValueError):
    pass


def _canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x))), raw


def required_symbols(contract: dict, pit: dict, authorities: dict[str, A.FoldFilterAuthority]) -> tuple[str, ...]:
    """All symbols that can enter the final fold; metadata only, no archive row access."""
    fold = next((row for row in contract["folds"] if row["name"] == "wf3"), None)
    if fold is None:
        raise DailyAuthorityError("wf3 fold is missing")
    start_ms, end_ms = A._utc_ms(fold["start"]), A._utc_ms(fold["end"])
    weeks = [week for week in pit["weeks"] if week["monday_ms"] <= start_ms]
    if not weeks:
        raise DailyAuthorityError("PIT universe has no wf3 start cell")
    selected = [max(weeks, key=lambda week: week["monday_ms"])]
    selected.extend(week for week in pit["weeks"] if start_ms < week["monday_ms"] < end_ms)
    eligible = authorities["wf3"].eligible_symbols
    seen: list[str] = []
    for week in selected:
        members = [symbol for symbol in week["books"]["crypto"]["members"] if symbol in eligible][:20]
        if len(members) != 20 or len(set(members)) != 20:
            raise DailyAuthorityError("wf3 causal CORE-20 cell is incomplete")
        for symbol in members:
            if symbol not in seen:
                seen.append(symbol)
    if not all(SYMBOL_RE.fullmatch(symbol) for symbol in seen):
        raise DailyAuthorityError("wf3 contains a symbol outside the archive path alphabet")
    return tuple(seen)


def archive_path(series: str, interval: str | None, symbol: str, day: str) -> str:
    if day not in ALLOWED_DATES or (series, interval) not in STREAMS or not SYMBOL_RE.fullmatch(symbol):
        raise DailyAuthorityError("archive coordinates are outside the frozen allowlist")
    leaf = f"{symbol}-{'fundingRate' if interval is None else interval}-{day}.zip"
    middle = f"/{interval}" if interval is not None else ""
    return f"um/daily/{series}/{symbol}{middle}/{leaf}"


def _monthly_requests_for_windows(windows: tuple[dict, ...], manifest: dict) -> tuple[dict, ...]:
    """Official Vision whole-month keys intersecting proven gaps, only when month_end <= cutoff."""
    existing = {row["path"] for row in manifest["files"]}
    paths = set()
    for window in windows:
        cursor = datetime.fromtimestamp(window["start_ms"] / 1000, timezone.utc)
        cursor = datetime(cursor.year, cursor.month, 1, tzinfo=timezone.utc)
        while int(cursor.timestamp() * 1000) < window["end_ms"]:
            following = datetime(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1,
                                 tzinfo=timezone.utc)
            if int(following.timestamp() * 1000) <= CUTOFF_MS:
                series, interval, symbol = window["series"], window["interval"], window["symbol"]
                rel = (f"um/monthly/{series}/{symbol}/{interval}/"
                       f"{symbol}-{interval}-{cursor.year:04d}-{cursor.month:02d}.zip")
                if rel not in existing:
                    paths.add(rel)
            cursor = following
    return tuple({"path": rel, "url": M.archive_url(rel), "checksum_url": M.archive_url(rel) + ".CHECKSUM"}
                 for rel in sorted(paths))


def monthly_gap_requests(preflight: dict, manifest: dict) -> tuple[dict, ...]:
    return _monthly_requests_for_windows(kline_gap_windows(preflight), manifest)


def fetch_monthly_gaps(preflight: dict, manifest: dict, store: Path, max_total_bytes: int,
                       workers: int = 8) -> dict:
    requests = monthly_gap_requests(preflight, manifest)
    if not 1 <= workers <= 8 or max_total_bytes <= 0:
        raise DailyAuthorityError("monthly fetch requires workers 1..8 and a positive byte cap")
    lock, totals = threading.Lock(), {"files": 0, "bytes": 0, "missing": []}

    def get(url: str):
        last = None
        for attempt in range(4):
            try:
                status, raw, final = BAF.http_fetcher(url)
                if final != url:
                    raise DailyAuthorityError(f"monthly archive redirected: {url}")
                return status, raw
            except OSError as exc:
                last = exc
                if attempt < 3:
                    time.sleep(2 ** attempt)
        raise DailyAuthorityError(f"monthly archive request failed after retries: {last}")

    def one(item: dict):
        target = _target(store, item["path"])
        if target.exists():
            M.scan_archive(str(target), item["path"])
            return
        status, checksum_raw = get(item["checksum_url"])
        if status == 404:
            with lock:
                totals["missing"].append(item["path"])
            return
        if status != 200:
            raise DailyAuthorityError(f"monthly checksum HTTP {status}: {item['checksum_url']}")
        expected = BAF.parse_checksum(checksum_raw, target.name)
        status, raw = get(item["url"])
        if status != 200:
            raise DailyAuthorityError(f"monthly archive HTTP {status}: {item['url']}")
        if _sha(raw) != expected:
            raise DailyAuthorityError(f"monthly archive checksum mismatch: {item['path']}")
        with lock:
            if totals["bytes"] + len(raw) > max_total_bytes:
                raise DailyAuthorityError("monthly verified downloads exceed explicit byte cap")
            totals["bytes"] += len(raw); totals["files"] += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        for path, payload in ((target, raw), (Path(str(target) + ".ok"), (expected + "\n").encode("ascii"))):
            part = Path(str(path) + ".part"); part.write_bytes(payload); os.replace(part, path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, requests))
    totals["requested"] = len(requests); totals["missing"] = sorted(totals["missing"])
    return totals


def expand(plan: dict) -> tuple[dict, ...]:
    validate_plan(plan)
    out = []
    for symbol in plan["symbols"]:
        for day in plan["allowed_dates"]:
            for stream in plan["streams"]:
                path = archive_path(stream["series"], stream["interval"], symbol, day)
                url = M.archive_url(path)
                out.append({"path": path, "url": url, "checksum_url": url + ".CHECKSUM"})
    return tuple(out)


def make_plan() -> dict:
    contract, contract_raw = _load_json(CONTRACT)
    pit, pit_raw = _load_json(BASE_UNIVERSE)
    expected_filter_sha = contract["data"]["exchange_filter_authority_sha256"]
    authorities = A.load_fold_filter_authorities(FILTER_AUTHORITY, expected_filter_sha, ROOT, contract)
    base = M.load(str(BASE_MANIFEST))
    symbols = required_symbols(contract, pit, authorities)
    body = {
        "format": FORMAT,
        "cutoff_exclusive": CUTOFF,
        "allowed_dates": list(ALLOWED_DATES),
        "streams": [{"series": series, "interval": interval} for series, interval in STREAMS],
        "symbols": list(symbols),
        "request_count": len(ALLOWED_DATES) * len(STREAMS) * len(symbols),
        "upstream": "https://data.binance.vision/data/futures/um/daily/",
        "checksum_rule": "verify each zip against its sibling .zip.CHECKSUM before keeping .ok",
        "base_manifest": {"path": str(BASE_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
                          "file_sha256": _sha(BASE_MANIFEST.read_bytes()), "digest": base["digest"]},
        "base_universe": {"path": str(BASE_UNIVERSE.relative_to(ROOT)).replace("\\", "/"),
                          "file_sha256": _sha(pit_raw), "digest": pit["digest"]},
        "contract_sha256": _sha(contract_raw),
        "filter_authority_sha256": expected_filter_sha,
    }
    body["digest"] = _sha(_canon(body))
    validate_plan(body)
    return body


def validate_plan(plan: dict) -> None:
    if plan.get("format") != FORMAT or plan.get("cutoff_exclusive") != CUTOFF:
        raise DailyAuthorityError("plan identity/cutoff changed")
    if tuple(plan.get("allowed_dates", ())) != ALLOWED_DATES:
        raise DailyAuthorityError("daily allowlist must be exactly 2026-09-01..2026-09-27")
    streams = tuple((row.get("series"), row.get("interval")) for row in plan.get("streams", ()))
    if streams != STREAMS:
        raise DailyAuthorityError("stream allowlist changed")
    symbols = plan.get("symbols")
    if not isinstance(symbols, list) or symbols != list(dict.fromkeys(symbols)) or not symbols:
        raise DailyAuthorityError("symbols must be a nonempty ordered unique list")
    if plan.get("request_count") != len(symbols) * len(ALLOWED_DATES) * len(STREAMS):
        raise DailyAuthorityError("request_count changed")
    digest = plan.get("digest")
    if digest != _sha(_canon({key: value for key, value in plan.items() if key != "digest"})):
        raise DailyAuthorityError("plan digest mismatch")
    for day in ALLOWED_DATES:
        if date.fromisoformat(day) + timedelta(days=1) > date(2026, 9, 28):
            raise DailyAuthorityError("allowlisted date crosses the cutoff")
    if "available" in plan:
        candidates = {
            archive_path(stream["series"], stream["interval"], symbol, day)
            for symbol in symbols for day in ALLOWED_DATES for stream in plan["streams"]
        }
        available = plan["available"]
        paths = [row.get("path") for row in available]
        if paths != sorted(set(paths)) or any(path not in candidates for path in paths):
            raise DailyAuthorityError("listed availability is not a sorted subset of the frozen allowlist")
        if any(type(row.get("bytes")) is not int or row["bytes"] <= 0 for row in available):
            raise DailyAuthorityError("listed archive bytes must be positive integers")
        if plan.get("listed_bytes") != sum(row["bytes"] for row in available):
            raise DailyAuthorityError("listed byte total mismatch")


def probe(plan: dict, workers: int = 8) -> dict:
    """Freeze official S3 LIST metadata for the allowlist; never requests a zip or outcome row."""
    validate_plan(plan)
    if not 1 <= workers <= 16:
        raise DailyAuthorityError("probe workers must be in 1..16")
    base = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

    def one(item):
        symbol, (series, interval) = item
        prefix = f"data/futures/um/daily/{series}/{symbol}/" + (f"{interval}/" if interval else "")
        url = base + "?" + urllib.parse.urlencode({"list-type": "2", "prefix": prefix, "max-keys": 1000})
        request = urllib.request.Request(url, headers={"User-Agent": "ZackBot2-precutoff-plan-metadata/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            root = ET.fromstring(response.read())
        rows = []
        for child in root:
            if not child.tag.endswith("Contents"):
                continue
            values = {node.tag.rsplit("}", 1)[-1]: node.text for node in child}
            key = values["Key"]
            if not key.endswith(".zip"):
                continue
            rel = key.removeprefix("data/futures/")
            try:
                meta = M.archive_meta(rel)
            except M.ManifestError:
                continue
            if (meta["series"], meta["interval"]) == (series, interval) and any(
                    rel.endswith(f"-{day}.zip") for day in ALLOWED_DATES):
                rows.append({"path": rel, "bytes": int(values["Size"])})
        return rows

    tasks = [(symbol, stream) for symbol in plan["symbols"] for stream in STREAMS]
    available = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for rows in pool.map(one, tasks):
            available.extend(rows)
    out = {key: value for key, value in plan.items() if key not in ("digest", "available", "listed_bytes")}
    out["available"] = sorted(available, key=lambda row: row["path"])
    out["listed_bytes"] = sum(row["bytes"] for row in available)
    out["digest"] = _sha(_canon(out))
    validate_plan(out)
    return out


def _target(store: Path, rel: str) -> Path:
    root = os.path.realpath(store)
    target = os.path.realpath(os.path.join(root, *rel.split("/")))
    if os.path.normcase(os.path.commonpath((root, target))) != os.path.normcase(root) or target == root:
        raise DailyAuthorityError(f"archive target escapes the store: {rel}")
    return Path(target)


def fetch(plan: dict, store: Path, max_total_bytes: int, workers: int = 8) -> tuple[int, int]:
    """Fetch only expanded allowlist URLs; checksum verification happens before atomic retention."""
    if max_total_bytes <= 0:
        raise DailyAuthorityError("max_total_bytes must be positive")
    if "available" not in plan:
        raise DailyAuthorityError("fetch requires a probed plan with frozen available paths")
    if not 1 <= workers <= 16:
        raise DailyAuthorityError("fetch workers must be in 1..16")
    by_path = {row["path"]: row for row in expand(plan)}
    requests = [(by_path[row["path"]], _target(store, row["path"])) for row in plan["available"]]
    lock = threading.Lock()
    total = 0

    def get(url: str):
        last = None
        for attempt in range(3):
            try:
                status, body, final = BAF.http_fetcher(url)
                if not BAF.archive_url_ok(final):
                    raise DailyAuthorityError(f"offsite redirect: {final}")
                if status in BAF.STOP_STATUS:
                    raise DailyAuthorityError(f"upstream stop {status}: {url}")
                if status in (500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return status, body
            except (OSError, BAF.FillError) as exc:
                last = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise DailyAuthorityError(f"transport failed after retries: {url}: {last}")

    def one(item):
        nonlocal total
        request, target = item
        ok_path = Path(str(target) + ".ok")
        if target.is_file() and ok_path.is_file():
            raw = target.read_bytes()
            expected = ok_path.read_text(encoding="ascii").strip()
            if _sha(raw) == expected:
                with lock:
                    if total + len(raw) > max_total_bytes:
                        raise DailyAuthorityError("verified download exceeds explicit max_total_bytes")
                    total += len(raw)
                return 1
        status, checksum_raw = get(request["checksum_url"])
        if status != 200:
            raise DailyAuthorityError(f"HTTP {status}: {request['checksum_url']}")
        expected = BAF.parse_checksum(checksum_raw, target.name)
        status, raw = get(request["url"])
        if status != 200:
            raise DailyAuthorityError(f"HTTP {status}: {request['url']}")
        actual = _sha(raw)
        if actual != expected:
            raise DailyAuthorityError(f"checksum mismatch: {request['path']} {actual} != {expected}")
        with lock:
            if total + len(raw) > max_total_bytes:
                raise DailyAuthorityError("verified download exceeds explicit max_total_bytes")
            total += len(raw)
        target.parent.mkdir(parents=True, exist_ok=True)
        for path, payload in ((target, raw), (Path(str(target) + ".ok"), (expected + "\n").encode("ascii"))):
            part = Path(str(path) + ".part")
            part.write_bytes(payload)
            os.replace(part, path)
        return 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        files = sum(pool.map(one, requests))
    return files, total


def _funding_url(symbol: str, start_ms: int) -> str:
    if not SYMBOL_RE.fullmatch(symbol) or not FUNDING_START_MS <= start_ms <= FUNDING_END_MS:
        raise DailyAuthorityError("funding query is outside the frozen symbol/time allowlist")
    query = urllib.parse.urlencode([
        ("symbol", symbol), ("startTime", start_ms), ("endTime", FUNDING_END_MS), ("limit", 1000)])
    return "https://fapi.binance.com/fapi/v1/fundingRate?" + query


def fetch_funding(plan: dict, store: Path, workers: int = 8) -> tuple[int, int, int]:
    """Freeze exact bounded raw funding responses and provenance; no query can omit either bound."""
    validate_plan(plan)
    if not 1 <= workers <= 8:
        raise DailyAuthorityError("funding workers must be in 1..8")

    def request(url: str) -> bytes:
        if not (url.startswith("https://fapi.binance.com/fapi/v1/fundingRate?")
                or url == "https://fapi.binance.com/fapi/v1/time"):
            raise DailyAuthorityError("funding fetch refused a non-allowlisted endpoint")
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ZackBot2-precutoff-funding/1"})
                with urllib.request.urlopen(req, timeout=60) as response:
                    if response.status != 200 or response.geturl() != url:
                        raise DailyAuthorityError(f"funding endpoint status/redirect: {response.status}")
                    return response.read()
            except OSError as exc:
                last = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        raise DailyAuthorityError(f"funding endpoint failed after retries: {last}")

    def one(symbol: str):
        cursor, page, byte_count, row_count = FUNDING_START_MS, 0, 0, 0
        while True:
            url = _funding_url(symbol, cursor)
            raw = request(url)
            rel = f"um/api/fundingRate/{symbol}/{symbol}-fundingRate-2026-09-01-2026-09-27-p{page:03d}.json"
            meta = M.archive_meta(rel)
            rows = M.parse_funding_api(rel, raw, meta)
            if any(row["fundingTime"] < cursor or row["fundingTime"] > FUNDING_END_MS for row in rows):
                raise DailyAuthorityError(f"{symbol}: funding page escaped query bounds")
            if len(rows) == 1000:
                next_cursor = rows[-1]["fundingTime"] + 1
                if next_cursor <= cursor or next_cursor > FUNDING_END_MS:
                    raise DailyAuthorityError(f"{symbol}: funding pagination did not advance")
            else:
                next_cursor = None
            server_raw = request("https://fapi.binance.com/fapi/v1/time")
            server = json.loads(server_raw)
            if type(server.get("serverTime")) is not int:
                raise DailyAuthorityError("Binance server-time response is malformed")
            accessed = __import__("datetime").datetime.now(ZoneInfo("Africa/Cairo")).isoformat(timespec="seconds")
            digest = _sha(raw)
            provenance = {
                "format": "zb-bounded-binance-funding-response/1", "endpoint": "/fapi/v1/fundingRate",
                "url": url, "params": {"symbol": symbol, "startTime": cursor,
                                          "endTime": FUNDING_END_MS, "limit": 1000},
                "accessed_cairo": accessed, "server_time_ms": server["serverTime"],
                "response_sha256": digest, "response_bytes": len(raw), "rows": len(rows), "page": page,
                "first_funding_time_ms": rows[0]["fundingTime"] if rows else None,
                "last_funding_time_ms": rows[-1]["fundingTime"] if rows else None,
                "terminal_page": len(rows) < 1000,
                "provenance": "unauthenticated official Binance USD-M funding-history REST response"
            }
            target = _target(store, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            payloads = ((target, raw), (Path(str(target) + ".ok"), (digest + "\n").encode("ascii")),
                        (Path(str(target) + ".provenance.json"),
                         (json.dumps(provenance, sort_keys=True, indent=1) + "\n").encode("utf-8")))
            for path, payload in payloads:
                part = Path(str(path) + ".part")
                part.write_bytes(payload)
                os.replace(part, path)
            byte_count += len(raw); row_count += len(rows); page += 1
            if next_cursor is None:
                return page, byte_count, row_count
            cursor = next_cursor

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, plan["symbols"]))
    return sum(row[0] for row in results), sum(row[1] for row in results), sum(row[2] for row in results)


def funding_summary(plan: dict, store: Path) -> dict:
    """Metadata-only completeness proof over frozen raw responses (never strategy outcomes)."""
    validate_plan(plan)
    tolerance = A.FUNDING_TIME_TOLERANCE_MS
    symbols = []
    for symbol in plan["symbols"]:
        paths = sorted((store / f"um/api/fundingRate/{symbol}").glob("*.json"))
        paths = [path for path in paths if not path.name.endswith(".provenance.json")]
        times, pages = [], []
        for path in paths:
            rel = path.relative_to(store).as_posix()
            row = M.scan_funding_api(str(path), rel)
            _, parsed = M.read_funding_api(str(path), rel, row["source"])
            times.extend(item["fundingTime"] for item in parsed)
            pages.append({"path": rel, "response_sha256": row["sha256"], "rows": row["rows"],
                          "first_ms": row["first_open_ms"], "last_ms": row["last_open_ms"],
                          "provenance_sha256": row["source"]["provenance_sha256"]})
        if any(right <= left for left, right in zip(times, times[1:])):
            raise DailyAuthorityError(f"{symbol}: funding summary found unordered/duplicate rows")
        gaps = [right - left for left, right in zip(times, times[1:])]
        head = times[0] - FUNDING_START_MS if times else None
        tail = CUTOFF_MS - times[-1] if times else None
        complete = bool(times and head <= 8 * A.HOUR_MS + tolerance and tail <= 8 * A.HOUR_MS + tolerance
                        and all(gap <= 8 * A.HOUR_MS + tolerance for gap in gaps))
        symbols.append({"symbol": symbol, "pages": pages, "rows": len(times),
                        "first_ms": times[0] if times else None, "last_ms": times[-1] if times else None,
                        "head_gap_ms": head, "tail_gap_ms": tail,
                        "max_interior_gap_ms": max(gaps, default=None), "bounded_window_complete": complete,
                        "empty_is_explicit_incomplete": not times})
    body = {"format": "zb-strategy-causal-pack-01-funding-authority-summary/1",
            "window": {"start_ms": FUNDING_START_MS, "end_ms_inclusive": FUNDING_END_MS,
                       "cutoff_exclusive_ms": CUTOFF_MS},
            "completeness_rule": "nonempty; head, every interior gap, and cutoff tail <= 8h + frozen 10m jitter tolerance",
            "missing_exposure_rule": "an empty or gapped symbol is retained; any required exposure intersecting its gap makes the candidate incomplete",
            "symbols": symbols,
            "complete_symbols": sum(row["bounded_window_complete"] for row in symbols),
            "explicit_incomplete_symbols": [row["symbol"] for row in symbols if not row["bounded_window_complete"]]}
    body["digest"] = _sha(_canon(body))
    return body


def kline_gap_windows(preflight: dict) -> tuple[dict, ...]:
    """Merge only already-proven manifest gaps; no caller-supplied symbol or range can widen them."""
    if (preflight.get("format") != "zb-strategy-causal-pack-01-authority-coverage-preflight/1"
            or preflight.get("digest") != _sha(_canon({k: v for k, v in preflight.items() if k != "digest"}))):
        raise DailyAuthorityError("kline REST gap source is not a digest-valid authority preflight")
    grouped: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
    for fold in preflight.get("folds", ()):
        for gap in fold.get("gaps", ()):
            series, interval, symbol = gap.get("series"), gap.get("interval"), gap.get("symbol")
            if series == "fundingRate":
                continue
            if ((series, interval) not in STREAMS[:3] or not SYMBOL_RE.fullmatch(symbol or "")
                    or type(gap.get("start_ms")) is not int or type(gap.get("end_ms")) is not int):
                raise DailyAuthorityError("preflight contains a non-allowlisted kline gap")
            start, end, step = gap["start_ms"], gap["end_ms"], M.INTERVALS[interval]
            if start >= end or start % step or end % step or end > CUTOFF_MS:
                raise DailyAuthorityError("preflight contains an unaligned or cutoff-crossing kline gap")
            grouped.setdefault((series, interval, symbol), []).append((start, end))
    windows = []
    for (series, interval, symbol), ranges in sorted(grouped.items()):
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        windows.extend({"series": series, "interval": interval, "symbol": symbol,
                        "start_ms": start, "end_ms": end} for start, end in merged)
    return tuple(windows)


def make_kline_gap_plan(preflight: dict) -> dict:
    body = {"format": "zb-strategy-causal-pack-01-kline-gap-plan/1",
            "source_preflight_digest": preflight.get("digest"), "cutoff_exclusive": CUTOFF,
            "source_order": ["existing-manifest", "binance-vision-monthly", "binance-vision-daily",
                             "bounded-official-rest-residual-only"],
            "windows": list(kline_gap_windows(preflight))}
    body["digest"] = _sha(_canon(body))
    return body


def load_kline_gap_plan(path: Path = KLINE_GAP_PLAN) -> dict:
    plan, _ = _load_json(path)
    if (plan.get("format") != "zb-strategy-causal-pack-01-kline-gap-plan/1"
            or plan.get("cutoff_exclusive") != CUTOFF
            or plan.get("digest") != _sha(_canon({k: v for k, v in plan.items() if k != "digest"}))):
        raise DailyAuthorityError("kline gap plan identity/digest changed")
    for row in plan.get("windows", ()):
        step = M.INTERVALS.get(row.get("interval"), 0)
        if ((row.get("series"), row.get("interval")) not in STREAMS[:3]
                or not SYMBOL_RE.fullmatch(row.get("symbol", "")) or type(row.get("start_ms")) is not int
                or type(row.get("end_ms")) is not int or row["start_ms"] >= row["end_ms"]
                or row["end_ms"] > CUTOFF_MS or row["start_ms"] % step or row["end_ms"] % step):
            raise DailyAuthorityError("kline gap plan contains an invalid window")
    return plan


def _kline_url(window: dict, cursor: int) -> str:
    series, interval, symbol = window["series"], window["interval"], window["symbol"]
    start, end = window["start_ms"], window["end_ms"]
    step = M.INTERVALS.get(interval, 0)
    if ((series, interval) not in STREAMS[:3] or not SYMBOL_RE.fullmatch(symbol)
            or not start <= cursor < end <= CUTOFF_MS or cursor % step or start % step or end % step):
        raise DailyAuthorityError("kline query is outside its frozen gap window")
    endpoint = "klines" if series == "klines" else "markPriceKlines"
    query = urllib.parse.urlencode([
        ("symbol", symbol), ("interval", interval), ("startTime", cursor),
        ("endTime", end - 1), ("limit", KLINE_LIMIT)])
    return f"https://fapi.binance.com/fapi/v1/{endpoint}?{query}"


def fetch_kline_gaps(preflight: dict, store: Path, workers: int = 8) -> dict:
    """Freeze official responses for exactly the guaranteed gaps in a signed preflight."""
    windows = kline_gap_windows(preflight)
    if not 1 <= workers <= 8:
        raise DailyAuthorityError("kline workers must be in 1..8")

    throttle_lock = threading.Lock()
    last_request = [0.0]

    def request(url: str) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != M.FUNDING_API_HOST
                or parsed.path not in ("/fapi/v1/klines", "/fapi/v1/markPriceKlines", "/fapi/v1/time")):
            raise DailyAuthorityError("kline fetch refused a non-allowlisted endpoint")
        last = None
        for attempt in range(4):
            try:
                # A 1500-row USD-M kline call has high request weight. Preserve eight I/O workers but
                # globally pace calls below the documented per-minute budget.
                with throttle_lock:
                    delay = 0.4 - (time.monotonic() - last_request[0])
                    if delay > 0:
                        time.sleep(delay)
                    last_request[0] = time.monotonic()
                req = urllib.request.Request(url, headers={"User-Agent": "ZackBot2-precutoff-kline-authority/1"})
                with urllib.request.urlopen(req, timeout=90) as response:
                    if response.status != 200 or response.geturl() != url:
                        raise DailyAuthorityError(f"kline endpoint status/redirect: {response.status}")
                    return response.read()
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:300]
                last = DailyAuthorityError(f"HTTP {exc.code}: {body}")
                if exc.code == 400:
                    raise DailyAuthorityError(f"bounded kline endpoint rejected historical request: {body}")
                retry_after = exc.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 60))
                elif attempt < 3:
                    time.sleep(2 ** attempt)
            except OSError as exc:
                last = exc
                if attempt < 3:
                    time.sleep(2 ** attempt)
        raise DailyAuthorityError(f"kline endpoint failed after retries: {last}")

    def one(window: dict) -> dict:
        series, interval, symbol = window["series"], window["interval"], window["symbol"]
        start, end = window["start_ms"], window["end_ms"]
        cursor, page, total_bytes, total_rows = start, 0, 0, 0
        while cursor < end:
            url = _kline_url(window, cursor)
            endpoint = "klines" if series == "klines" else "markPriceKlines"
            rel = (f"um/api/{series}/{symbol}/{interval}/{symbol}-{interval}-{start:013d}-{end:013d}"
                   f"-p{page:03d}.json")
            meta = M.archive_meta(rel)
            target = _target(store, rel)
            if target.exists():
                frozen = M.scan_kline_api(str(target), rel)
                raw, rows = M.read_kline_api(str(target), rel, frozen["source"])
                if frozen["source"]["url"] != url:
                    raise DailyAuthorityError(f"{rel}: frozen response URL does not match deterministic pagination")
            else:
                raw = request(url)
                rows = M.parse_kline_api(rel, raw, meta)
            if rows and rows[0][0] != cursor:
                raise DailyAuthorityError(
                    f"{series}/{symbol}/{interval}: endpoint lacks history at {cursor}; first={rows[0][0]}")
            if any(row[0] < cursor or row[0] >= end for row in rows):
                raise DailyAuthorityError(f"{series}/{symbol}/{interval}: response escaped bounded query")
            terminal = len(rows) < KLINE_LIMIT
            if not target.exists():
                server = json.loads(request("https://fapi.binance.com/fapi/v1/time"))
                if type(server.get("serverTime")) is not int:
                    raise DailyAuthorityError("Binance server-time response is malformed")
                digest = _sha(raw)
                provenance = {
                "format": "zb-bounded-binance-kline-response/1", "endpoint": f"/fapi/v1/{endpoint}",
                "url": url, "params": {"symbol": symbol, "interval": interval, "startTime": cursor,
                                            "endTime": end - 1, "limit": KLINE_LIMIT},
                "window": {"start_ms": start, "end_ms_exclusive": end}, "page": page,
                "accessed_cairo": datetime.now(ZoneInfo("Africa/Cairo")).isoformat(timespec="seconds"),
                "server_time_ms": server["serverTime"], "response_sha256": digest,
                "response_bytes": len(raw), "rows": len(rows),
                "first_open_ms": rows[0][0] if rows else None,
                "last_open_ms": rows[-1][0] if rows else None, "terminal_page": terminal,
                "provenance": "unauthenticated official Binance USD-M historical kline REST response"
                }
                target.parent.mkdir(parents=True, exist_ok=True)
                payloads = ((target, raw), (Path(str(target) + ".ok"), (digest + "\n").encode("ascii")),
                            (Path(str(target) + ".provenance.json"),
                             (json.dumps(provenance, sort_keys=True, indent=1) + "\n").encode("utf-8")))
                for path, payload in payloads:
                    part = Path(str(path) + ".part"); part.write_bytes(payload); os.replace(part, path)
            total_bytes += len(raw); total_rows += len(rows); page += 1
            if terminal:
                expected_last = end - M.INTERVALS[interval]
                complete = bool(rows and rows[-1][0] == expected_last)
                return {"window": window, "pages": page, "rows": total_rows, "bytes": total_bytes,
                        "complete": complete, "last_open_ms": rows[-1][0] if rows else None}
            cursor = rows[-1][0] + M.INTERVALS[interval]
        raise DailyAuthorityError(f"{series}/{symbol}/{interval}: pagination ended without terminal page")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, windows))
    return {"windows": len(windows), "pages": sum(r["pages"] for r in results),
            "rows": sum(r["rows"] for r in results), "bytes": sum(r["bytes"] for r in results),
            "incomplete": [r for r in results if not r["complete"]]}


def coverage_preflight(plan: dict, manifest_path: Path, universe_path: Path,
                       funding_summary_path: Path) -> dict:
    """Authority-only period/cell coverage proof; does not evaluate a signal or outcome."""
    validate_plan(plan)
    contract, _ = _load_json(CONTRACT)
    manifest, pit = A.load_authorities(manifest_path, universe_path)
    authorities = A.load_fold_filter_authorities(
        FILTER_AUTHORITY, contract["data"]["exchange_filter_authority_sha256"], ROOT, contract)
    funding, _ = _load_json(funding_summary_path)
    funding_complete = {row["symbol"]: row["bounded_window_complete"] for row in funding["symbols"]}
    index = {}
    for row in manifest["files"]:
        index.setdefault((row["series"], row["interval"], row["symbol"]), []).append(row)
    required = (("klines", "4h"), ("markPriceKlines", "1h"), ("fundingRate", None), ("klines", "1h"))
    fold_results = []
    all_gaps = []
    for fold in contract["folds"]:
        start, end = A._utc_ms(fold["start"]), A._utc_ms(fold["end"])
        boundaries = [start] + sorted(week["monday_ms"] for week in pit["weeks"]
                                      if start < week["monday_ms"] < end) + [end]
        gaps = []
        for left, right in zip(boundaries, boundaries[1:]):
            weeks = [week for week in pit["weeks"] if week["monday_ms"] <= left]
            ranked = max(weeks, key=lambda week: week["monday_ms"])["books"]["crypto"]["members"]
            members = [s for s in ranked if s in authorities[fold["name"]].eligible_symbols][:20]
            for symbol in members:
                for series, interval in required:
                    periods = []
                    for row in index.get((series, interval, symbol), ()):
                        meta = M.archive_meta(row["path"])
                        if row["rows"] and interval is not None:
                            coverage_start = row["first_open_ms"]
                            coverage_end = row["last_open_ms"] + M.INTERVALS[interval]
                        else:
                            coverage_start, coverage_end = meta["month_start_ms"], meta["month_end_ms"]
                        if row["rows"] and coverage_start < right and coverage_end > left:
                            periods.append((max(left, coverage_start), min(right, coverage_end)))
                    cursor = left
                    for period_start, period_end in sorted(periods):
                        if period_start > cursor:
                            gaps.append({"symbol": symbol, "series": series, "interval": interval,
                                         "start_ms": cursor, "end_ms": period_start})
                        cursor = max(cursor, period_end)
                    if cursor < right:
                        gaps.append({"symbol": symbol, "series": series, "interval": interval,
                                     "start_ms": cursor, "end_ms": right})
        fold_results.append({"fold": fold["name"], "guaranteed_period_gaps": len(gaps), "gaps": gaps})
        all_gaps.extend(dict(row, fold=fold["name"]) for row in gaps)

    wf3 = next(row for row in contract["folds"] if row["name"] == "wf3")
    eligible = authorities["wf3"].eligible_symbols
    daily_missing = []
    checked = 0
    for day in ALLOWED_DATES:
        day_ms = int(__import__("datetime").datetime.fromisoformat(day + "T00:00:00+00:00").timestamp() * 1000)
        weeks = [week for week in pit["weeks"] if week["monday_ms"] <= day_ms]
        ranked = max(weeks, key=lambda week: week["monday_ms"])["books"]["crypto"]["members"]
        members = [symbol for symbol in ranked if symbol in eligible][:20]
        for symbol in members:
            for series, interval in STREAMS[:3]:
                checked += 1
                day_end = day_ms + 24 * A.HOUR_MS
                spans = sorted((max(day_ms, row["first_open_ms"]),
                                min(day_end, row["last_open_ms"] + M.INTERVALS[interval]))
                               for row in index.get((series, interval, symbol), ()) if row["rows"]
                               and row["first_open_ms"] < day_end
                               and row["last_open_ms"] + M.INTERVALS[interval] > day_ms)
                cursor = day_ms
                for left, right in spans:
                    if left > cursor:
                        break
                    cursor = max(cursor, right)
                if cursor < day_end:
                    daily_missing.append({"symbol": symbol, "date": day, "series": series,
                                          "interval": interval,
                                          "reason": "manifest_sources_do_not_cover_complete_aligned_utc_day"})
    active_sep_symbols = sorted({row["symbol"] for row in daily_missing} |
                                {symbol for day in ALLOWED_DATES for symbol in
                                 [s for s in max((w for w in pit["weeks"] if w["monday_ms"] <= int(
                                     __import__("datetime").datetime.fromisoformat(day + "T00:00:00+00:00").timestamp()*1000)),
                                     key=lambda w: w["monday_ms"])["books"]["crypto"]["members"]
                                  if s in eligible][:20]})
    funding_incomplete = [symbol for symbol in active_sep_symbols if not funding_complete.get(symbol, False)]
    body = {"format": "zb-strategy-causal-pack-01-authority-coverage-preflight/1",
            "manifest_digest": manifest["digest"], "pit_universe_digest": pit["digest"],
            "mode": "authority metadata/grid only; no candidate signal or economic outcome evaluated",
            "required_streams": [{"series": s, "interval": i} for s, i in required],
            "folds": fold_results,
            "guaranteed_period_gaps": len(all_gaps),
            "guaranteed_period_gaps_by_stream": {
                f"{s}:{i}": sum(row["series"] == s and row["interval"] == i for row in all_gaps)
                for s, i in required},
            "wf3_september": {"active_symbols": active_sep_symbols, "daily_grid_checks": checked,
                              "daily_grid_missing": daily_missing,
                              "funding_incomplete_symbols": funding_incomplete},
            "interpretation": "period gaps are guaranteed missing authority; funding/grid gaps make a candidate incomplete only when its frozen rules require that stream/exposure. The 1h candidate remains pre-run incomplete where its signal bars have no authority."}
    body["digest"] = _sha(_canon(body))
    return body


def build(plan: dict, store: Path, manifest_out: Path, universe_out: Path, accessed_cairo: str,
          manifest_id: str) -> dict:
    """Build the new authority; this is the only command that parses allowlisted market rows."""
    if "available" not in plan:
        raise DailyAuthorityError("build requires a probed plan with frozen available paths")
    by_path = {row["path"]: row for row in expand(plan)}
    requests = [by_path[row["path"]] for row in plan["available"]]
    daily = [M.scan_archive(str(_target(store, row["path"])), row["path"]) for row in requests]
    api_root = store / "um/api/fundingRate"
    api_paths = sorted(path for path in api_root.glob("*/*.json") if not path.name.endswith(".provenance.json"))
    funding = []
    seen_funding_symbols = set()
    for path in api_paths:
        rel = path.relative_to(store).as_posix()
        if M.archive_meta(rel)["month_end_ms"] > CUTOFF_MS:
            raise DailyAuthorityError(f"funding authority crosses cutoff before read: {rel}")
        row = M.scan_funding_api(str(path), rel)
        if row["symbol"] not in plan["symbols"]:
            raise DailyAuthorityError(f"funding authority contains non-allowlisted symbol {row['symbol']}")
        funding.append(row); seen_funding_symbols.add(row["symbol"])
    if seen_funding_symbols != set(plan["symbols"]):
        raise DailyAuthorityError("funding authority lacks a frozen response for every required symbol")
    for symbol in plan["symbols"]:
        pages = sorted((row for row in funding if row["symbol"] == symbol), key=lambda row: row["path"])
        cursor = FUNDING_START_MS
        for page_number, row in enumerate(pages):
            expected_suffix = f"-p{page_number:03d}.json"
            if not row["path"].endswith(expected_suffix):
                raise DailyAuthorityError(f"{symbol}: funding pages are not contiguous")
            provenance_path = _target(store, row["source"]["provenance_path"])
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            if (provenance.get("params") != {"symbol": symbol, "startTime": cursor,
                                               "endTime": FUNDING_END_MS, "limit": 1000}
                    or provenance.get("rows") != row["rows"]
                    or provenance.get("first_funding_time_ms") != row["first_open_ms"]
                    or provenance.get("last_funding_time_ms") != row["last_open_ms"]
                    or provenance.get("terminal_page") != (row["rows"] < 1000)):
                raise DailyAuthorityError(f"{symbol}: funding page provenance/count/bounds mismatch")
            if row["rows"] == 1000:
                cursor = row["last_open_ms"] + 1
            elif page_number != len(pages) - 1:
                raise DailyAuthorityError(f"{symbol}: nonterminal funding page is short")
        if not pages or pages[-1]["rows"] >= 1000:
            raise DailyAuthorityError(f"{symbol}: funding pagination lacks a terminal page")
    base = M.load(str(BASE_MANIFEST))
    gap_plan = load_kline_gap_plan()
    monthly = []
    for item in _monthly_requests_for_windows(tuple(gap_plan["windows"]), base):
        target = _target(store, item["path"])
        if target.exists():
            monthly.append(M.scan_archive(str(target), item["path"]))
    kline_api = []
    for series in ("klines", "markPriceKlines"):
        root = store / f"um/api/{series}"
        for path in sorted(root.glob("*/*/*.json")) if root.exists() else ():
            if path.name.endswith(".provenance.json"):
                continue
            rel = path.relative_to(store).as_posix()
            meta = M.archive_meta(rel)
            if meta["month_end_ms"] > CUTOFF_MS:
                raise DailyAuthorityError(f"kline authority crosses cutoff before read: {rel}")
            # Whole pre-September gaps must come from checksum-published Vision monthly files.
            # REST is only an authority of last resort for residual September partial-month gaps.
            if meta["month_start_ms"] < FUNDING_START_MS:
                continue
            kline_api.append(M.scan_kline_api(str(path), rel))
    retained = []
    for row in base["files"]:
        meta = M.archive_meta(row["path"])
        if meta["month_end_ms"] <= CUTOFF_MS:
            retained.append(dict(row))
    files = sorted((*retained, *daily, *monthly, *funding, *kline_api), key=lambda row: row["path"])
    manifest = {key: base[key] for key in M.TOP_KEYS if key not in ("files", "digest")}
    if not re.fullmatch(r"binance-um-archive-precutoff-[a-z0-9-]+-v\d+", manifest_id):
        raise DailyAuthorityError("manifest_id must be an explicit versioned pre-cutoff authority identity")
    manifest.update(manifest_id=manifest_id, accessed_cairo=accessed_cairo,
                    note="Monthly/Vision authority plus checksum-bound bounded official REST gap authority; no period crosses 2026-09-28T00:00:00Z.",
                    files=files)
    manifest["digest"] = M.digest_of(manifest)
    M.validate(manifest)
    if any(M.archive_meta(row["path"])["month_end_ms"] > CUTOFF_MS for row in manifest["files"]):
        raise DailyAuthorityError("derived manifest contains a cutoff-crossing period")
    M.write(manifest, str(manifest_out))
    pit, _ = _load_json(BASE_UNIVERSE)
    pit["manifest_digest"] = manifest["digest"]
    pit["digest"] = U.digest_of(pit)
    universe_out.write_text(json.dumps(pit, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")
    return {"manifest_digest": manifest["digest"], "pit_universe_digest": pit["digest"],
            "manifest_file_sha256": _sha(manifest_out.read_bytes()),
            "pit_universe_file_sha256": _sha(universe_out.read_bytes()), "files": len(files)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--out", required=True)
    probe_parser = sub.add_parser("probe")
    probe_parser.add_argument("--plan", required=True)
    probe_parser.add_argument("--out", required=True)
    probe_parser.add_argument("--workers", type=int, default=8)
    fetch_parser = sub.add_parser("fetch")
    fetch_parser.add_argument("--plan", required=True)
    fetch_parser.add_argument("--store", required=True)
    fetch_parser.add_argument("--max-total-bytes", required=True, type=int)
    fetch_parser.add_argument("--workers", type=int, default=8)
    funding_parser = sub.add_parser("funding-fetch")
    funding_parser.add_argument("--plan", required=True)
    funding_parser.add_argument("--store", required=True)
    funding_parser.add_argument("--workers", type=int, default=8)
    kline_parser = sub.add_parser("kline-gap-fetch")
    kline_parser.add_argument("--preflight", required=True)
    kline_parser.add_argument("--store", required=True)
    kline_parser.add_argument("--workers", type=int, default=8)
    monthly_parser = sub.add_parser("monthly-gap-fetch")
    monthly_parser.add_argument("--preflight", required=True)
    monthly_parser.add_argument("--manifest", required=True)
    monthly_parser.add_argument("--store", required=True)
    monthly_parser.add_argument("--max-total-bytes", required=True, type=int)
    monthly_parser.add_argument("--workers", type=int, default=8)
    gap_plan_parser = sub.add_parser("gap-plan")
    gap_plan_parser.add_argument("--preflight", required=True)
    gap_plan_parser.add_argument("--out", required=True)
    summary_parser = sub.add_parser("funding-summary")
    summary_parser.add_argument("--plan", required=True)
    summary_parser.add_argument("--store", required=True)
    summary_parser.add_argument("--out", required=True)
    preflight_parser = sub.add_parser("preflight")
    preflight_parser.add_argument("--plan", required=True)
    preflight_parser.add_argument("--manifest", required=True)
    preflight_parser.add_argument("--universe", required=True)
    preflight_parser.add_argument("--funding-summary", required=True)
    preflight_parser.add_argument("--out", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--plan", required=True)
    build_parser.add_argument("--store", required=True)
    build_parser.add_argument("--manifest-out", required=True)
    build_parser.add_argument("--universe-out", required=True)
    build_parser.add_argument("--accessed-cairo", required=True)
    build_parser.add_argument("--manifest-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            plan = make_plan()
            Path(args.out).write_text(json.dumps(plan, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")
            print(f"{plan['request_count']} verified daily archives; digest={plan['digest']}")
        elif args.command == "probe":
            source, _ = _load_json(Path(args.plan))
            plan = probe(source, args.workers)
            Path(args.out).write_text(json.dumps(plan, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")
            print(f"listed {len(plan['available'])}/{plan['request_count']} files, {plan['listed_bytes']} bytes; "
                  f"digest={plan['digest']}")
        elif args.command == "funding-fetch":
            plan, _ = _load_json(Path(args.plan))
            pages, total, rows = fetch_funding(plan, Path(args.store), args.workers)
            print(f"froze {pages} bounded funding response(s), {rows} row(s), {total} raw bytes")
        elif args.command == "funding-summary":
            plan, _ = _load_json(Path(args.plan))
            summary = funding_summary(plan, Path(args.store))
            Path(args.out).write_text(json.dumps(summary, sort_keys=True, indent=1) + "\n",
                                      encoding="utf-8", newline="\n")
            print(f"complete={summary['complete_symbols']}, incomplete={summary['explicit_incomplete_symbols']}, "
                  f"digest={summary['digest']}")
        elif args.command == "kline-gap-fetch":
            preflight, _ = _load_json(Path(args.preflight))
            print(json.dumps(fetch_kline_gaps(preflight, Path(args.store), args.workers), sort_keys=True))
        elif args.command == "monthly-gap-fetch":
            preflight, _ = _load_json(Path(args.preflight))
            manifest = M.load(args.manifest)
            print(json.dumps(fetch_monthly_gaps(preflight, manifest, Path(args.store),
                                                args.max_total_bytes, args.workers), sort_keys=True))
        elif args.command == "gap-plan":
            preflight, _ = _load_json(Path(args.preflight))
            gap_plan = make_kline_gap_plan(preflight)
            Path(args.out).write_text(json.dumps(gap_plan, sort_keys=True, indent=1) + "\n",
                                      encoding="utf-8", newline="\n")
            print(f"windows={len(gap_plan['windows'])}, digest={gap_plan['digest']}")
        elif args.command == "preflight":
            plan, _ = _load_json(Path(args.plan))
            report = coverage_preflight(plan, Path(args.manifest), Path(args.universe), Path(args.funding_summary))
            Path(args.out).write_text(json.dumps(report, sort_keys=True, indent=1) + "\n",
                                      encoding="utf-8", newline="\n")
            print(f"period_gaps={report['guaranteed_period_gaps']}, "
                  f"wf3_daily_missing={len(report['wf3_september']['daily_grid_missing'])}, "
                  f"funding_incomplete={report['wf3_september']['funding_incomplete_symbols']}, "
                  f"digest={report['digest']}")
        else:
            plan, _ = _load_json(Path(args.plan))
            validate_plan(plan)
            if args.command == "fetch":
                files, total = fetch(plan, Path(args.store), args.max_total_bytes, args.workers)
                print(f"verified {files} files, {total} bytes")
            else:
                print(json.dumps(build(plan, Path(args.store), Path(args.manifest_out), Path(args.universe_out),
                                       args.accessed_cairo, args.manifest_id), sort_keys=True))
        return 0
    except (A.ArchiveIncomplete, BAF.FillError, DailyAuthorityError, M.ManifestError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
