"""Frozen archive manifest and checksum gate for the signed CARRY-01 study.

This module plans inputs and verifies bytes only.  It deliberately does not calculate
returns; result generation belongs to the separately tested historical runner.
"""
from __future__ import annotations

import hashlib
import csv
import io
import json
import math
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests


BASE = "https://data.binance.vision/data"
SEAL = datetime(2026, 9, 28, tzinfo=timezone.utc)
SEAL_MS = int(SEAL.timestamp() * 1000)
PREREG = Path("research_evidence/prereg/carry01_cash_and_carry.v1.json")
MAX_ARCHIVE_MEMBER_BYTES = 50_000_000
MAX_DOWNLOAD_BYTES = 100_000_000
HOUR_MS = 3_600_000
MIN_COMMON_COVERAGE = 0.99
MAX_INCOMPLETE_RUN_HOURS = 24
KLINE_HEADER = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


class HistoryDataError(ValueError):
    """The frozen contract or an archive cannot support the study."""


@dataclass(frozen=True)
class ArchiveSpec:
    symbol: str
    market: str
    series: str
    frequency: str
    period: str
    url: str
    checksum_url: str


@dataclass(frozen=True)
class KlineArchive:
    """Validated usable rows plus a deterministic ledger of unusable source hours."""

    rows: list[dict[str, Any]]
    incomplete: list[dict[str, Any]]
    expected_hours: int

    @property
    def valid_hours(self) -> int:
        return len(self.rows)

    @property
    def coverage(self) -> float:
        return self.valid_hours / self.expected_hours


def canonical_digest(contract: dict[str, Any]) -> str:
    material = dict(contract)
    material.pop("contract_digest_sha256", None)
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_signed_contract(path: Path = PREREG) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    validate_contract(contract)
    return contract


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("signed") is not True or not str(contract.get("status", "")).startswith("SIGNED"):
        raise HistoryDataError("CARRY-01 contract is not signed")
    if contract.get("contract_digest_sha256") != canonical_digest(contract):
        raise HistoryDataError("CARRY-01 contract digest mismatch")
    end = datetime.fromisoformat(contract["data"]["sealed_development_end_exclusive"].replace("Z", "+00:00"))
    if end != SEAL:
        raise HistoryDataError("unexpected CARRY-01 data seal")


def _months(start: date, end_exclusive: date) -> Iterable[str]:
    cursor = start.replace(day=1)
    while cursor < end_exclusive:
        yield cursor.strftime("%Y-%m")
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)


def _days(start: date, end_exclusive: date) -> Iterable[str]:
    cursor = start
    while cursor < end_exclusive:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def _archive(symbol: str, market: str, series: str, frequency: str, period: str) -> ArchiveSpec:
    if market == "spot":
        directory = f"spot/{frequency}/klines/{symbol}/1h"
        name = f"{symbol}-1h-{period}.zip"
    elif series == "markPriceKlines":
        directory = f"futures/um/{frequency}/markPriceKlines/{symbol}/1h"
        name = f"{symbol}-1h-{period}.zip"
    elif series == "fundingRate":
        directory = f"futures/um/{frequency}/fundingRate/{symbol}"
        name = f"{symbol}-fundingRate-{period}.zip"
    else:
        raise HistoryDataError(f"unsupported series: {market}/{series}")
    url = f"{BASE}/{directory}/{name}"
    return ArchiveSpec(symbol, market, series, frequency, period, url, url + ".CHECKSUM")


def build_manifest(contract: dict[str, Any]) -> list[ArchiveSpec]:
    """Plan only archives that cannot contain a row at or beyond the signed seal."""
    validate_contract(contract)
    specs: list[ArchiveSpec] = []
    monthly_end = date(2026, 9, 1)
    daily_start, daily_end = monthly_end, SEAL.date()
    for symbol, raw_start in contract["data"]["pairs"].items():
        start = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).date()
        for period in _months(start, monthly_end):
            specs.extend((_archive(symbol, "spot", "klines", "monthly", period),
                          _archive(symbol, "futures", "markPriceKlines", "monthly", period),
                          _archive(symbol, "futures", "fundingRate", "monthly", period)))
        for period in _days(daily_start, daily_end):
            specs.extend((_archive(symbol, "spot", "klines", "daily", period),
                          _archive(symbol, "futures", "markPriceKlines", "daily", period),
                          _archive(symbol, "futures", "fundingRate", "daily", period)))
    if len({s.url for s in specs}) != len(specs):
        raise HistoryDataError("archive manifest contains duplicates")
    return specs


def parse_checksum(raw: bytes, expected_filename: str | None = None) -> str:
    try:
        fields = raw.decode("ascii").strip().split()
        token = fields[0].lower()
    except (UnicodeDecodeError, IndexError) as exc:
        raise HistoryDataError("unreadable archive checksum") from exc
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise HistoryDataError("invalid archive checksum")
    if expected_filename is not None:
        filename = fields[1][1:] if len(fields) == 2 and fields[1].startswith("*") else (fields[1] if len(fields) == 2 else "")
        if len(fields) != 2 or "*" in filename or filename != expected_filename:
            raise HistoryDataError("archive checksum names a different file")
    return token


def verify_bytes(payload: bytes, checksum: bytes, expected_filename: str | None = None) -> str:
    expected = parse_checksum(checksum, expected_filename)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise HistoryDataError("archive checksum mismatch")
    return actual


def safe_archive_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.hostname == "data.binance.vision"
            and port in (None, 443) and parsed.username is None and parsed.password is None
            and parsed.path.startswith("/data/") and "//" not in parsed.path
            and not parsed.query and not parsed.fragment)


def archive_relative_path(url: str) -> Path:
    if not safe_archive_url(url):
        raise HistoryDataError("archive URL is outside the frozen official source")
    parsed = urlparse(url)
    rel = Path(parsed.path.removeprefix("/data/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise HistoryDataError("unsafe archive path")
    return rel


def _get_bytes(session: requests.Session, url: str) -> bytes:
    if not safe_archive_url(url):
        raise HistoryDataError("refusing non-official archive URL")
    response = session.get(url, timeout=30, allow_redirects=False, stream=True)
    try:
        if response.is_redirect or response.is_permanent_redirect:
            raise HistoryDataError("archive redirect refused")
        response.raise_for_status()
        chunks, size = [], 0
        iterator = response.iter_content(chunk_size=1 << 20) if hasattr(response, "iter_content") else (response.content,)
        for chunk in iterator:
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise HistoryDataError("archive download exceeds the size cap")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


def fetch_verified(spec: ArchiveSpec, root: Path,
                   session: requests.Session | None = None) -> dict[str, Any]:
    """Fetch one archive and sibling checksum, then atomically keep verified bytes."""
    session = session or requests.Session()
    checksum = _get_bytes(session, spec.checksum_url)
    payload = _get_bytes(session, spec.url)
    filename = Path(urlparse(spec.url).path).name
    digest = verify_bytes(payload, checksum, filename)
    destination = root / archive_relative_path(spec.url)
    resolved_root = root.resolve()
    resolved_destination = destination.resolve()
    if resolved_root != resolved_destination and resolved_root not in resolved_destination.parents:
        raise HistoryDataError("archive destination escapes its root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    checksum_path = destination.with_name(destination.name + ".CHECKSUM")
    if destination.exists():
        if not checksum_path.exists():
            raise HistoryDataError("stored archive has no checksum sibling")
        existing = verify_bytes(destination.read_bytes(), checksum_path.read_bytes(), filename)
        if existing != digest:
            raise HistoryDataError("refusing to overwrite frozen archive bytes")
        return {"path": destination.as_posix(), "sha256": existing,
                "bytes": destination.stat().st_size, "reused": True}
    if checksum_path.exists() and parse_checksum(checksum_path.read_bytes(), filename) != digest:
        raise HistoryDataError("orphan checksum disagrees with frozen archive")
    checksum_partial = checksum_path.with_name(checksum_path.name + ".part")
    payload_partial = destination.with_name(destination.name + ".part")
    checksum_partial.write_bytes(checksum)
    payload_partial.write_bytes(payload)
    checksum_partial.replace(checksum_path)  # sibling exists before archive becomes visible
    payload_partial.replace(destination)     # archive is the commit marker for the pair
    return {"path": destination.as_posix(), "sha256": digest, "bytes": len(payload), "reused": False}


def _csv_rows(payload: bytes) -> list[list[str]]:
    """Read exactly one bounded, plain CSV member from a verified zip."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise HistoryDataError("archive is not a valid zip") from exc
    members = [item for item in archive.infolist() if not item.is_dir()]
    if len(members) != 1:
        raise HistoryDataError("archive must contain exactly one CSV member")
    member = members[0]
    member_path = Path(member.filename)
    if (member_path.is_absolute() or ".." in member_path.parts
            or member_path.suffix.lower() != ".csv" or member.flag_bits & 0x1):
        raise HistoryDataError("unsafe archive member")
    if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise HistoryDataError("archive member is too large")
    if member.compress_size and member.file_size / member.compress_size > 1000:
        raise HistoryDataError("archive compression ratio is unsafe")
    try:
        raw = archive.read(member).decode("utf-8-sig")
    except (RuntimeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise HistoryDataError("archive CSV cannot be read") from exc
    rows = list(csv.reader(io.StringIO(raw, newline="")))
    if not rows or any(not row for row in rows):
        raise HistoryDataError("archive CSV is empty or malformed")
    return rows


def _number(value: str, name: str, *, positive: bool = False) -> float:
    if not isinstance(value, str) or re.fullmatch(
            r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None:
        raise HistoryDataError(f"{name} is not a strict ASCII number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoryDataError(f"{name} is not numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise HistoryDataError(f"{name} is invalid")
    return result


def _ascii_uint(value: str, name: str) -> int:
    if (not value or any(char not in "0123456789" for char in value)
            or (len(value) > 1 and value.startswith("0"))):
        raise HistoryDataError(f"{name} must be an ASCII unsigned integer")
    return int(value)


def _timestamp_ms(value: str, name: str) -> int:
    raw = _ascii_uint(value, name)
    digits = len(value)
    if digits == 13:
        return raw
    if digits == 16:
        return raw // 1000
    raise HistoryDataError(f"{name} must use 13-digit ms or 16-digit us")


def _period_bounds(spec: ArchiveSpec) -> tuple[int, int]:
    if spec.frequency == "daily":
        start = datetime.strptime(spec.period, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    elif spec.frequency == "monthly":
        start = datetime.strptime(spec.period, "%Y-%m").replace(tzinfo=timezone.utc)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        raise HistoryDataError("unsupported archive frequency")
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def parse_kline_archive(payload: bytes, spec: ArchiveSpec) -> KlineArchive:
    """Parse one archive without inventing bars across documented source outages."""
    rows = _csv_rows(payload)
    if rows[0] == KLINE_HEADER:
        rows = rows[1:]
    parsed, quarantined, observed = [], [], set()
    previous, archive_width = -1, None
    start, end = _period_bounds(spec)
    for row in rows:
        if len(row) != 12:
            raise HistoryDataError("kline row must have 12 columns")
        _ascii_uint(row[0], "kline open")
        _ascii_uint(row[6], "kline close")
        widths = {len(row[0]), len(row[6])}
        if len(widths) != 1 or (archive_width is not None and archive_width not in widths):
            raise HistoryDataError("kline archive mixes timestamp units")
        archive_width = next(iter(widths))
        if archive_width == 16 and (int(row[0]) % 1000 != 0 or int(row[6]) % 1000 != 999):
            raise HistoryDataError("microsecond kline residue is not canonical")
        opened, closed = _timestamp_ms(row[0], "kline open"), _timestamp_ms(row[6], "kline close")
        if opened <= previous or opened % 3_600_000 != 0:
            raise HistoryDataError("kline opens must be unique, increasing and hourly")
        if opened < start or opened >= end:
            raise HistoryDataError("kline row falls outside its declared period")
        if closed >= SEAL_MS:
            raise HistoryDataError("kline close violates the signed seal")
        open_price = _number(row[1], "open", positive=True)
        high = _number(row[2], "high", positive=True)
        low = _number(row[3], "low", positive=True)
        close = _number(row[4], "close", positive=True)
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            raise HistoryDataError("kline OHLC ordering is invalid")
        observed.add(opened)
        if closed - opened != 3_599_999:
            quarantined.append({"open_time": opened, "close_time": closed,
                                "reason": "noncanonical_close"})
        else:
            parsed.append({"open_time": opened, "close_time": closed, "open": open_price,
                           "high": high, "low": low, "close": close})
        previous = opened
    if not parsed:
        raise HistoryDataError("kline archive contains no data rows")
    expected = range(start, end, 3_600_000)
    missing = [{"open_time": opened, "reason": "missing_source_hour"}
               for opened in expected if opened not in observed]
    return KlineArchive(parsed, sorted(quarantined + missing, key=lambda item: item["open_time"]),
                        (end - start) // 3_600_000)


def parse_kline_zip(payload: bytes, spec: ArchiveSpec) -> list[dict[str, Any]]:
    """Fail-closed compatibility view that cannot discard a data-quality ledger."""
    archive = parse_kline_archive(payload, spec)
    if archive.incomplete:
        raise HistoryDataError("kline archive is incomplete; use parse_kline_archive with its ledger")
    return archive.rows


def validate_common_hour_coverage(spot_rows: Iterable[dict[str, Any]],
                                  mark_rows: Iterable[dict[str, Any]],
                                  start_ms: int, end_ms: int) -> dict[str, Any]:
    """Enforce the signed annual common-grid and continuous-gap gates."""
    if start_ms % HOUR_MS or end_ms % HOUR_MS or end_ms <= start_ms + HOUR_MS:
        raise HistoryDataError("common-hour coverage bounds must span aligned hourly timestamps")

    def times(rows: Iterable[dict[str, Any]], leg: str) -> set[int]:
        values = [int(row["open_time"]) for row in rows]
        if len(values) != len(set(values)) or any(t < start_ms or t >= end_ms or t % HOUR_MS
                                                  for t in values):
            raise HistoryDataError(f"{leg} rows violate unique in-range hourly coverage")
        return set(values)

    spot, mark = times(spot_rows, "spot"), times(mark_rows, "mark")
    expected = list(range(start_ms, end_ms, HOUR_MS))
    common = spot & mark
    eligible = sum(t in common and t - HOUR_MS in common for t in expected[1:])
    denominator = len(expected) - 1
    coverage = eligible / denominator
    longest = current = 0
    for t in expected:
        if t not in common:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    if coverage < MIN_COMMON_COVERAGE:
        raise HistoryDataError("consecutive common-hour coverage is below the signed 99.0% gate")
    if longest > MAX_INCOMPLETE_RUN_HOURS:
        raise HistoryDataError("continuous incomplete run exceeds the signed 24-hour gate")
    return {"expected_return_hours": denominator, "eligible_return_hours": eligible,
            "coverage": coverage, "longest_incomplete_run_hours": longest}


def required_mark_open_time(funding_time_ms: int) -> int:
    """Open time of the latest 1h mark bar closed strictly before a funding event."""
    return ((funding_time_ms - HOUR_MS) // HOUR_MS) * HOUR_MS


def parse_funding_zip(payload: bytes, spec: ArchiveSpec) -> list[dict[str, Any]]:
    rows = _csv_rows(payload)
    expected = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    if rows[0] != expected:
        raise HistoryDataError("funding CSV header is not the frozen schema")
    parsed, previous = [], -1
    for row in rows[1:]:
        if len(row) != 3:
            raise HistoryDataError("funding row must have 3 columns")
        timestamp = _timestamp_ms(row[0], "funding time")
        interval = _ascii_uint(row[1], "funding interval")
        if timestamp <= previous or timestamp >= SEAL_MS or interval <= 0:
            raise HistoryDataError("funding event violates ordering, interval or signed seal")
        rate = _number(row[2], "funding rate")
        if abs(rate) > 1:
            raise HistoryDataError("funding rate magnitude exceeds the schema bound")
        parsed.append({"funding_time": timestamp, "interval_hours": interval, "funding_rate": rate})
        previous = timestamp
    if not parsed:
        raise HistoryDataError("funding archive contains no events")
    start, end = _period_bounds(spec)
    if any(not start <= row["funding_time"] < end for row in parsed):
        raise HistoryDataError("funding event falls outside its declared period")
    return parsed


def validate_hourly_contiguous(rows: Iterable[dict[str, Any]]) -> None:
    previous = None
    count = 0
    for row in rows:
        opened = int(row["open_time"])
        if previous is not None and opened != previous + 3_600_000:
            raise HistoryDataError("hourly series contains a gap or overlap")
        previous = opened
        count += 1
    if not count:
        raise HistoryDataError("hourly series is empty")


def manifest_document(contract: dict[str, Any]) -> dict[str, Any]:
    specs = build_manifest(contract)
    return {
        "format": "zb-carry01-archive-manifest/1",
        "contract_digest_sha256": contract["contract_digest_sha256"],
        "sealed_end_exclusive": contract["data"]["sealed_development_end_exclusive"],
        "archives": [asdict(spec) for spec in specs],
        "archive_count": len(specs),
        "result_fields_pending_fetch": ["sha256", "bytes"],
    }
