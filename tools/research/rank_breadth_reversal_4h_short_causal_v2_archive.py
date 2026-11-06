"""Checksum-bound raw archive input for RBR-4H short causal v2.

This module is deliberately separate from the executable runner.  It reuses the
repository archive and PIT-universe authorities and has no CLI, promotion path,
or implicit store.  The runner may call it only after the contract merge guard.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tools.research import manifest as M
from tools.research import pit as P
from tools.research import universe as U
import feasibility as F


HOUR_MS = 3_600_000
CUTOFF_MS = 1_790_553_600_000  # 2026-09-28T00:00:00Z, exclusive
INTERVAL_MS = {"1h": HOUR_MS, "4h": 4 * HOUR_MS}
EXPECTED_MANIFEST_SHA256 = "94617184d0b6d409e90b5eb15f2a41f40f0465fb01fa5d659899be25e1a199fb"
EXPECTED_MANIFEST_DIGEST = "54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7"
EXPECTED_UNIVERSE_SHA256 = "d7000458b2c6ec848c6a359aa4b011850a3be437d8f22e53701e76927c4d3229"
EXPECTED_UNIVERSE_DIGEST = "485c752226cc6b50a07d3feefa90a4fdc62e256e8d5a033e57d24983bf2cd093"
EXPECTED_CLASSES_SHA256 = "b5d3858e3b090f8e6b8a3769a23ba409949f6edfeeefe896baf4bc5d3d7f6198"
EXPECTED_CLASSES_DIGEST = "48a9d32c4299495d0c58b175755d8ebdab562f8e18a05196a7f361fb6a986845"
EXPECTED_BOOKS = ("crypto", "gold-commodity", "equity", "fx")


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


@dataclass(frozen=True)
class AuthorityDigests:
    manifest_digest: str
    pit_universe_digest: str
    manifest_file_sha256: str
    universe_file_sha256: str
    classes_digest: str
    classes_file_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "manifest_digest": self.manifest_digest,
            "pit_universe_digest": self.pit_universe_digest,
            "manifest_file_sha256": self.manifest_file_sha256,
            "universe_file_sha256": self.universe_file_sha256,
            "classes_digest": self.classes_digest,
            "classes_file_sha256": self.classes_file_sha256,
        }


def load_exchange_filters(path: str | Path, expected_sha256: str,
                          symbols: tuple[str, ...]) -> tuple[dict[str, dict], str]:
    """Checksum-bind raw frozen exchangeInfo and reuse the engine's exact parser."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ArchiveIncomplete(f"frozen exchangeInfo unavailable: {exc}") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ArchiveIncomplete(f"frozen exchangeInfo checksum mismatch: {actual}")
    try:
        info = json.loads(raw.decode("utf-8"),
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        rules = F.rules_from_exchange_info(info)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ArchiveIncomplete(f"frozen exchangeInfo is malformed: {exc}") from exc
    missing = sorted(set(symbols) - set(rules))
    if missing:
        raise ArchiveIncomplete(f"frozen exchangeInfo lacks actual filters for {missing}")
    required = ("tick", "step", "min_qty", "min_notional")
    for symbol in symbols:
        rule = rules[symbol]
        if any(not math.isfinite(float(rule.get(key, float("nan")))) or float(rule[key]) <= 0
               for key in required):
            raise ArchiveIncomplete(f"{symbol}: invalid frozen exchange filters")
        max_qty = rule.get("max_qty")
        if max_qty is not None and (not math.isfinite(float(max_qty)) or float(max_qty) <= 0):
            raise ArchiveIncomplete(f"{symbol}: invalid frozen maximum quantity")
    return {symbol: dict(rules[symbol]) for symbol in symbols}, actual


def _canonical_digest(value: dict) -> str:
    core = {key: item for key, item in value.items() if key != "digest"}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _file_sha256(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ArchiveIncomplete(f"authority byte binding failed: {exc}") from exc


def load_authorities(manifest_path: str | Path, universe_path: str | Path,
                     classes_path: str | Path) -> tuple[dict, dict, dict]:
    """Load and bind the repository's authoritative manifest and PIT universe."""
    try:
        manifest = M.load(str(manifest_path))
        with Path(universe_path).open(encoding="utf-8") as fh:
            pit = json.load(fh, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        with Path(classes_path).open(encoding="utf-8") as fh:
            classes = json.load(fh, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, ValueError, M.ManifestError) as exc:
        raise ArchiveIncomplete(f"authority load failed: {exc}") from exc
    if (_file_sha256(manifest_path) != EXPECTED_MANIFEST_SHA256
            or manifest.get("digest") != EXPECTED_MANIFEST_DIGEST
            or manifest.get("loader_version") != M.ARCHIVE_LOADER
            or manifest.get("source_class") != "archive-verified"
            or manifest.get("data_root") != M.ARCHIVE_ROOT
            or manifest.get("survivor_only") is not False):
        raise ArchiveIncomplete("manifest is not the checksum-verified Binance archive authority")
    if (_file_sha256(universe_path) != EXPECTED_UNIVERSE_SHA256
            or pit.get("format") != U.FORMAT or pit.get("universe_id") != U.UNIVERSE_ID
            or pit.get("digest") != U.digest_of(pit)
            or pit.get("digest") != EXPECTED_UNIVERSE_DIGEST):
        raise ArchiveIncomplete("PIT universe identity/digest does not match its content")
    if (_file_sha256(classes_path) != EXPECTED_CLASSES_SHA256
            or classes.get("digest") != EXPECTED_CLASSES_DIGEST
            or _canonical_digest(classes) != EXPECTED_CLASSES_DIGEST):
        raise ArchiveIncomplete("instrument classes identity/digest does not match its content")
    if (pit.get("manifest_digest") != manifest["digest"]
            or pit.get("classes_digest") != classes.get("digest")
            or pit.get("classes_id") != classes.get("classes_id")
            or tuple(pit.get("books", ())) != EXPECTED_BOOKS):
        raise ArchiveIncomplete("PIT universe is not bound to this archive manifest")
    weeks = pit.get("weeks") or []
    if len(weeks) != 348 or weeks[0].get("monday_utc") != "2020-02-03" or weeks[-1].get("monday_utc") != "2026-09-28":
        raise ArchiveIncomplete("PIT universe does not contain the frozen 348-week authority")
    previous = None
    for week in weeks:
        monday = week.get("monday_ms")
        if type(monday) is not int or (previous is not None and monday - previous != 7 * 86_400_000):
            raise ArchiveIncomplete("PIT weeks are not unique consecutive Monday rows")
        previous = monday
        if set((week.get("books") or {}).keys()) != set(EXPECTED_BOOKS):
            raise ArchiveIncomplete("PIT week book keys changed")
        for book in EXPECTED_BOOKS:
            row = week["books"][book]
            members, qv = row.get("members"), row.get("qv30d_usdt")
            if (not isinstance(members, list) or not isinstance(qv, list)
                    or len(members) != len(qv) or len(members) > 40
                    or len(set(members)) != len(members)
                    or any(not isinstance(symbol, str) or not symbol.isascii() for symbol in members)):
                raise ArchiveIncomplete("PIT member arrays are malformed")
            try:
                pairs = [(symbol, Decimal(value)) for symbol, value in zip(members, qv)]
            except (InvalidOperation, ValueError):
                raise ArchiveIncomplete("PIT quote-volume value is malformed")
            if (any(not value.is_finite() or value < 0 for _, value in pairs)
                    or any(not (left_v > right_v or (left_v == right_v and left_s < right_s))
                           for (left_s, left_v), (right_s, right_v) in zip(pairs, pairs[1:]))):
                raise ArchiveIncomplete("PIT quote-volume ordering changed")
    return manifest, pit, classes


def authority_digests(manifest_path: str | Path, universe_path: str | Path, classes_path: str | Path,
                      manifest: dict, pit: dict, classes: dict) -> AuthorityDigests:
    """Bind parsed authorities to the exact repository file bytes."""
    try:
        manifest_raw = Path(manifest_path).read_bytes()
        universe_raw = Path(universe_path).read_bytes()
        classes_raw = Path(classes_path).read_bytes()
    except OSError as exc:
        raise ArchiveIncomplete(f"authority byte binding failed: {exc}") from exc
    return AuthorityDigests(
        manifest_digest=str(manifest["digest"]),
        pit_universe_digest=str(pit["digest"]),
        manifest_file_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        universe_file_sha256=hashlib.sha256(universe_raw).hexdigest(),
        classes_digest=str(classes["digest"]),
        classes_file_sha256=hashlib.sha256(classes_raw).hexdigest(),
    )


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

    def verify_repository_authorities(self, manifest_path: str | Path,
                                      universe_path: str | Path,
                                      classes_path: str | Path) -> AuthorityDigests:
        """Refuse a caller-supplied self-consistent authority pair.

        The runner invokes this before the first row-loading method.  Only the
        fixed repository manifest and PIT universe bytes can authorize an
        evaluation; synthetic adapters remain usable solely for adapter tests.
        """
        expected_manifest, expected_pit, expected_classes = load_authorities(
            manifest_path, universe_path, classes_path)
        expected = authority_digests(
            manifest_path, universe_path, classes_path,
            expected_manifest, expected_pit, expected_classes)
        if (self.manifest != expected_manifest or self.pit_universe != expected_pit
                or self.manifest.get("digest") != expected.manifest_digest
                or self.pit_universe.get("digest") != expected.pit_universe_digest
                or self.pit_universe.get("manifest_digest") != expected.manifest_digest):
            raise ArchiveIncomplete(
                "adapter authorities do not match fixed repository manifest/PIT bytes")
        return expected

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
