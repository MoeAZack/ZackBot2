"""Checksum-bound raw archive input for STRATEGY-CAUSAL-PACK-01.

This module is deliberately separate from the executable runner.  It reuses the
repository archive and PIT-universe authorities and has no CLI, promotion path,
or implicit store.  The runner may call it only after the contract merge guard.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.research import manifest as M
from tools.research import pit as P
from tools.research import universe as U
import feasibility as F


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


@dataclass(frozen=True)
class CoverageGap:
    """An explicit half-open interval absent from an otherwise verified load."""

    start_ms: int
    end_ms: int
    reason: str


@dataclass(frozen=True)
class SparseKlineLoad:
    rows: tuple[RawKline, ...]
    gaps: tuple[CoverageGap, ...]


@dataclass(frozen=True)
class SparseFundingLoad:
    rows: tuple[RawFunding, ...]
    gaps: tuple[CoverageGap, ...]


@dataclass(frozen=True)
class SparseMarkLoad:
    rows: tuple[tuple[int, float], ...]
    gaps: tuple[CoverageGap, ...]


@dataclass(frozen=True)
class AuthorityDigests:
    manifest_digest: str
    pit_universe_digest: str
    manifest_file_sha256: str
    universe_file_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "manifest_digest": self.manifest_digest,
            "pit_universe_digest": self.pit_universe_digest,
            "manifest_file_sha256": self.manifest_file_sha256,
            "universe_file_sha256": self.universe_file_sha256,
        }


@dataclass(frozen=True)
class FoldFilterAuthority:
    fold_name: str
    authority_sha256: str
    snapshot_sha256: str
    captured_at: str
    eligible_symbols: frozenset[str]
    rules: dict[str, dict]


def _utc_ms(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ArchiveIncomplete(f"invalid authority timestamp: {value!r}") from exc
    return int(parsed.timestamp() * 1000)


def load_fold_filter_authorities(authority_path: str | Path, expected_sha256: str,
                                 repository_root: str | Path, contract: dict) -> dict[str, FoldFilterAuthority]:
    """Bind causal, pre-fold Binance filter snapshots before any market row read."""
    authority_file = Path(authority_path)
    root = Path(repository_root).resolve()
    try:
        raw = authority_file.read_bytes()
        authority = json.loads(raw.decode("utf-8"),
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ArchiveIncomplete(f"filter authority unavailable or malformed: {exc}") from exc
    actual_authority_sha = hashlib.sha256(raw).hexdigest()
    if actual_authority_sha != expected_sha256:
        raise ArchiveIncomplete(f"filter authority checksum mismatch: {actual_authority_sha}")
    if (authority.get("authority"), authority.get("version"), authority.get("status"),
            authority.get("signed_by")) != (
            "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", 2, "FROZEN", "codex"):
        raise ArchiveIncomplete("filter authority identity/status changed")
    data = contract.get("data") or {}
    if (data.get("exchange_filter_authority_sha256") != actual_authority_sha
            or data.get("pit_universe_path") != authority.get("pit_universe_path")
            or data.get("pit_universe_sha256") != authority.get("pit_universe_sha256")
            or data.get("pit_universe_digest") != authority.get("pit_universe_digest")):
        raise ArchiveIncomplete("contract/filter authority PIT binding changed")
    pit_rel = authority.get("pit_universe_path")
    if not isinstance(pit_rel, str) or Path(pit_rel).is_absolute():
        raise ArchiveIncomplete("PIT universe path must be repository-relative")
    pit_path = (root / pit_rel).resolve()
    if root != pit_path and root not in pit_path.parents:
        raise ArchiveIncomplete("PIT universe escapes repository root")
    try:
        pit_raw = pit_path.read_bytes()
        pit = json.loads(pit_raw.decode("utf-8"),
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ArchiveIncomplete(f"PIT universe unavailable or malformed: {exc}") from exc
    if (hashlib.sha256(pit_raw).hexdigest() != authority.get("pit_universe_sha256")
            or pit.get("digest") != authority.get("pit_universe_digest")
            or pit.get("digest") != U.digest_of(pit)):
        raise ArchiveIncomplete("PIT universe byte/content digest mismatch")
    folds = contract.get("folds") or []
    snapshots: dict[str, tuple[dict[str, dict], frozenset[str], str, int, int]] = {}
    for row in authority.get("snapshots", ()):
        captured = row.get("captured_at")
        if not isinstance(captured, str) or captured in snapshots:
            raise ArchiveIncomplete("filter authority has duplicate or invalid capture")
        rel = row.get("path")
        if not isinstance(rel, str) or Path(rel).is_absolute():
            raise ArchiveIncomplete("filter snapshot path must be repository-relative")
        path = (root / rel).resolve()
        if root != path and root not in path.parents:
            raise ArchiveIncomplete("filter snapshot escapes repository root")
        try:
            payload_raw = path.read_bytes()
            payload = json.loads(payload_raw.decode("utf-8"),
                                 parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (OSError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ArchiveIncomplete(f"filter snapshot unavailable or malformed: {exc}") from exc
        snapshot_sha = hashlib.sha256(payload_raw).hexdigest()
        if snapshot_sha != row.get("sha256"):
            raise ArchiveIncomplete(f"filter snapshot checksum mismatch: {path.name}")
        captured_ms, declared_server_ms = _utc_ms(captured), _utc_ms(row.get("server_time"))
        if type(payload.get("serverTime")) is not int or payload["serverTime"] != declared_server_ms:
            raise ArchiveIncomplete(f"filter snapshot serverTime mismatch: {path.name}")
        rules = F.rules_from_exchange_info(payload)
        eligible: set[str] = set()
        seen_symbols: set[str] = set()
        for symbol_row in payload.get("symbols", ()):
            symbol = symbol_row.get("symbol") if isinstance(symbol_row, dict) else None
            if not isinstance(symbol, str) or symbol in seen_symbols:
                raise ArchiveIncomplete(f"filter snapshot has duplicate/invalid symbol: {path.name}")
            seen_symbols.add(symbol)
            if not (symbol_row.get("status") == "TRADING"
                    and symbol_row.get("contractType") == "PERPETUAL"
                    and symbol_row.get("quoteAsset") == "USDT"
                    and symbol_row.get("underlyingType") == "COIN"):
                continue
            filters = symbol_row.get("filters")
            if not isinstance(filters, list):
                continue
            types = [item.get("filterType") for item in filters if isinstance(item, dict)]
            required = ("PRICE_FILTER", "MARKET_LOT_SIZE", "MIN_NOTIONAL")
            if any(types.count(filter_type) != 1 for filter_type in required) or symbol not in rules:
                continue
            rule = rules[symbol]
            if any(not math.isfinite(float(rule.get(key, float("nan")))) or float(rule[key]) <= 0
                   for key in ("tick", "step", "min_qty", "min_notional", "max_qty")):
                continue
            eligible.add(symbol)
        snapshots[captured] = (
            rules, frozenset(eligible), snapshot_sha, captured_ms, declared_server_ms)
    bindings = authority.get("fold_bindings")
    if not isinstance(bindings, dict) or set(bindings) != {fold.get("name") for fold in folds}:
        raise ArchiveIncomplete("filter authority fold bindings do not match contract folds")
    out: dict[str, FoldFilterAuthority] = {}
    for fold in folds:
        name, start_ms = fold.get("name"), _utc_ms(fold.get("start"))
        eligible_captures = [
            capture
            for capture, (_, _, _, captured_ms, server_ms) in snapshots.items()
            if captured_ms < start_ms and server_ms < start_ms
        ]
        if not eligible_captures:
            raise ArchiveIncomplete(f"{name}: no causal pre-fold filter snapshot")
        latest = max(eligible_captures, key=_utc_ms)
        if bindings.get(name) != latest:
            raise ArchiveIncomplete(f"{name}: binding is not the latest causal pre-fold snapshot")
        rules, symbols, snapshot_sha, captured_ms, server_ms = snapshots[latest]
        if captured_ms >= start_ms or server_ms >= start_ms:
            raise ArchiveIncomplete(f"{name}: future-dated filter snapshot")
        active_at_start = max(
            (week for week in pit.get("weeks", ())
             if type(week.get("monday_ms")) is int and week["monday_ms"] <= start_ms),
            key=lambda week: week["monday_ms"],
            default=None,
        )
        if active_at_start is None:
            raise ArchiveIncomplete(f"{name}: PIT membership is unavailable at fold start")
        weeks = [active_at_start]
        end_ms = _utc_ms(fold.get("end"))
        for week in pit.get("weeks", ()):
            monday_ms = week.get("monday_ms")
            if type(monday_ms) is int and start_ms < monday_ms < end_ms:
                weeks.append(week)
        cells = []
        for week in weeks:
            ranked = week.get("books", {}).get("crypto", {}).get("members", ())
            members = [symbol for symbol in ranked if symbol in symbols][:20]
            if len(members) != 20 or len(set(members)) != 20:
                raise ArchiveIncomplete(f"{name}: causal filter-eligible CORE-20 is incomplete")
            cells.append({"monday_utc": week.get("monday_utc"), "members": members})
        cell_sha = hashlib.sha256(json.dumps(
            cells, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        if cell_sha != (authority.get("weekly_membership_sha256") or {}).get(name):
            raise ArchiveIncomplete(f"{name}: causal weekly membership digest mismatch")
        out[name] = FoldFilterAuthority(name, actual_authority_sha, snapshot_sha, latest,
                                        symbols, {symbol: dict(rules[symbol]) for symbol in symbols})
    return out


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
    return {symbol: dict(rules[symbol]) for symbol in symbols}, actual


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


def authority_digests(manifest_path: str | Path, universe_path: str | Path,
                      manifest: dict, pit: dict) -> AuthorityDigests:
    """Bind parsed authorities to the exact repository file bytes."""
    try:
        manifest_raw = Path(manifest_path).read_bytes()
        universe_raw = Path(universe_path).read_bytes()
    except OSError as exc:
        raise ArchiveIncomplete(f"authority byte binding failed: {exc}") from exc
    return AuthorityDigests(
        manifest_digest=str(manifest["digest"]),
        pit_universe_digest=str(pit["digest"]),
        manifest_file_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        universe_file_sha256=hashlib.sha256(universe_raw).hexdigest(),
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
                                      universe_path: str | Path) -> AuthorityDigests:
        """Refuse a caller-supplied self-consistent authority pair.

        The runner invokes this before the first row-loading method.  Only the
        fixed repository manifest and PIT universe bytes can authorize an
        evaluation; synthetic adapters remain usable solely for adapter tests.
        """
        expected_manifest, expected_pit = load_authorities(manifest_path, universe_path)
        expected = authority_digests(
            manifest_path, universe_path, expected_manifest, expected_pit)
        if (self.manifest != expected_manifest or self.pit_universe != expected_pit
                or self.manifest.get("digest") != expected.manifest_digest
                or self.pit_universe.get("digest") != expected.pit_universe_digest
                or self.pit_universe.get("manifest_digest") != expected.manifest_digest):
            raise ArchiveIncomplete(
                "adapter authorities do not match fixed repository manifest/PIT bytes")
        return expected

    def core20_at(self, time_ms: int,
                  eligible_symbols: frozenset[str] | None = None) -> tuple[str, ...]:
        weeks = [week for week in self.pit_universe["weeks"] if week["monday_ms"] <= time_ms]
        if not weeks:
            raise ArchiveIncomplete(f"no PIT universe observation at {time_ms}")
        ranked = tuple(max(weeks, key=lambda week: week["monday_ms"])["books"]["crypto"]["members"])
        members = (ranked[:20] if eligible_symbols is None else
                   tuple(symbol for symbol in ranked if symbol in eligible_symbols)[:20])
        if len(members) != 20 or len(set(members)) != 20:
            raise ArchiveIncomplete("PIT CORE-20 is not 20 unique crypto symbols")
        try:
            if not set(members).issubset(self.dataset.members(time_ms)):
                raise ArchiveIncomplete("ordered CORE-20 disagrees with PIT dataset membership")
        except P.PITError as exc:
            raise ArchiveIncomplete(f"PIT membership unavailable: {exc}") from exc
        return members

    def core20_union(self, start_ms: int, end_ms: int,
                     eligible_symbols: frozenset[str] | None = None) -> tuple[str, ...]:
        self._window(start_ms, end_ms)
        instants = [start_ms]
        instants.extend(
            week["monday_ms"] for week in self.pit_universe["weeks"]
            if start_ms < week["monday_ms"] < end_ms
        )
        seen: list[str] = []
        for instant in instants:
            for symbol in self.core20_at(instant, eligible_symbols):
                if symbol not in seen:
                    seen.append(symbol)
        return tuple(seen)

    def load_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int, *,
                    eligible_symbols: frozenset[str] | None = None) -> tuple[RawKline, ...]:
        if interval not in INTERVAL_MS:
            raise ArchiveIncomplete("candidate archive adapter permits only 1h/4h klines")
        self._require_core_symbol(symbol, start_ms, end_ms, eligible_symbols)
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

    def load_klines_sparse(self, symbol: str, interval: str, start_ms: int, end_ms: int, *,
                           eligible_symbols: frozenset[str] | None = None) -> SparseKlineLoad:
        """Return every verified grid-aligned bar and explicit missing grid intervals.

        Sparse mode relaxes coverage only.  Authority, archive checksum, cutoff,
        parser, ordering, duplicate, timestamp alignment, and finite structural
        OHLCV validation remain fail-closed. A checksum-verified row containing
        a non-finite numeric candle is explicit unavailable data, not a bridge.
        """
        if interval not in INTERVAL_MS:
            raise ArchiveIncomplete("candidate archive adapter permits only 1h/4h klines")
        self._require_core_symbol(symbol, start_ms, end_ms, eligible_symbols)
        step = INTERVAL_MS[interval]
        if start_ms % step or end_ms % step:
            raise ArchiveIncomplete(f"{symbol} {interval}: sparse window is not grid-aligned")
        rows, manifest_gaps = self._rows_sparse(
            symbol, "klines", interval, start_ms, end_ms)
        chosen = [row for row in rows if start_ms <= int(row[0]) < end_ms]
        times = [int(row[0]) for row in chosen]
        if any((time_ms - start_ms) % step for time_ms in times):
            raise ArchiveIncomplete(f"{symbol} {interval}: misaligned kline timestamp")
        out = []
        unavailable = []
        for row in chosen:
            try:
                values = [float(row[index]) for index in range(1, 6)]
            except ValueError as exc:
                raise ArchiveIncomplete(f"{symbol} {interval}: non-numeric OHLCV") from exc
            o, h, low, close, volume = values
            if not all(math.isfinite(value) for value in values):
                unavailable.append(CoverageGap(int(row[0]), int(row[0]) + step,
                                               "non_finite_row"))
                continue
            if (min(o, h, low, close) <= 0 or low > min(o, close)
                    or h < max(o, close) or volume < 0):
                raise ArchiveIncomplete(f"{symbol} {interval}: malformed OHLCV row")
            out.append(RawKline(int(row[0]), o, h, low, close, volume))
        observed = {row.open_ms for row in out} | {gap.start_ms for gap in unavailable}
        row_gaps = self._grid_gaps(start_ms, end_ms, step, observed, "missing_row")
        return SparseKlineLoad(
            tuple(out), self._merge_gaps((*manifest_gaps, *row_gaps, *unavailable)))

    def load_funding(self, symbol: str, start_ms: int, end_ms: int, *,
                     eligible_symbols: frozenset[str] | None = None) -> tuple[RawFunding, ...]:
        self._require_core_symbol(symbol, start_ms, end_ms, eligible_symbols)
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

    def load_funding_sparse(self, symbol: str, start_ms: int, end_ms: int, *,
                            eligible_symbols: frozenset[str] | None = None) -> SparseFundingLoad:
        """Return verified settlements and explicit uncovered funding intervals."""
        self._require_core_symbol(symbol, start_ms, end_ms, eligible_symbols)
        rows, manifest_gaps = self._rows_sparse(
            symbol, "fundingRate", None, start_ms, end_ms)
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
        settlement_gaps: list[CoverageGap] = []
        if not out:
            settlement_gaps.append(CoverageGap(start_ms, end_ms, "no_settlement"))
        else:
            first_cover_start = out[0].time_ms - out[0].interval_hours * HOUR_MS
            if first_cover_start > start_ms:
                settlement_gaps.append(CoverageGap(start_ms, min(first_cover_start, end_ms),
                                                   "settlement_gap"))
            for left, right in zip(out, out[1:]):
                covered_until = left.time_ms + max(
                    left.interval_hours, right.interval_hours) * HOUR_MS
                if covered_until < right.time_ms:
                    settlement_gaps.append(CoverageGap(
                        max(start_ms, covered_until), min(end_ms, right.time_ms),
                        "settlement_gap"))
            covered_until = out[-1].time_ms + out[-1].interval_hours * HOUR_MS
            if covered_until < end_ms:
                settlement_gaps.append(CoverageGap(max(start_ms, covered_until), end_ms,
                                                   "settlement_gap"))
        return SparseFundingLoad(tuple(out), self._merge_gaps((*manifest_gaps, *settlement_gaps)))

    def load_mark_closes(self, symbol: str, start_ms: int, end_ms: int, *,
                         eligible_symbols: frozenset[str] | None = None) -> tuple[tuple[int, float], ...]:
        """Return causal 1h mark closes as (available_ms, close), with exact hourly coverage."""
        self._require_core_symbol(symbol, start_ms, end_ms, eligible_symbols)
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

    def load_mark_closes_sparse(self, symbol: str, start_ms: int, end_ms: int, *,
                                eligible_symbols: frozenset[str] | None = None) -> SparseMarkLoad:
        """Return verified causal mark closes and explicit missing availability intervals."""
        self._require_core_symbol(symbol, start_ms, end_ms, eligible_symbols)
        if start_ms % HOUR_MS or end_ms % HOUR_MS:
            raise ArchiveIncomplete(f"{symbol}: sparse mark window is not grid-aligned")
        raw_start = start_ms - HOUR_MS
        rows, manifest_gaps = self._rows_sparse(
            symbol, "markPriceKlines", "1h", raw_start, end_ms)
        chosen = [row for row in rows if raw_start <= int(row[0]) < end_ms]
        times = [int(row[0]) for row in chosen]
        if any((time_ms - raw_start) % HOUR_MS for time_ms in times):
            raise ArchiveIncomplete(f"{symbol}: misaligned causal mark timestamp")
        out = []
        for row in chosen:
            close = float(row[4])
            if not math.isfinite(close) or close <= 0:
                raise ArchiveIncomplete(f"{symbol}: invalid causal mark close")
            out.append((int(row[0]) + HOUR_MS, close))
        observed = set(times)
        row_gaps = self._grid_gaps(raw_start, end_ms, HOUR_MS, observed, "missing_row")
        # Gap intervals are reported in availability time, matching returned rows.
        shifted = tuple(CoverageGap(max(start_ms, gap.start_ms + HOUR_MS),
                                    min(end_ms + HOUR_MS, gap.end_ms + HOUR_MS), gap.reason)
                        for gap in (*manifest_gaps, *row_gaps))
        return SparseMarkLoad(tuple(out), self._merge_gaps(shifted))

    @staticmethod
    def _window(start_ms: int, end_ms: int) -> None:
        if (type(start_ms) is not int or type(end_ms) is not int or start_ms < 0
                or start_ms >= end_ms or end_ms > CUTOFF_MS):
            raise ArchiveIncomplete("archive window must be nonempty and end at/before the frozen exclusive cutoff")

    def _require_core_symbol(self, symbol: str, start_ms: int, end_ms: int,
                             eligible_symbols: frozenset[str] | None = None) -> None:
        self._window(start_ms, end_ms)
        if symbol not in self.core20_union(start_ms, end_ms, eligible_symbols):
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

    def _rows_sparse(self, symbol: str, series: str, interval: str | None,
                     start_ms: int, end_ms: int) -> tuple[list[list[bytes]], tuple[CoverageGap, ...]]:
        """Read only present manifest files, retaining every strict integrity check."""
        self._window(max(0, start_ms), end_ms)
        files = []
        for record in self.dataset.files(series, symbol, interval):
            meta = M.archive_meta(record["path"])
            if meta["month_start_ms"] < end_ms and meta["month_end_ms"] > start_ms:
                files.append(record)
        files.sort(key=lambda record: M.archive_meta(record["path"])["month_start_ms"])
        covered = [
            (max(start_ms, M.archive_meta(record["path"])["month_start_ms"]),
             min(end_ms, M.archive_meta(record["path"])["month_end_ms"]))
            for record in files
        ]
        manifest_gaps = self._interval_gaps(start_ms, end_ms, covered, "missing_manifest")
        rows: list[list[bytes]] = []
        for record in files:
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
                rows.extend([[str(row.open_ms).encode(), repr(row.open).encode(), repr(row.high).encode(),
                              repr(row.low).encode(), repr(row.close).encode(), repr(row.volume).encode()]
                             for row in parsed_objects])
        times = [int(row[0]) for row in rows]
        if any(right <= left for left, right in zip(times, times[1:])):
            raise ArchiveIncomplete(f"{symbol} {series} {interval}: rows are not globally strictly increasing")
        return rows, manifest_gaps

    @staticmethod
    def _interval_gaps(start_ms: int, end_ms: int, covered: list[tuple[int, int]],
                       reason: str) -> tuple[CoverageGap, ...]:
        gaps: list[CoverageGap] = []
        cursor = start_ms
        for left, right in sorted(covered):
            if right <= cursor:
                continue
            if left > cursor:
                gaps.append(CoverageGap(cursor, min(left, end_ms), reason))
            cursor = max(cursor, right)
            if cursor >= end_ms:
                break
        if cursor < end_ms:
            gaps.append(CoverageGap(cursor, end_ms, reason))
        return tuple(gaps)

    @classmethod
    def _grid_gaps(cls, start_ms: int, end_ms: int, step_ms: int,
                   observed: set[int], reason: str) -> tuple[CoverageGap, ...]:
        missing = [time_ms for time_ms in range(start_ms, end_ms, step_ms)
                   if time_ms not in observed]
        if not missing:
            return ()
        gaps: list[CoverageGap] = []
        left = previous = missing[0]
        for time_ms in missing[1:]:
            if time_ms != previous + step_ms:
                gaps.append(CoverageGap(left, previous + step_ms, reason))
                left = time_ms
            previous = time_ms
        gaps.append(CoverageGap(left, previous + step_ms, reason))
        return tuple(gaps)

    @staticmethod
    def _merge_gaps(gaps: tuple[CoverageGap, ...]) -> tuple[CoverageGap, ...]:
        """Normalize overlapping gaps without hiding their distinct causes."""
        valid = sorted((gap for gap in gaps if gap.start_ms < gap.end_ms),
                       key=lambda gap: (gap.reason, gap.start_ms, gap.end_ms))
        merged: list[CoverageGap] = []
        for gap in valid:
            if (merged and merged[-1].reason == gap.reason
                    and gap.start_ms <= merged[-1].end_ms):
                prior = merged[-1]
                merged[-1] = CoverageGap(prior.start_ms, max(prior.end_ms, gap.end_ms), prior.reason)
            else:
                merged.append(gap)
        return tuple(sorted(merged, key=lambda gap: (gap.start_ms, gap.end_ms, gap.reason)))
