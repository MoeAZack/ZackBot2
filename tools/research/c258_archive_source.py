"""Checksum-bound v6 input for C258; no outcome evaluation at import time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from decimal import Decimal
import hashlib
import json

from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C
from tools.research.authority_v6_runner import _read_record_rows


def _read_window_rows(store: Path, record: Mapping, *, new: bool,
                      start_ms: int, end_ms: int):
    """Hash complete Vision bytes, then parse economic fields only in-window."""
    if record.get("source", {}).get("kind") != "binance-vision-archive":
        return _read_record_rows(store, record, new=new)
    from tools.research import authority_v6 as V6
    rel = record["path"]
    target = V6._safe_store_path(store, rel)
    raw, published, csv_raw = V6.M.read_archive(str(target), rel)
    expected = record["raw_sha256" if new else "sha256"]
    if published != expected or hashlib.sha256(raw).hexdigest() != expected:
        raise C.IncompleteAuthority("archive source bytes changed")
    meta = V6.M.archive_meta(rel)
    header = V6.M.FUNDING_HEADER if meta["series"] == "fundingRate" else V6.M.KLINE_HEADER
    selected = []
    previous = None
    for index, line in enumerate(csv_raw.splitlines()):
        if index == 0 and line == header:
            continue
        # Only the timestamp is inspected outside the requested economic window.
        try:
            timestamp = int(line.split(b",", 1)[0])
        except ValueError as exc:
            raise C.IncompleteAuthority("invalid archive timestamp") from exc
        if previous is not None and timestamp <= previous:
            raise C.IncompleteAuthority("duplicate/unordered source timestamp")
        previous = timestamp
        if start_ms <= timestamp < end_ms:
            selected.append(line)
    clipped = b"\n".join(selected) + (b"\n" if selected else b"")
    return meta, V6.M.parse_archive_csv(rel, clipped, meta)


@dataclass(frozen=True)
class SourceRows:
    symbol: str
    stream: str
    fold: str
    start_ms: int
    end_ms: int
    rows: tuple[tuple, ...]
    records: tuple[tuple[str, str], ...]


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def frozen_window(fold: str) -> tuple[int, int]:
    splits = C.load_frozen_contract()["splits"]
    windows = {"development": splits["development"]}
    windows.update({f"wf{i+1}": value for i, value in enumerate(splits["forward_folds"])})
    if fold not in windows:
        raise C.IncompleteAuthority("unknown or sealed C258 fold")
    return tuple(_ms(part) for part in windows[fold].split("/"))


class C258ArchiveSource:
    """Opens protected authority first, then only explicitly bound archive records.

    This source is a data dependency of the pending replay, not an executable
    evaluator. It cannot accept caller market rows or replace the frozen universe.
    """

    def __init__(self, pins: C.AuthorityPins, store: Path, *, root: Path = C.ROOT):
        docs = C.validate_authority_pins(pins, root=root)
        self._store = Path(store)
        self._root = root
        self._head = pins.protected_head
        self._manifest = docs["manifest_v6"]
        self._pit = docs["pit_v9"]
        self._filters = docs["filter_v4"]
        self._records = tuple((record, False) for record in self._manifest["active_base_files_v5"])
        self._records += tuple((record, True) for record in self._manifest["files_v6"])
        paths = [record["path"] for record, _ in self._records]
        if len(paths) != len(set(paths)):
            raise C.IncompleteAuthority("duplicate active archive path")

    def membership_and_filters(self, *, fold: str, decision_ms: int
                               ) -> tuple[tuple[str, ...], dict[str, tuple[C.Filters, Decimal]]]:
        """Derive membership from the frozen PIT order and raw pre-fold snapshot."""
        lo, hi = frozen_window(fold)
        if not lo <= decision_ms < hi:
            raise C.IncompleteAuthority("membership decision escapes frozen fold")
        binding = "development_eligible_segment" if fold == "development" else fold
        capture = self._filters["fold_bindings"][binding]
        matches = [row for row in self._filters["snapshots"] if row["captured_at"] == capture]
        if len(matches) != 1:
            raise C.IncompleteAuthority("ambiguous frozen filter capture")
        snapshot = matches[0]
        if fold != "development" and (_ms(capture) >= lo or _ms(snapshot["server_time"]) >= lo):
            raise C.IncompleteAuthority("filter snapshot is not pre-fold")
        if decision_ms <= max(_ms(capture), _ms(snapshot["server_time"])):
            return (), {}
        raw = C._git(self._root, "show", f"{self._head}:{snapshot['path']}", binary=True)
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != snapshot["sha256"]:
            raise C.IncompleteAuthority("protected filter snapshot bytes changed")
        payload = json.loads(raw)
        from tools.research import authority_v6 as V6
        eligible = V6._snapshot_eligible(payload)
        weeks = [week for week in self._pit["weeks"] if week["monday_ms"] <= decision_ms]
        if not weeks:
            raise C.IncompleteAuthority("missing causal PIT cell")
        monday = max(week["monday_ms"] for week in weeks)
        selected = [week for week in weeks if week["monday_ms"] == monday]
        if len(selected) != 1:
            raise C.IncompleteAuthority("duplicate causal PIT cell")
        ranked = selected[0]["books"]["crypto"]["members"]
        if len(ranked) != len(set(ranked)):
            raise C.IncompleteAuthority("duplicate PIT member")
        members = tuple(symbol for symbol in ranked if symbol in eligible)
        by_symbol = {}
        for row in payload["symbols"]:
            symbol = row["symbol"]
            if symbol not in members:
                continue
            if symbol in by_symbol:
                raise C.IncompleteAuthority("duplicate snapshot symbol")
            rules = {rule["filterType"]: rule for rule in row["filters"]}
            if len(rules) != len(row["filters"]):
                raise C.IncompleteAuthority("duplicate exchange filter")
            lot = rules["MARKET_LOT_SIZE"]
            filters = C.Filters(Decimal(lot["stepSize"]), Decimal(lot["minQty"]),
                                Decimal(lot["maxQty"]), Decimal(str(
                                    rules["MIN_NOTIONAL"].get("notional", rules["MIN_NOTIONAL"].get("minNotional")))))
            filters.validate()
            tick = Decimal(rules["PRICE_FILTER"]["tickSize"])
            if not tick.is_finite() or tick <= 0:
                raise C.IncompleteAuthority("invalid price tick")
            by_symbol[symbol] = (filters, tick)
        return members, by_symbol

    def load(self, *, symbol: str, stream: str, fold: str,
             start_ms: int, end_ms: int) -> SourceRows:
        lo, hi = frozen_window(fold)
        if (type(start_ms) is not int or type(end_ms) is not int
                or not lo <= start_ms < end_ms <= hi):
            raise C.IncompleteAuthority("market request escapes frozen fold")
        if stream not in ("klines", "markPriceKlines", "fundingRate"):
            raise C.IncompleteAuthority("unsupported C258 source stream")
        selected = []
        rows: dict[int, tuple] = {}
        for record, new in self._records:
            if (record.get("symbol") != symbol or record.get("series") != stream
                    or (stream != "fundingRate" and record.get("interval") != "1h")):
                continue
            # Reject any record that intersects the requested range ambiguously.
            first = record.get("observed_first_ms") if new else record.get("first_open_ms")
            last = record.get("observed_last_ms") if new else record.get("last_open_ms")
            if first is not None and last is not None and (last < start_ms or first >= end_ms):
                continue
            _, parsed = _read_window_rows(self._store, record, new=new,
                                          start_ms=start_ms, end_ms=end_ms)
            digest = record["raw_sha256" if new else "sha256"]
            selected.append((record["path"], digest))
            for item in parsed:
                if isinstance(item, Mapping):
                    stamp = item["fundingTime"]
                    row = (stamp, str(item["fundingRate"]))
                else:
                    row = tuple(item)
                    stamp = int(row[0])
                if start_ms <= stamp < end_ms:
                    if stamp in rows:
                        raise C.IncompleteAuthority("overlapping active authority timestamps")
                    rows[stamp] = row
        if stream != "fundingRate" and tuple(sorted(rows)) != tuple(range(start_ms, end_ms, C.HOUR_MS)):
            raise C.IncompleteAuthority("missing canonical C258 source candle")
        if not selected:
            raise C.IncompleteAuthority("no checksum-bound source records")
        return SourceRows(symbol, stream, fold, start_ms, end_ms,
                          tuple(rows[t] for t in sorted(rows)), tuple(sorted(selected)))
