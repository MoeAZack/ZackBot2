"""Checksum-bound v6 input for C258; no outcome evaluation at import time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from decimal import Decimal
import hashlib
import json
import math
import zipfile
import io

from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C
from tools.research.authority_v6_runner import _read_record_rows
from tools.research import logical_trade_hours as L


class VerifiedSourceAbsence(C.IncompleteAuthority):
    """The protected manifest contains no applicable records; no integrity failure."""


def _read_window_rows(store: Path, record: Mapping, *, new: bool,
                      start_ms: int, end_ms: int):
    """Hash complete Vision bytes, then parse economic fields only in-window."""
    if record.get("source", {}).get("kind") != "binance-vision-archive":
        from tools.research import authority_v6 as V6
        # Synthetic unit seams retain their explicit non-archive reader. Actual
        # JSON sources are hash-bound and timestamp-clipped before conversion.
        if not str(record.get("path", "")).endswith(".json"):
            return _read_record_rows(store, record, new=new)
        if record.get("source", {}).get("kind") not in {V6.M.FUNDING_API_KIND, V6.M.KLINE_API_KIND}:
            raise C.IncompleteAuthority("unsupported protected JSON source kind")
        rel = record["path"]
        raw = V6._safe_store_path(store, rel).read_bytes()
        expected = record["raw_sha256" if new else "sha256"]
        if hashlib.sha256(raw).hexdigest() != expected:
            raise C.IncompleteAuthority("archive source bytes changed")
        meta = V6.M.archive_meta(rel)
        payload = json.loads(raw, parse_float=str, parse_int=str)
        if not isinstance(payload, list):
            raise C.IncompleteAuthority("API source is not a row list")
        selected = []
        for row in payload:
            timestamp = int(row["fundingTime"] if isinstance(row, Mapping) else row[0])
            if start_ms <= timestamp < end_ms:
                if isinstance(row, Mapping):
                    if row.get("symbol", meta["symbol"]) != meta["symbol"]:
                        raise C.IncompleteAuthority("API source symbol does not match protected path")
                    row = {**row, "fundingTime": timestamp}
                selected.append(row)
        return meta, selected
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


@dataclass(frozen=True)
class FundingObservation:
    nominal_ms: int
    rate: float
    interval_hours: int | None


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

    This source supplies the fixed executable replay. It cannot accept caller
    market rows or replace the frozen universe.
    """

    def __init__(self, pins: C.AuthorityPins, store: Path, *, root: Path = C.ROOT):
        docs = C.validate_authority_pins(pins, root=root)
        L.validate_clarification(C.ROOT)
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
        from tools.research import manifest as M
        normalized = []
        for original, new in self._records:
            record = dict(original)
            fields = ("symbol", "series", "interval")
            if new and any(key not in record for key in fields):
                raise C.IncompleteAuthority("v6 source record identity is incomplete")
            if (not new and any(key not in record for key in fields)
                    or record.get("source", {}).get("kind") in {
                        M.ARCHIVE_KIND, M.FUNDING_API_KIND, M.KLINE_API_KIND}):
                # v5 FILE_KEYS deliberately omit series/symbol aliases in some
                # producers. Resolve the exact frozen archive path, not a guess.
                meta = M.archive_meta(record["path"])
                for key in fields:
                    if key in record and record[key] != meta[key]:
                        raise C.IncompleteAuthority("v5 path/record identity mismatch")
                    record[key] = meta[key]
            normalized.append((record, new))
        self._records = tuple(normalized)

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

    def boundary_open(self, *, symbol: str, fold: str) -> C.OpenBar:
        """Permit only the exact fold-end open for an already exposed due exit."""
        _, boundary = frozen_window(fold)
        found = []
        for record, new in self._records:
            if record["symbol"] != symbol or L.trade_window(
                    record, new=new, start_ms=boundary, end_ms=boundary+1) is None:
                continue
            from tools.research import manifest as M
            meta = M.archive_meta(record["path"])
            if not meta["month_start_ms"] <= boundary < meta["month_end_ms"]:
                continue
            from tools.research import authority_v6 as V6
            raw = V6._safe_store_path(self._store, record["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record["raw_sha256" if new else "sha256"]:
                raise C.IncompleteAuthority("boundary source bytes changed")
            expected = record["raw_sha256" if new else "sha256"]
            if record.get('source', {}).get('kind') == 'binance-vision-archive':
                if Path(str(V6._safe_store_path(self._store, record['path']))+'.ok').read_text().strip() != expected:
                    raise C.IncompleteAuthority('boundary source checksum sidecar changed')
                if new:
                    checksum = Path(str(V6._safe_store_path(self._store, record['path']))+'.CHECKSUM').read_bytes()
                    if (hashlib.sha256(checksum).hexdigest() != record['source']['checksum_raw_sha256']
                            or V6.parse_official_checksum(checksum, Path(record['path']).name) != expected):
                        raise C.IncompleteAuthority('boundary official checksum changed')
            if record["path"].endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    names = [name for name in archive.namelist() if name.endswith(".csv")]
                    if len(names) != 1:
                        raise C.IncompleteAuthority("boundary archive is ambiguous")
                    rows = [line.decode("utf-8").split(",")
                            for line in archive.read(names[0]).splitlines()]
                rows = [row for row in rows if row and row[0].isdigit()]
            else:
                rows = json.loads(raw, parse_int=str, parse_float=str)
            for row in rows:
                if int(row[0]) == boundary:
                    found.append(C.OpenBar(boundary, float(row[1])))
        if len(found) != 1:
            raise C.IncompleteAuthority("missing or duplicate exact boundary open")
        found[0].validate()
        return found[0]

    def candles(self, *, symbol: str, fold: str, start_ms: int, end_ms: int,
                allow_gaps: bool = False) -> tuple[C.Bar, ...]:
        source = self.load(symbol=symbol, stream="klines", fold=fold,
                           start_ms=start_ms, end_ms=end_ms, allow_gaps=allow_gaps)
        bars = []
        for row in source.rows:
            if len(row) != 12:
                raise C.IncompleteAuthority("invalid canonical candle width")
            bar = C.Bar(int(row[0]), *(float(row[index]) for index in (1, 2, 3, 4, 7)))
            bar.validate()
            if int(row[6]) != bar.open_ms+C.HOUR_MS-1:
                raise C.IncompleteAuthority("canonical candle close time changed")
            bars.append(bar)
        return tuple(bars)

    def marks(self, *, symbol: str, fold: str, start_ms: int, end_ms: int,
              allow_gaps: bool = False) -> tuple[C.MarkClose, ...]:
        source = self.load(symbol=symbol, stream="markPriceKlines", fold=fold,
                           start_ms=start_ms, end_ms=end_ms, allow_gaps=allow_gaps)
        marks = []
        for row in source.rows:
            if len(row) != 12 or int(row[6]) != int(row[0])+C.HOUR_MS-1:
                raise C.IncompleteAuthority("invalid canonical mark close")
            price = float(row[4])
            if not math.isfinite(price) or price <= 0:
                raise C.IncompleteAuthority("invalid source mark price")
            # Keep Binance's actual close timestamp, not its candle open time.
            marks.append(C.MarkClose(int(row[6]), price))
        return tuple(marks)

    def funding(self, *, symbol: str, fold: str, start_ms: int, end_ms: int
                ) -> tuple[FundingObservation, ...]:
        source = self.load(symbol=symbol, stream="fundingRate", fold=fold,
                           start_ms=start_ms, end_ms=end_ms)
        events = []
        for row in source.rows:
            if len(row) == 3:
                interval, rate = int(row[1]), float(row[2])
                if interval <= 0 or interval > 24:
                    raise C.IncompleteAuthority("invalid declared funding interval")
            elif len(row) == 2:
                # API rates do not declare cadence; never invent an 8h default.
                interval, rate = None, float(row[1])
            else:
                raise C.IncompleteAuthority("invalid funding observation")
            if not math.isfinite(rate):
                raise C.IncompleteAuthority("non-finite source funding")
            events.append(FundingObservation(int(row[0]), rate, interval))
        return tuple(events)

    def load(self, *, symbol: str, stream: str, fold: str,
             start_ms: int, end_ms: int, allow_gaps: bool = False) -> SourceRows:
        lo, hi = frozen_window(fold)
        if (type(start_ms) is not int or type(end_ms) is not int
                or not lo <= start_ms < end_ms <= hi):
            raise C.IncompleteAuthority("market request escapes frozen fold")
        if stream not in ("klines", "markPriceKlines", "fundingRate"):
            raise C.IncompleteAuthority("unsupported C258 source stream")
        selected = []
        rows: dict[int, tuple] = {}
        minutes: dict[int, tuple] = {}
        for record, new in self._records:
            if record.get("symbol") != symbol or record.get("series") != stream:
                continue
            window = (L.trade_window(record, new=new, start_ms=start_ms, end_ms=end_ms)
                      if stream == 'klines' else (start_ms,end_ms)
                      if stream == 'fundingRate' or record.get('interval') == '1h' else None)
            if window is None:
                continue
            read_start, read_end = window
            # Reject any record that intersects the requested range ambiguously.
            first = record.get("observed_first_ms") if new else record.get("first_open_ms")
            last = record.get("observed_last_ms") if new else record.get("last_open_ms")
            if first is not None and last is not None and (last < read_start or first >= read_end):
                continue
            if first is None or last is None:
                from tools.research import manifest as M
                period = M.archive_meta(record["path"])
                period_start = record.get("declared_start_ms", period["month_start_ms"])
                period_end = record.get("declared_end_ms", period["month_end_ms"])
                if period_end <= read_start or period_start >= read_end:
                    continue
            try:
                _, parsed = _read_window_rows(self._store, record, new=new,
                                              start_ms=read_start, end_ms=read_end)
            except C.IncompleteAuthority:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                raise C.IncompleteAuthority("checksum-bound source integrity failure") from exc
            digest = record["raw_sha256" if new else "sha256"]
            selected.append((record["path"], digest))
            for item in parsed:
                if isinstance(item, Mapping):
                    stamp = item["fundingTime"]
                    row = (stamp, str(item["fundingRate"]))
                else:
                    row = tuple(item)
                    stamp = int(row[0])
                if read_start <= stamp < read_end:
                    target = minutes if stream == 'klines' and not new else rows
                    if stamp in target:
                        raise C.IncompleteAuthority("overlapping active authority timestamps")
                    target[stamp] = row
        if minutes:
            try:
                for row in L.exact_hours(minutes[t] for t in sorted(minutes)):
                    if row[0] in rows:
                        raise ValueError('overlapping logical source hours')
                    rows[row[0]] = row
            except ValueError as exc:
                raise C.IncompleteAuthority(str(exc)) from exc
        if (not allow_gaps and stream != "fundingRate"
                and tuple(sorted(rows)) != tuple(range(start_ms, end_ms, C.HOUR_MS))):
            raise C.IncompleteAuthority("missing canonical C258 source candle")
        if not selected:
            raise VerifiedSourceAbsence("no checksum-bound source records")
        return SourceRows(symbol, stream, fold, start_ms, end_ms,
                          tuple(rows[t] for t in sorted(rows)), tuple(sorted(selected)))
