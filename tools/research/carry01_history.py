"""Frozen archive manifest and checksum gate for the signed CARRY-01 study.

This module plans inputs and verifies bytes only.  It deliberately does not calculate
returns; result generation belongs to the separately tested historical runner.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


BASE = "https://data.binance.vision/data"
SEAL = datetime(2026, 9, 28, tzinfo=timezone.utc)
PREREG = Path("research_evidence/prereg/carry01_cash_and_carry.v1.json")


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
