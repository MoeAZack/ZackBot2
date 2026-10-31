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


def canonical_digest(contract: dict[str, Any]) -> str:
    material = dict(contract)
    material.pop("contract_digest_sha256", None)
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_signed_contract(path: Path = PREREG) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("signed") is not True or not str(contract.get("status", "")).startswith("SIGNED"):
        raise HistoryDataError("CARRY-01 contract is not signed")
    if contract.get("contract_digest_sha256") != canonical_digest(contract):
        raise HistoryDataError("CARRY-01 contract digest mismatch")
    end = datetime.fromisoformat(contract["data"]["sealed_development_end_exclusive"].replace("Z", "+00:00"))
    if end != SEAL:
        raise HistoryDataError("unexpected CARRY-01 data seal")
    return contract


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
    if contract.get("contract_digest_sha256") != canonical_digest(contract):
        raise HistoryDataError("contract changed after signing")
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


def parse_checksum(raw: bytes) -> str:
    try:
        token = raw.decode("ascii").strip().split()[0].lower()
    except (UnicodeDecodeError, IndexError) as exc:
        raise HistoryDataError("unreadable archive checksum") from exc
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise HistoryDataError("invalid archive checksum")
    return token


def verify_bytes(payload: bytes, checksum: bytes) -> str:
    expected = parse_checksum(checksum)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise HistoryDataError("archive checksum mismatch")
    return actual


def safe_archive_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname == "data.binance.vision"
            and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
            and parsed.path.startswith("/data/") and not parsed.query and not parsed.fragment)


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
    response = session.get(url, timeout=30, allow_redirects=False)
    if response.is_redirect or response.is_permanent_redirect:
        raise HistoryDataError("archive redirect refused")
    response.raise_for_status()
    return response.content


def fetch_verified(spec: ArchiveSpec, root: Path,
                   session: requests.Session | None = None) -> dict[str, Any]:
    """Fetch one archive and sibling checksum, then atomically keep verified bytes."""
    session = session or requests.Session()
    checksum = _get_bytes(session, spec.checksum_url)
    payload = _get_bytes(session, spec.url)
    digest = verify_bytes(payload, checksum)
    destination = root / archive_relative_path(spec.url)
    resolved_root = root.resolve()
    resolved_destination = destination.resolve()
    if resolved_root != resolved_destination and resolved_root not in resolved_destination.parents:
        raise HistoryDataError("archive destination escapes its root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.write_bytes(payload)
    partial.replace(destination)
    destination.with_name(destination.name + ".CHECKSUM").write_bytes(checksum)
    return {"path": destination.as_posix(), "sha256": digest, "bytes": len(payload)}


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
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoryDataError(f"{name} is not numeric") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise HistoryDataError(f"{name} is invalid")
    return result


def parse_kline_zip(payload: bytes) -> list[dict[str, Any]]:
    rows = _csv_rows(payload)
    if rows[0] and not rows[0][0].lstrip("-").isdigit():
        rows = rows[1:]
    parsed, previous = [], -1
    for row in rows:
        if len(row) != 12:
            raise HistoryDataError("kline row must have 12 columns")
        try:
            opened, closed = int(row[0]), int(row[6])
        except ValueError as exc:
            raise HistoryDataError("kline timestamps must be integers") from exc
        if opened <= previous or opened % 3_600_000 != 0:
            raise HistoryDataError("kline opens must be unique, increasing and hourly")
        if closed - opened != 3_599_999 or closed >= SEAL_MS:
            raise HistoryDataError("kline close violates the hourly grid or signed seal")
        open_price = _number(row[1], "open", positive=True)
        high = _number(row[2], "high", positive=True)
        low = _number(row[3], "low", positive=True)
        close = _number(row[4], "close", positive=True)
        if high < max(open_price, low, close) or low > min(open_price, high, close):
            raise HistoryDataError("kline OHLC ordering is invalid")
        parsed.append({"open_time": opened, "close_time": closed, "open": open_price,
                       "high": high, "low": low, "close": close})
        previous = opened
    if not parsed:
        raise HistoryDataError("kline archive contains no data rows")
    return parsed


def parse_funding_zip(payload: bytes) -> list[dict[str, Any]]:
    rows = _csv_rows(payload)
    expected = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    if rows[0] != expected:
        raise HistoryDataError("funding CSV header is not the frozen schema")
    parsed, previous = [], -1
    for row in rows[1:]:
        if len(row) != 3:
            raise HistoryDataError("funding row must have 3 columns")
        try:
            timestamp = int(row[0])
            interval = int(row[1])
        except ValueError as exc:
            raise HistoryDataError("funding time and interval must be integers") from exc
        if timestamp <= previous or timestamp >= SEAL_MS or interval <= 0:
            raise HistoryDataError("funding event violates ordering, interval or signed seal")
        parsed.append({"funding_time": timestamp, "interval_hours": interval,
                       "funding_rate": _number(row[2], "funding rate")})
        previous = timestamp
    if not parsed:
        raise HistoryDataError("funding archive contains no events")
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
