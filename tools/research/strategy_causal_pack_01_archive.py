"""Checksum-bound raw archive input for STRATEGY-CAUSAL-PACK-01.

This module is deliberately separate from the executable runner.  It reuses the
repository archive and PIT-universe authorities and has no CLI, promotion path,
or implicit store.  The runner may call it only after the contract merge guard.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from tools.research import manifest as M
from tools.research import pit as P
from tools.research import universe as U


HOUR_MS = 3_600_000
CUTOFF_MS = 1_790_553_600_000  # 2026-09-28T00:00:00Z, exclusive
INTERVAL_MS = {"1h": HOUR_MS, "4h": 4 * HOUR_MS}


class ArchiveIncomplete(RuntimeError):
    """The complete frozen candidate must be refused, never reduced."""


@dataclass(frozen=True)
class RawKline:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class RawFunding:
    time_ms: int
    interval_hours: int
    rate: float


def load_authorities(manifest_path: str | Path, universe_path: str | Path) -> tuple[dict, dict]:
    """Load and bind the repository's authoritative manifest and PIT universe."""
    try:
        manifest = M.load(str(manifest_path))
        with Path(universe_path).open(encoding="utf-8") as fh:
            pit = json.load(fh, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, ValueError, M.ManifestError) as exc:
        raise ArchiveIncomplete(f"authority load failed: {exc}") from exc
    if (manifest.get("loader_version") != M.ARCHIVE_LOADER
            or manifest.get("source_class") != "archive-verified"
            or manifest.get("data_root") != M.ARCHIVE_ROOT
            or manifest.get("survivor_only") is not False):
        raise ArchiveIncomplete("manifest is not the checksum-verified Binance archive authority")
    if (pit.get("format") != U.FORMAT or pit.get("universe_id") != U.UNIVERSE_ID
            or pit.get("digest") != U.digest_of(pit)):
        raise ArchiveIncomplete("PIT universe identity/digest does not match its content")
    if pit.get("manifest_digest") != manifest["digest"]:
        raise ArchiveIncomplete("PIT universe is not bound to this archive manifest")
    return manifest, pit


class RawArchiveAdapter:
    """Read only manifest-authorized CORE-20 rows, failing closed on any gap."""

    def __init__(self, manifest: dict, pit_universe: dict, store: str | Path):
        try:
            M.validate(manifest)
        except M.ManifestError as exc:
            raise ArchiveIncomplete(f"manifest invalid: {exc}") from exc
        if (manifest.get("loader_version") != M.ARCHIVE_LOADER
                or manifest.get("source_class") != "archive-verified"
                or manifest.get("data_root") != M.ARCHIVE_ROOT
                or manifest.get("survivor_only") is not False):
            raise ArchiveIncomplete("manifest is not archive-verified binance_um data")
        if (pit_universe.get("format") != U.FORMAT
                or pit_universe.get("universe_id") != U.UNIVERSE_ID
                or pit_universe.get("digest") != U.digest_of(pit_universe)
                or pit_universe.get("manifest_digest") != manifest["digest"]):
            raise ArchiveIncomplete("PIT universe digest/manifest binding failed")
        self.manifest = manifest
        self.pit_universe = pit_universe
        self.store = str(Path(store))
        try:
            # The repository PIT dataset owns manifest overlap refusal,
            # checksum/parser reuse, and the exact universe/manifest binding.
            self.dataset = P.Dataset(manifest, self.store, pit_universe, books=["crypto"])
        except (M.ManifestError, P.M.ManifestError, P.PITError) as exc:
            raise ArchiveIncomplete(f"PIT dataset initialization failed: {exc}") from exc

    def core20_at(self, time_ms: int) -> tuple[str, ...]:
        weeks = [week for week in self.pit_universe["weeks"] if week["monday_ms"] <= time_ms]
        if not weeks:
            raise ArchiveIncomplete(f"no PIT universe observation at {time_ms}")
        members = tuple(max(weeks, key=lambda week: week["monday_ms"])["books"]["crypto"]["members"][:20])
        if len(members) != 20 or len(set(members)) != 20:
            raise ArchiveIncomplete("PIT CORE-20 is not 20 unique crypto symbols")
        try:
            if not set(members).issubset(self.dataset.members(time_ms)):
                raise ArchiveIncomplete("ordered CORE-20 disagrees with PIT dataset membership")
        except P.PITError as exc:
            raise ArchiveIncomplete(f"PIT membership unavailable: {exc}") from exc
        return members

    def core20_union(self, start_ms: int, end_ms: int) -> tuple[str, ...]:
        self._window(start_ms, end_ms)
        instants = [start_ms]
        instants.extend(
            week["monday_ms"] for week in self.pit_universe["weeks"]
            if start_ms < week["monday_ms"] < end_ms
        )
        seen: list[str] = []
        for instant in instants:
            for symbol in self.core20_at(instant):
                if symbol not in seen:
                    seen.append(symbol)
        return tuple(seen)

    def load_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> tuple[RawKline, ...]:
        if interval not in INTERVAL_MS:
            raise ArchiveIncomplete("candidate archive adapter permits only 1h/4h klines")
        self._require_core_symbol(symbol, start_ms, end_ms)
        rows = self._rows(symbol, "klines", interval, start_ms, end_ms)
        step = INTERVAL_MS[interval]
        chosen = [row for row in rows if start_ms <= int(row[0]) < end_ms]
        times = [int(row[0]) for row in chosen]
        if times != list(range(start_ms, end_ms, step)):
            raise ArchiveIncomplete(f"{symbol} {interval}: incomplete or misaligned kline coverage")
        out = []
        for row in chosen:
            try:
                values = [float(row[index]) for index in range(1, 6)]
            except ValueError as exc:
                raise ArchiveIncomplete(f"{symbol} {interval}: non-numeric OHLCV") from exc
            o, h, low, close, volume = values
            if (not all(math.isfinite(value) for value in values) or min(o, h, low, close) <= 0
                    or low > min(o, close) or h < max(o, close) or volume < 0):
                raise ArchiveIncomplete(f"{symbol} {interval}: malformed OHLCV row")
            out.append(RawKline(int(row[0]), o, h, low, close, volume))
        return tuple(out)

    def load_funding(self, symbol: str, start_ms: int, end_ms: int) -> tuple[RawFunding, ...]:
        self._require_core_symbol(symbol, start_ms, end_ms)
        rows = self._rows(symbol, "fundingRate", None, start_ms, end_ms)
        out = []
        for row in rows:
            time_ms = int(row[0])
            if start_ms <= time_ms < end_ms:
                try:
                    interval_hours, rate = int(row[1]), float(row[2])
                except ValueError as exc:
                    raise ArchiveIncomplete(f"{symbol}: malformed funding row") from exc
                if interval_hours <= 0 or not math.isfinite(rate):
                    raise ArchiveIncomplete(f"{symbol}: malformed funding interval/rate")
                out.append(RawFunding(time_ms, interval_hours, rate))
        if any(right.time_ms <= left.time_ms for left, right in zip(out, out[1:])):
            raise ArchiveIncomplete(f"{symbol}: funding rows are not strictly increasing")
        if not out:
            raise ArchiveIncomplete(f"{symbol}: no funding settlements cover requested window")
        if out[0].time_ms - start_ms > out[0].interval_hours * HOUR_MS:
            raise ArchiveIncomplete(f"{symbol}: funding coverage gap at window start")
        for left, right in zip(out, out[1:]):
            if right.time_ms - left.time_ms > max(left.interval_hours, right.interval_hours) * HOUR_MS:
                raise ArchiveIncomplete(f"{symbol}: funding settlement gap")
        if end_ms - out[-1].time_ms > out[-1].interval_hours * HOUR_MS:
            raise ArchiveIncomplete(f"{symbol}: funding coverage gap at window end")
        return tuple(out)

    def load_mark_closes(self, symbol: str, start_ms: int, end_ms: int) -> tuple[tuple[int, float], ...]:
        """Return causal 1h mark closes as (available_ms, close), with exact hourly coverage."""
        self._require_core_symbol(symbol, start_ms, end_ms)
        raw_start = start_ms - HOUR_MS
        rows = self._rows(symbol, "markPriceKlines", "1h", raw_start, end_ms)
        chosen = [row for row in rows if raw_start <= int(row[0]) < end_ms]
        times = [int(row[0]) for row in chosen]
        if times != list(range(raw_start, end_ms, HOUR_MS)):
            raise ArchiveIncomplete(f"{symbol}: incomplete causal 1h mark coverage")
        out = []
        for row in chosen:
            close = float(row[4])
            if not math.isfinite(close) or close <= 0:
                raise ArchiveIncomplete(f"{symbol}: invalid causal mark close")
            out.append((int(row[0]) + HOUR_MS, close))
        return tuple(out)

    @staticmethod
    def _window(start_ms: int, end_ms: int) -> None:
        if (type(start_ms) is not int or type(end_ms) is not int or start_ms < 0
                or start_ms >= end_ms or end_ms > CUTOFF_MS):
            raise ArchiveIncomplete("archive window must be nonempty and end at/before the frozen exclusive cutoff")

    def _require_core_symbol(self, symbol: str, start_ms: int, end_ms: int) -> None:
        self._window(start_ms, end_ms)
        if symbol not in self.core20_union(start_ms, end_ms):
            raise ArchiveIncomplete(f"{symbol}: outside frozen PIT CORE-20 for requested window")

    def _rows(self, symbol: str, series: str, interval: str | None,
              start_ms: int, end_ms: int) -> list[list[bytes]]:
        self._window(max(0, start_ms), end_ms)
        files = []
        for record in self.dataset.files(series, symbol, interval):
            meta = M.archive_meta(record["path"])
            if meta["month_start_ms"] < end_ms and meta["month_end_ms"] > start_ms:
                files.append(record)
        files.sort(key=lambda record: M.archive_meta(record["path"])["month_start_ms"])
        if not files:
            raise ArchiveIncomplete(f"{symbol} {series} {interval}: no manifest coverage")
        cursor = start_ms
        for record in files:
            meta = M.archive_meta(record["path"])
            if meta["month_start_ms"] > cursor:
                raise ArchiveIncomplete(f"{symbol} {series} {interval}: missing archive month coverage")
            cursor = max(cursor, meta["month_end_ms"])
        if cursor < end_ms:
            raise ArchiveIncomplete(f"{symbol} {series} {interval}: missing archive month coverage")
        rows: list[list[bytes]] = []
        for record in files:
            # Refuse before opening a monthly archive that contains forbidden rows.
            if record["last_open_ms"] >= CUTOFF_MS:
                raise ArchiveIncomplete(f"{record['path']}: archive crosses frozen cutoff; not read")
            try:
                parsed_objects = self.dataset.rows(record)
            except (OSError, M.ManifestError, P.M.ManifestError, P.PITError) as exc:
                raise ArchiveIncomplete(f"{record['path']}: archive verification failed: {exc}") from exc
            if series == "fundingRate":
                rows.extend([[str(row.time_ms).encode(), str(row.interval_hours).encode(),
                              repr(row.rate).encode()] for row in parsed_objects])
            else:
                # PIT Bar has already passed manifest.py's authoritative parser.
                rows.extend([[str(row.open_ms).encode(), repr(row.open).encode(), repr(row.high).encode(),
                              repr(row.low).encode(), repr(row.close).encode(), repr(row.volume).encode()]
                             for row in parsed_objects])
        times = [int(row[0]) for row in rows]
        if any(right <= left for left, right in zip(times, times[1:])):
            raise ArchiveIncomplete(f"{symbol} {series} {interval}: rows are not globally strictly increasing")
        return rows
