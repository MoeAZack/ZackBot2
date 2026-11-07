"""C258 Stage-A causal evaluator primitives and synthetic runner.

The public entrypoint deliberately validates every authority-v6 byte/content pin
before invoking a market loader.  The pure functions in this module are used by
hostile synthetic tests; importing the module never opens an authority, market,
outcome, packet, identity, or holdout file.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import subprocess
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "research_evidence/prereg/cross_sectional_relative_strength_continuation_c258_stagea.v1.json"
CONTRACT_SHA256 = "aeacd7fc684be2c6bc406a8f0d4e6ce7007e015d1b8d45a6250b6a587cce3264"
PLAN_PATH = ROOT / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
PLAN_SHA256 = "30080d16132348fabceb91bffe110f43ee33824c4496622f736ee6a6c4d7af60"
AUTHORITY_ID = "BINANCE-UM-PRECUTOFF-AUTHORITY-V6"
C258_FROZEN_HEAD = "976adda2653584559a5fc105468a6d422796d3a9"
AUTHORITY_PROTECTED_REF = "refs/heads/master"
AUTHORITY_EXPECTED_ANCESTORS = (
    "6754a336652496952aaab82e42fbe9bd6171430a",  # authority-v6 protected base
    C258_FROZEN_HEAD,  # frozen C258 registration
)
HOUR_MS = 3_600_000
FEE_BPS = 5.0
BASE_SLIPPAGE_BPS = 2.0
STRESS_SLIPPAGE_BPS = 10.0
CAPITALS = (1000, 2000, 3000, 4000)
PRIMARY_CAPITAL = 2000
MAX_DECREMENTS = 100_000


class C258Error(RuntimeError):
    """Base class for a deterministic C258 refusal."""


class IncompleteAuthority(C258Error):
    """Required causal authority is absent, malformed, or inconsistent."""


class EvaluationLocked(C258Error):
    """The real evaluator cannot run without the future mechanical pin amendment."""


class IncompleteEpisode(C258Error):
    """An exposed position cannot be completely and conservatively accounted."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def load_frozen_contract() -> dict:
    """Byte-bind and validate the metadata-only preregistration and v6 plan."""
    if not CONTRACT_PATH.is_file() or _sha256(CONTRACT_PATH) != CONTRACT_SHA256:
        raise IncompleteAuthority("C258 frozen contract byte pin mismatch")
    if not PLAN_PATH.is_file() or _sha256(PLAN_PATH) != PLAN_SHA256:
        raise IncompleteAuthority("authority-v6 plan byte pin mismatch")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if (contract.get("candidate_id"), contract.get("default_off"), contract.get("status")) != (
        258, True, "FROZEN_NOT_EVALUATED_RESEARCH_ONLY"
    ):
        raise IncompleteAuthority("C258 is not the frozen default-OFF research contract")
    authority = contract.get("authority", {})
    if (authority.get("plan_sha256"), authority.get("plan_authority_id")) != (PLAN_SHA256, AUTHORITY_ID):
        raise IncompleteAuthority("C258 contract does not bind the reviewed authority-v6 plan")
    if (plan.get("authority_id"), plan.get("version"), plan.get("status")) != (
        AUTHORITY_ID, 6, "FROZEN_METADATA_ONLY_NOT_ACQUIRED"
    ):
        raise IncompleteAuthority("authority-v6 plan identity/status mismatch")
    return contract


@dataclass(frozen=True)
class ArtifactPin:
    path: str
    byte_sha256: str
    content_digest: str

    def validate_shape(self, expected_path: str) -> None:
        if self.path != expected_path:
            raise IncompleteAuthority(f"authority path mismatch: {expected_path}")
        for name, value in (("byte_sha256", self.byte_sha256), ("content_digest", self.content_digest)):
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise IncompleteAuthority(f"invalid {name} for {expected_path}")


@dataclass(frozen=True)
class AuthorityPins:
    protected_head: str
    manifest_v6: ArtifactPin
    pit_v9: ArtifactPin
    filter_v4: ArtifactPin
    coverage_v4: ArtifactPin


EXPECTED_AUTHORITY_PATHS = {
    "manifest_v6": "research_evidence/manifests/binance-um-archive-precutoff-v6.json.gz",
    "pit_v9": "research_evidence/universe/pit-top40-qv30d-precutoff-v9.json",
    "filter_v4": "contracts/strategy_causal_pack_01/exchange_filter_authority.v4.json",
    "coverage_v4": "contracts/strategy_causal_pack_01/precutoff_authority_coverage_preflight.v4.json",
}


def _decode_json_bytes(path: Path, raw: bytes) -> dict:
    try:
        payload = gzip.decompress(raw) if path.suffix == ".gz" else raw
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IncompleteAuthority(f"invalid authority JSON: {path}") from exc
    if not isinstance(value, dict):
        raise IncompleteAuthority(f"authority root is not an object: {path}")
    return value


def _declared_content_digest(value: Mapping[str, object]) -> str | None:
    for key in ("content_digest", "digest"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate
    identity = value.get("identity")
    if isinstance(identity, Mapping):
        for key in ("content_digest", "digest"):
            candidate = identity.get(key)
            if isinstance(candidate, str):
                return candidate
    return None


def _recomputed_content_digest(value: Mapping[str, object]) -> str:
    body = dict(value)
    digest_keys = [key for key in ("content_digest", "digest") if key in body]
    if len(digest_keys) != 1 or not isinstance(body[digest_keys[0]], str):
        raise IncompleteAuthority("authority must declare exactly one top-level content digest")
    del body[digest_keys[0]]
    return _sha256_bytes(_canonical_bytes(body))


def _git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                                text=not binary)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise IncompleteAuthority(f"Git authority resolution failed: {' '.join(args)}") from exc
    return result.stdout if binary else result.stdout.strip()


def _require_binding(doc: Mapping[str, object], key: str, expected: str) -> None:
    found: list[object] = []

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for child_key, child in value.items():
                if child_key == key:
                    found.append(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(doc)
    if not found or any(value != expected for value in found):
        raise IncompleteAuthority(f"authority cross-binding mismatch: {key}")


def _expected_weekly_cells() -> tuple[int, ...]:
    start = int(datetime(2024, 10, 14, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp() * 1000)
    return tuple(range(start, end + 1, 7 * 24 * HOUR_MS))


def _validate_full_authority_schema(pit: Mapping[str, object], filters: Mapping[str, object]) -> None:
    cells = pit.get("weekly_cells", pit.get("cells"))
    if not isinstance(cells, list) or not cells:
        raise IncompleteAuthority("PIT-v9 weekly cells are absent/empty")
    observed: list[int] = []
    for cell in cells:
        if not isinstance(cell, Mapping) or not isinstance(cell.get("monday_ms"), int):
            raise IncompleteAuthority("malformed PIT-v9 weekly cell")
        books = cell.get("books")
        symbols = books.get("crypto") if isinstance(books, Mapping) else cell.get("symbols")
        if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) and s for s in symbols):
            raise IncompleteAuthority("PIT-v9 weekly cell has no nonempty crypto membership")
        observed.append(int(cell["monday_ms"]))
    expected_cells = _expected_weekly_cells()
    if tuple(observed) != expected_cells:
        raise IncompleteAuthority("PIT-v9 weekly cells do not exactly cover frozen windows")
    expected_bindings = {
        "development_eligible_segment": "2024-12-13T04:44:48Z",
        "wf1": "2024-12-13T04:44:48Z",
        "wf2": "2024-12-13T04:44:48Z",
        "wf3": "2026-02-17T04:13:28Z",
        "sealed_holdout": "2026-02-17T04:13:28Z",
    }
    if filters.get("fold_bindings") != expected_bindings:
        raise IncompleteAuthority("filter-v4 fold bindings are not exact")


def _validate_coverage_and_manifest(manifest: Mapping[str, object],
                                    coverage: Mapping[str, object]) -> None:
    required_streams = [{"series": "klines", "interval": "1d"},
                        {"series": "klines", "interval": "1h"},
                        {"series": "markPriceKlines", "interval": "1h"},
                        {"series": "fundingRate", "interval": None}]
    coverage_streams = {"klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null"}
    if (coverage.get("format") != "zb-strategy-causal-pack-01-authority-coverage-preflight/1"
            or coverage.get("required_result") != "ZERO_GUARANTEED_PERIOD_GAPS"
            or coverage.get("result") != "ZERO_GUARANTEED_PERIOD_GAPS"
            or coverage.get("required_streams") != required_streams
            or coverage.get("gaps") != []
            or coverage.get("guaranteed_period_gaps") != 0
            or coverage.get("coverage_start_ms") != 1728864000000
            or coverage.get("cutoff_exclusive_ms") != 1793836800000
            or coverage.get("guaranteed_period_gaps_by_stream")
            != {stream: 0 for stream in coverage_streams}):
        raise IncompleteAuthority("coverage-v4 is not ZERO_GUARANTEED_PERIOD_GAPS")
    cutoff = manifest.get("cutoff_exclusive_utc", manifest.get("cutoff", {}).get("exclusive_utc")
                          if isinstance(manifest.get("cutoff"), Mapping) else None)
    if (cutoff != "2026-11-05T00:00:00Z"
            or manifest.get("cutoff_exclusive_ms") != 1793836800000
            or manifest.get("timezone") != "UTC"):
        raise IncompleteAuthority("manifest-v6 cutoff is not exact and exclusive")


def validate_authority_pins(pins: AuthorityPins, *, root: Path = ROOT) -> dict[str, dict]:
    """Validate all v6 identities before any market-row loader may be called."""
    load_frozen_contract()
    if len(pins.protected_head) != 40 or any(ch not in "0123456789abcdef" for ch in pins.protected_head):
        raise IncompleteAuthority("accepted protected head is not exactly pinned")
    resolved_head = _git(root, "rev-parse", "--verify", f"{pins.protected_head}^{{commit}}")
    resolved_ref = _git(root, "rev-parse", "--verify", f"{AUTHORITY_PROTECTED_REF}^{{commit}}")
    if resolved_head != pins.protected_head or resolved_ref != pins.protected_head:
        raise IncompleteAuthority("protected head/ref exact equality failed")
    for ancestor in AUTHORITY_EXPECTED_ANCESTORS:
        _git(root, "merge-base", "--is-ancestor", ancestor, pins.protected_head)
    for path, expected_hash in ((CONTRACT_PATH.relative_to(ROOT).as_posix(), CONTRACT_SHA256),
                                (PLAN_PATH.relative_to(ROOT).as_posix(), PLAN_SHA256)):
        frozen_raw = _git(root, "show", f"{pins.protected_head}:{path}", binary=True)
        assert isinstance(frozen_raw, bytes)
        if _sha256_bytes(frozen_raw) != expected_hash:
            raise IncompleteAuthority(f"protected tree frozen metadata mismatch: {path}")
    decoded: dict[str, dict] = {}
    for name, expected_path in EXPECTED_AUTHORITY_PATHS.items():
        pin = getattr(pins, name)
        pin.validate_shape(expected_path)
        raw = _git(root, "show", f"{pins.protected_head}:{pin.path}", binary=True)
        assert isinstance(raw, bytes)
        if _sha256_bytes(raw) != pin.byte_sha256:
            raise IncompleteAuthority(f"authority-v6 byte mismatch: {pin.path}")
        doc = _decode_json_bytes(Path(pin.path), raw)
        if (_declared_content_digest(doc) != pin.content_digest
                or _recomputed_content_digest(doc) != pin.content_digest):
            raise IncompleteAuthority(f"authority-v6 content digest mismatch: {pin.path}")
        decoded[name] = doc
    manifest, pit, filters, coverage = (decoded[k] for k in EXPECTED_AUTHORITY_PATHS)
    if manifest.get("format") != "zb-binance-um-archive-manifest/6":
        raise IncompleteAuthority("manifest-v6 format mismatch")
    if pit.get("format") != "zb-pit-universe/2":
        raise IncompleteAuthority("PIT-v9 format mismatch")
    if (filters.get("authority"), filters.get("version")) != (
        "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", 4
    ):
        raise IncompleteAuthority("filter-v4 identity mismatch")
    if "weeks" in pit:
        _validate_emitted_v6(decoded, pins, root=root)
        return decoded
    _validate_coverage_and_manifest(manifest, coverage)
    _validate_full_authority_schema(pit, filters)
    for key, expected in (("manifest_v6_byte_sha256", pins.manifest_v6.byte_sha256),
                          ("manifest_v6_content_digest", pins.manifest_v6.content_digest)):
        _require_binding(pit, key, expected)
    if pit.get("bindings") != {
            "manifest_v6_byte_sha256": pins.manifest_v6.byte_sha256,
            "manifest_v6_content_digest": pins.manifest_v6.content_digest}:
        raise IncompleteAuthority("PIT-v9 exact manifest cross-bindings mismatch")
    for key, expected in (("pit_v9_byte_sha256", pins.pit_v9.byte_sha256),
                          ("pit_v9_content_digest", pins.pit_v9.content_digest)):
        _require_binding(filters, key, expected)
    if filters.get("bindings") != {
            "pit_v9_byte_sha256": pins.pit_v9.byte_sha256,
            "pit_v9_content_digest": pins.pit_v9.content_digest}:
        raise IncompleteAuthority("filter-v4 exact PIT cross-bindings mismatch")
    for name in ("manifest_v6", "pit_v9", "filter_v4"):
        pin = getattr(pins, name)
        _require_binding(coverage, f"{name}_byte_sha256", pin.byte_sha256)
        _require_binding(coverage, f"{name}_content_digest", pin.content_digest)
    expected_coverage_bindings = {key: value for name in ("manifest_v6", "pit_v9", "filter_v4")
        for key, value in ((f"{name}_byte_sha256", getattr(pins, name).byte_sha256),
                           (f"{name}_content_digest", getattr(pins, name).content_digest))}
    if coverage.get("bindings") != expected_coverage_bindings:
        raise IncompleteAuthority("coverage-v4 exact cross-bindings mismatch")
    return decoded


def _validate_emitted_v6(docs: Mapping[str, dict], pins: AuthorityPins, *, root: Path) -> None:
    """Validate the exact schema emitted by the protected authority-v6 builder.

    All four protected-tree byte hashes and recomputed content digests have
    already passed before this function is called. Do not infer aliases for
    fields that the real producer does not emit.
    """
    from tools.research import authority_v6 as V6

    manifest, pit, filters, coverage = (docs[name] for name in EXPECTED_AUTHORITY_PATHS)
    try:
        V6._validate_pit_v9_prefix(pit, root=root)
    except (V6.AuthorityV6Error, KeyError, TypeError, ValueError) as exc:
        raise IncompleteAuthority("PIT-v9 frozen prefix/extension mismatch") from exc
    if (manifest.get("authority_id") != AUTHORITY_ID
            or manifest.get("cutoff_exclusive_ms") != V6.CUTOFF_MS
            or pit.get("manifest_digest") != pins.manifest_v6.content_digest):
        raise IncompleteAuthority("emitted manifest/PIT identity mismatch")
    if (filters.get("pit_universe_path") != pins.pit_v9.path
            or filters.get("pit_universe_sha256") != pins.pit_v9.byte_sha256
            or filters.get("pit_universe_digest") != pins.pit_v9.content_digest):
        raise IncompleteAuthority("emitted filter/PIT cross-binding mismatch")
    plan = V6.load_frozen_plan(root / V6.PLAN_PATH.relative_to(V6.ROOT))
    if filters.get("fold_bindings") != plan["acquisition_sequence"][5]["fold_bindings"]:
        raise IncompleteAuthority("emitted filter fold bindings mismatch")
    streams = ["klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null"]
    if (coverage.get("format") != "zb-strategy-causal-pack-01-authority-coverage-preflight/1"
            or coverage.get("authority_version") != 4
            or coverage.get("cutoff_exclusive_ms") != V6.CUTOFF_MS
            or coverage.get("required_result") != "ZERO_GUARANTEED_PERIOD_GAPS"
            or coverage.get("required_streams") != streams
            or coverage.get("guaranteed_period_gaps") != 0
            or coverage.get("guaranteed_period_gaps_by_stream") != dict.fromkeys(streams, 0)
            or coverage.get("gaps") != []
            or coverage.get("manifest_digest") != pins.manifest_v6.content_digest
            or coverage.get("pit_universe_digest") != pins.pit_v9.content_digest
            or coverage.get("filter_authority_digest") != pins.filter_v4.content_digest):
        raise IncompleteAuthority("emitted coverage is incomplete or cross-bound incorrectly")


def run_with_authority(pins: AuthorityPins, loader: Callable[[dict[str, dict]], object], *,
                       root: Path = ROOT) -> object:
    """The only real-data boundary: pins and schemas pass before loader invocation."""
    authorities = validate_authority_pins(pins, root=root)
    return loader(authorities)


def assert_real_evaluation_authorized() -> None:
    load_frozen_contract()
    raise EvaluationLocked(
        "C258_REAL_EVALUATION_LOCKED: authority-v6 output byte/content pins and accepted protected head "
        "are absent from the frozen contract; no loader was invoked"
    )


@dataclass(frozen=True)
class Bar:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float

    def validate(self) -> None:
        if not isinstance(self.open_ms, int) or self.open_ms % HOUR_MS:
            raise IncompleteAuthority("bar timestamp is not on the canonical 1h grid")
        values = (self.open, self.high, self.low, self.close, self.quote_volume)
        if not all(math.isfinite(v) and v > 0 for v in values):
            raise IncompleteAuthority("bar fields must be finite and positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.low > self.high:
            raise IncompleteAuthority("inconsistent OHLC")


def exact_index(bars: Sequence[Bar]) -> dict[int, Bar]:
    out: dict[int, Bar] = {}
    for bar in bars:
        bar.validate()
        if bar.open_ms in out:
            raise IncompleteAuthority("duplicate canonical 1h bar")
        out[bar.open_ms] = bar
    return out


def next_open(bars: Sequence[Bar], decision_close_ms: int) -> Bar:
    """Return only the exact next boundary; never bridge a missing canonical hour."""
    if not isinstance(decision_close_ms, int) or decision_close_ms % HOUR_MS:
        raise IncompleteAuthority("decision close is not a canonical 1h boundary")
    indexed = exact_index(bars)
    target = decision_close_ms + HOUR_MS
    if decision_close_ms not in indexed or target not in indexed:
        raise IncompleteAuthority("missing exact next canonical 1h open or continuity bar")
    return indexed[target]


def latest_pit_membership(cells: Sequence[Mapping[str, object]], decision_close_ms: int,
                          *, book_key: str = "crypto") -> tuple[str, ...]:
    """Greatest Monday cell <= decision, preserving artifact symbol order."""
    eligible = [cell for cell in cells
                if isinstance(cell.get("monday_ms"), int) and int(cell["monday_ms"]) <= decision_close_ms]
    if not eligible:
        raise IncompleteAuthority("no PIT-v9 weekly cell at or before decision")
    monday_ms = max(int(cell["monday_ms"]) for cell in eligible)
    matches = [cell for cell in cells if cell.get("monday_ms") == monday_ms]
    if len(matches) != 1:
        raise IncompleteAuthority("duplicate PIT-v9 weekly cell")
    cell = matches[0]
    books = cell.get("books")
    symbols = books.get(book_key) if isinstance(books, Mapping) else cell.get("symbols")
    if not isinstance(symbols, list) or not all(isinstance(value, str) and value for value in symbols):
        raise IncompleteAuthority("malformed PIT-v9 membership")
    if len(symbols) != len(set(symbols)):
        raise IncompleteAuthority("duplicate PIT-v9 member")
    return tuple(symbols)


def apply_filter_snapshot(pit_order: Sequence[str], eligible_symbols: Iterable[str]) -> tuple[str, ...]:
    """Apply the checksum-bound fold snapshot without reordering or survivor substitution."""
    allowed = set(eligible_symbols)
    if not all(isinstance(symbol, str) and symbol for symbol in allowed):
        raise IncompleteAuthority("malformed filter-v4 eligible membership")
    return tuple(symbol for symbol in pit_order if symbol in allowed)


def exact_close(index: Mapping[int, Bar], decision_close_ms: int, hours_ago: int) -> float:
    open_ms = decision_close_ms - (hours_ago + 1) * HOUR_MS
    if open_ms not in index:
        raise IncompleteAuthority("missing exact canonical close")
    return index[open_ms].close


def true_ranges_through(bars: Sequence[Bar], decision_close_ms: int) -> tuple[float, ...]:
    indexed = exact_index(bars)
    decision_open = decision_close_ms - HOUR_MS
    required = [decision_open - i * HOUR_MS for i in range(24, -1, -1)]
    if any(ts not in indexed for ts in required):
        raise IncompleteAuthority("ATR24 requires a contiguous 25-bar segment through decision")
    segment = [indexed[ts] for ts in required]
    return tuple(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
                 for prev, cur in zip(segment, segment[1:]))


def atr24_wilder(bars: Sequence[Bar], decision_close_ms: int) -> float:
    ranges = true_ranges_through(bars, decision_close_ms)
    atr = math.fsum(ranges[:24]) / 24
    for value in ranges[24:]:
        atr = ((23 * atr) + value) / 24
    if not math.isfinite(atr) or atr <= 0:
        raise IncompleteAuthority("ATR24 is non-positive or non-finite")
    return atr


@dataclass(frozen=True)
class ScoredSymbol:
    symbol: str
    r24: float
    z: float


@dataclass(frozen=True)
class IndexedPair:
    index: int
    long: ScoredSymbol
    short: ScoredSymbol


@dataclass(frozen=True)
class CrossSection:
    median: float
    mad: float
    scale: float
    scores: tuple[ScoredSymbol, ...]
    pairs: tuple[IndexedPair, ...]


def form_cross_section(symbol_bars: Mapping[str, Sequence[Bar]], decision_close_ms: int,
                       eligible_order: Sequence[str]) -> CrossSection | None:
    """Form the exact 24h robust cross-section; gaps exclude, never compress."""
    effective: list[tuple[str, float]] = []
    for symbol in eligible_order:
        try:
            indexed = exact_index(symbol_bars[symbol])
            current = exact_close(indexed, decision_close_ms, 0)
            old = exact_close(indexed, decision_close_ms, 24)
            atr24_wilder(symbol_bars[symbol], decision_close_ms)
        except (KeyError, IncompleteAuthority):
            continue
        value = current / old - 1
        if math.isfinite(value):
            effective.append((symbol, value))
    if len(effective) < 16:
        return None
    median = float(np.median(np.asarray([value for _, value in effective], dtype=np.float64)))
    mad = float(np.median(np.asarray([abs(value - median) for _, value in effective], dtype=np.float64)))
    scale = 1.4826 * mad
    if not math.isfinite(scale) or scale <= 0:
        return None
    scores = tuple(ScoredSymbol(symbol, value, (value - median) / scale) for symbol, value in effective)
    longs = sorted((row for row in scores if row.z >= 1.5), key=lambda row: (-row.z, row.symbol))
    shorts = sorted((row for row in scores if row.z <= -1.5), key=lambda row: (row.z, row.symbol))
    pairs = tuple(IndexedPair(i + 1, longs[i], shorts[i]) for i in range(min(3, len(longs), len(shorts))))
    return CrossSection(median, mad, scale, scores, pairs)


@dataclass
class EMA200State:
    closes: list[float] = field(default_factory=list)
    ema: float | None = None
    last_open_ms: int | None = None

    def update(self, bar: Bar) -> str | None:
        bar.validate()
        if self.last_open_ms is not None and bar.open_ms != self.last_open_ms + HOUR_MS:
            self.closes.clear()
            self.ema = None
        self.last_open_ms = bar.open_ms
        if self.ema is None:
            self.closes.append(bar.close)
            if len(self.closes) < 200:
                return None
            if len(self.closes) > 200:
                self.closes = self.closes[-200:]
            self.ema = math.fsum(self.closes) / 200
        else:
            self.ema = (2 / 201) * bar.close + (199 / 201) * self.ema
        if not math.isfinite(self.ema):
            raise IncompleteAuthority("non-finite BTC EMA200")
        return "BTC_ABOVE_OR_ON_EMA200" if bar.close >= self.ema else "BTC_BELOW_EMA200"


@dataclass(frozen=True)
class Filters:
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal

    def validate(self) -> None:
        if not (self.step_size > 0 and self.min_qty > 0 and self.max_qty >= self.min_qty and self.min_notional > 0):
            raise IncompleteAuthority("invalid exchange filters")


@dataclass(frozen=True)
class LegSpec:
    symbol: str
    side: int
    entry_reference: Decimal
    stop_reference: Decimal
    filters: Filters

    def validate(self) -> None:
        self.filters.validate()
        if self.side not in {-1, 1} or not self.symbol or self.entry_reference <= 0 or self.stop_reference <= 0:
            raise C258Error("invalid leg specification")
        if self.side == 1 and self.stop_reference >= self.entry_reference:
            raise C258Error("long stop must be below entry")
        if self.side == -1 and self.stop_reference <= self.entry_reference:
            raise C258Error("short stop must be above entry")


def _decimal(value: float | str | Decimal) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise C258Error("invalid decimal") from exc
    if not result.is_finite():
        raise C258Error("non-finite decimal")
    return result


def floor_step(quantity: Decimal, step: Decimal) -> Decimal:
    if quantity < 0 or step <= 0:
        raise C258Error("invalid step-floor input")
    return (quantity / step).to_integral_value(rounding=ROUND_FLOOR) * step


def stressed_loss_per_unit(leg: LegSpec) -> Decimal:
    leg.validate()
    entry = leg.entry_reference
    stop = leg.stop_reference
    fee = _decimal(FEE_BPS) / Decimal(10_000)
    slip = _decimal(STRESS_SLIPPAGE_BPS) / Decimal(10_000)
    # Fees use adverse execution prices; slippage drag uses raw references.
    entry_execution = entry * (Decimal(1) + slip if leg.side == 1 else Decimal(1) - slip)
    exit_execution = stop * (Decimal(1) - slip if leg.side == 1 else Decimal(1) + slip)
    return abs(entry - stop) + entry_execution * fee + entry * slip + exit_execution * fee + stop * slip


@dataclass(frozen=True)
class SizedPair:
    long_qty: Decimal
    short_qty: Decimal
    long_raw_notional: Decimal
    short_raw_notional: Decimal
    initial_pair_risk_cash: Decimal
    requested_pair_risk_cash: Decimal
    book_equity: Decimal
    decrements: int

    @property
    def gross(self) -> Decimal:
        return self.long_raw_notional + self.short_raw_notional


@dataclass(frozen=True)
class AcceptedPair:
    long: LegSpec
    short: LegSpec
    sizing: SizedPair

    def exact_initial_risk(self) -> Decimal:
        self.long.validate(); self.short.validate()
        if self.long.side != 1 or self.short.side != -1 or self.long.symbol == self.short.symbol:
            raise IncompleteEpisode("accepted pair symbols/sides are invalid")
        risk = (self.sizing.long_qty * stressed_loss_per_unit(self.long)
                + self.sizing.short_qty * stressed_loss_per_unit(self.short))
        long_notional = self.sizing.long_qty * self.long.entry_reference
        short_notional = self.sizing.short_qty * self.short.entry_reference
        if (not risk.is_finite() or risk <= Decimal("1e-12") or risk != self.sizing.initial_pair_risk_cash
                or self.sizing.long_qty <= 0 or self.sizing.short_qty <= 0
                or self.sizing.long_raw_notional != long_notional
                or self.sizing.short_raw_notional != short_notional
                or self.sizing.book_equity <= 0
                or self.sizing.requested_pair_risk_cash != self.sizing.book_equity * Decimal("0.01")
                or risk > self.sizing.requested_pair_risk_cash
                or self.sizing.gross > self.sizing.book_equity * Decimal("0.5")
                or abs(long_notional - short_notional) / max(long_notional, short_notional)
                > Decimal("0.01")):
            raise IncompleteEpisode("accepted pair initial risk is not exact, finite, and positive")
        return risk


def _valid_quantity(quantity: Decimal, reference: Decimal, filters: Filters) -> bool:
    return (quantity >= filters.min_qty and quantity <= filters.max_qty
            and quantity * reference >= filters.min_notional)


def size_pair(long: LegSpec, short: LegSpec, *, book_equity: float | Decimal,
              existing_remaining_stressed_stop_loss: float | Decimal = 0) -> SizedPair | None:
    """Frozen Decimal step-floor/decrement search; never increases either leg."""
    long.validate(); short.validate()
    if long.side != 1 or short.side != -1 or long.symbol == short.symbol:
        raise C258Error("sizing requires a distinct long/short pair")
    equity = _decimal(book_equity)
    existing = _decimal(existing_remaining_stressed_stop_loss)
    if equity <= 0 or existing < 0:
        raise IncompleteEpisode("book equity/risk must be finite and positive/nonnegative")
    requested = equity * Decimal("0.01")
    long_lpu, short_lpu = stressed_loss_per_unit(long), stressed_loss_per_unit(short)
    long_rate = long_lpu / long.entry_reference
    short_rate = short_lpu / short.entry_reference
    equal_notional = min(equity * Decimal("0.25"), requested / (long_rate + short_rate))
    long_qty = floor_step(equal_notional / long.entry_reference, long.filters.step_size)
    short_qty = floor_step(equal_notional / short.entry_reference, short.filters.step_size)
    for decrements in range(MAX_DECREMENTS + 1):
        if not _valid_quantity(long_qty, long.entry_reference, long.filters):
            return None
        if not _valid_quantity(short_qty, short.entry_reference, short.filters):
            return None
        ln, sn = long_qty * long.entry_reference, short_qty * short.entry_reference
        mismatch = abs(ln - sn) / max(ln, sn)
        risk = long_qty * long_lpu + short_qty * short_lpu
        post_ok = (risk > 0 and risk <= requested and ln + sn <= equity * Decimal("0.5")
                   and existing + risk <= equity * Decimal("0.03"))
        if mismatch <= Decimal("0.01") and post_ok:
            return SizedPair(long_qty, short_qty, ln, sn, risk, requested, equity, decrements)
        if mismatch > Decimal("0.01"):
            decrement_long = ln > sn
        elif ln != sn:
            decrement_long = ln > sn
        else:
            decrement_long = long.symbol > short.symbol
        if decrement_long:
            long_qty -= long.filters.step_size
        else:
            short_qty -= short.filters.step_size
    return None


@dataclass(frozen=True)
class FillCash:
    timestamp_ms: int
    symbol: str
    side: int
    quantity: float
    reference_price: float
    execution_price: float
    movement_cash: float
    fee_drag: float
    slippage_drag: float
    phase: str
    prior_quote_volume: float = 0.0
    stress: bool = False
    impact_incomplete: bool = False
    movement_from: float | None = None


def make_fill(*, timestamp_ms: int, symbol: str, side: int, quantity: float,
              reference_price: float, phase: str, movement_from: float | None,
              prior_quote_volume: float = 1_000_000_000.0, stress: bool = False) -> FillCash:
    if side not in {-1, 1} or phase not in {"entry", "exit", "stop", "cleanup"}:
        raise C258Error("invalid fill side/phase")
    if not all(math.isfinite(v) and v > 0 for v in (quantity, reference_price, prior_quote_volume)):
        raise C258Error("invalid fill quantity/reference/impact")
    if phase == "entry":
        if not entry_impact_feasible(quantity, reference_price, prior_quote_volume):
            raise IncompleteEpisode("entry exceeds frozen causal impact cap")
        impact_multiplier, incomplete = 1.0, False
    else:
        impact_multiplier, incomplete = required_exit_impact(quantity, reference_price, prior_quote_volume)
    slippage_bps = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    effective_slip = slippage_bps * max(1.0, impact_multiplier)
    # side is the transaction direction: +1 buy, -1 sell.
    execution = reference_price * (1 + side * effective_slip / 10_000)
    movement = 0.0 if movement_from is None else (-side) * quantity * (reference_price - movement_from)
    fee = abs(quantity * execution) * FEE_BPS / 10_000
    slip = abs(quantity) * abs(execution - reference_price)
    if not all(math.isfinite(v) for v in (execution, movement, fee, slip)) or min(fee, slip) < 0:
        raise C258Error("non-finite or signed drag")
    if fee <= 0 or slip <= 0:
        raise IncompleteEpisode("zero-cost fill is impossible under frozen costs")
    return FillCash(timestamp_ms, symbol, side, quantity, reference_price, execution,
                    movement, fee, slip, phase, prior_quote_volume, stress, incomplete, movement_from)


def _rederive_fill(fill: FillCash, *, stress: bool) -> FillCash:
    """Treat FillCash as raw references only; caller-supplied economics are discarded."""
    base = make_fill(timestamp_ms=fill.timestamp_ms, symbol=fill.symbol, side=fill.side,
                     quantity=fill.quantity, reference_price=fill.reference_price,
                     phase=fill.phase, movement_from=fill.movement_from,
                     prior_quote_volume=fill.prior_quote_volume, stress=False)
    supplied = (fill.execution_price, fill.movement_cash, fill.fee_drag, fill.slippage_drag,
                fill.impact_incomplete, fill.stress)
    expected = (base.execution_price, base.movement_cash, base.fee_drag, base.slippage_drag,
                base.impact_incomplete, False)
    if supplied != expected:
        raise IncompleteEpisode("caller-supplied fill economics do not match frozen derivation")
    if not stress:
        return base
    return make_fill(timestamp_ms=fill.timestamp_ms, symbol=fill.symbol, side=fill.side,
                     quantity=fill.quantity, reference_price=fill.reference_price,
                     phase=fill.phase, movement_from=fill.movement_from,
                     prior_quote_volume=fill.prior_quote_volume, stress=True)


def _derive_fill_from_bar_authority(fill: FillCash, *, leg: LegSpec,
                                    bars: Sequence[Bar], stress: bool,
                                    final_event: "_DerivedFinal | None" = None) -> FillCash:
    """Bind every raw reference and impact input to exact timestamped causal bars."""
    index = exact_index(bars)
    bar = index.get(fill.timestamp_ms)
    prior = index.get(fill.timestamp_ms - HOUR_MS)
    if bar is None or prior is None:
        raise IncompleteAuthority("fill is missing exact bar or strict-prior volume authority")
    if fill.phase == "entry":
        reference, movement_from = bar.open, None
    elif final_event is not None:
        reference, movement_from = final_event.reference, float(leg.entry_reference)
    else:
        frozen_stop = float(leg.stop_reference)
        if leg.side == 1:
            reference = min(frozen_stop, bar.open) if bar.open <= frozen_stop else (
                frozen_stop if bar.low <= frozen_stop else math.nan)
        else:
            reference = max(frozen_stop, bar.open) if bar.open >= frozen_stop else (
                frozen_stop if bar.high >= frozen_stop else math.nan)
        if not math.isfinite(reference):
            raise IncompleteEpisode("stop fill is not supported by frozen protection bar")
        movement_from = float(leg.entry_reference)
    if fill.reference_price != reference or fill.movement_from != movement_from:
        raise IncompleteEpisode("fill reference/impact is not exact frozen bar authority")
    base = make_fill(timestamp_ms=fill.timestamp_ms, symbol=fill.symbol, side=fill.side,
                     quantity=fill.quantity, reference_price=reference, phase=fill.phase,
                     movement_from=movement_from, prior_quote_volume=prior.quote_volume,
                     stress=False)
    if (fill.execution_price, fill.movement_cash, fill.fee_drag, fill.slippage_drag,
            fill.impact_incomplete, fill.stress) != (
            base.execution_price, base.movement_cash, base.fee_drag, base.slippage_drag,
            base.impact_incomplete, False):
        raise IncompleteEpisode("caller-supplied fill economics do not match frozen derivation")
    if not stress:
        return base
    return make_fill(timestamp_ms=fill.timestamp_ms, symbol=fill.symbol, side=fill.side,
                     quantity=fill.quantity, reference_price=reference, phase=fill.phase,
                     movement_from=movement_from, prior_quote_volume=prior.quote_volume,
                     stress=True)


@dataclass(frozen=True)
class _DerivedFinal:
    symbol: str
    timestamp_ms: int
    phase: str
    side: int
    quantity: Decimal
    reference: float
    source_open_ms: int
    stop_kind: str | None


def _first_leg_event(*, leg: LegSpec, quantity: Decimal, bars: Sequence[Bar],
                     entry_ms: int, due_ms: int) -> _DerivedFinal:
    index = exact_index(bars)
    stop = float(leg.stop_reference)
    for open_ms in range(entry_ms, due_ms + HOUR_MS, HOUR_MS):
        bar = index.get(open_ms)
        if bar is None:
            raise IncompleteAuthority("lifecycle tape is missing a canonical event bar")
        gap = bar.open <= stop if leg.side == 1 else bar.open >= stop
        touch = bar.low <= stop if leg.side == 1 else bar.high >= stop
        if gap:
            reference = min(stop, bar.open) if leg.side == 1 else max(stop, bar.open)
            return _DerivedFinal(leg.symbol, open_ms, "stop", -leg.side, quantity,
                                 reference, open_ms, "gap")
        if touch:
            return _DerivedFinal(leg.symbol, open_ms + HOUR_MS, "stop", -leg.side,
                                 quantity, stop, open_ms, "range")
        if open_ms == due_ms:
            return _DerivedFinal(leg.symbol, due_ms, "exit", -leg.side, quantity,
                                 bar.open, open_ms, None)
    raise IncompleteEpisode("no mandatory lifecycle event was derived")


def _derive_final_events(*, legs: Mapping[str, LegSpec], quantities: Mapping[str, Decimal],
                         symbol_bars: Mapping[str, Sequence[Bar]], entry_ms: int,
                         atomic_one_leg: bool) -> tuple[_DerivedFinal, ...]:
    due_ms = entry_ms + (HOUR_MS if atomic_one_leg else 6 * HOUR_MS)
    events = {symbol: _first_leg_event(leg=leg, quantity=quantities[symbol],
                                      bars=symbol_bars[symbol], entry_ms=entry_ms, due_ms=due_ms)
              for symbol, leg in legs.items()}
    if atomic_one_leg:
        event = next(iter(events.values()))
        if event.phase == "stop":
            return (event,)
        bar = exact_index(symbol_bars[event.symbol])[due_ms]
        return (_DerivedFinal(event.symbol, due_ms, "cleanup", event.side, event.quantity,
                              bar.open, due_ms, None),)
    first_stop = min((event for event in events.values() if event.phase == "stop"),
                     key=lambda event: (event.source_open_ms, event.symbol), default=None)
    if first_stop is None:
        return tuple(events[symbol] for symbol in sorted(events))
    same_bar_stops = [event for event in events.values()
                      if event.phase == "stop" and event.source_open_ms == first_stop.source_open_ms]
    if len(same_bar_stops) == len(events):
        return tuple(sorted(same_bar_stops, key=lambda event: event.symbol))
    cleanup_ms = first_stop.source_open_ms + HOUR_MS
    finals = [first_stop]
    for symbol, event in events.items():
        if symbol == first_stop.symbol:
            continue
        if (event.phase == "stop" and event.source_open_ms == first_stop.source_open_ms):
            finals.append(event)
            continue
        # At the cleanup boundary a stop gap is processed before the mate cleanup.
        if (event.phase == "stop" and event.stop_kind == "gap"
                and event.source_open_ms == cleanup_ms):
            finals.append(event)
            continue
        bar = exact_index(symbol_bars[symbol]).get(cleanup_ms)
        if bar is None:
            raise IncompleteAuthority("mate cleanup is missing the exact next canonical open")
        finals.append(_DerivedFinal(symbol, cleanup_ms, "cleanup", -legs[symbol].side,
                                    quantities[symbol], bar.open, cleanup_ms, None))
    return tuple(sorted(finals, key=lambda event: event.symbol))


def impact_ratio(quantity: float, reference_price: float, prior_quote_volume: float) -> float:
    if not all(math.isfinite(v) and v > 0 for v in (quantity, reference_price, prior_quote_volume)):
        raise IncompleteAuthority("impact authority is missing/non-positive")
    return abs(quantity * reference_price) / prior_quote_volume


def entry_impact_feasible(quantity: float, reference_price: float, prior_quote_volume: float) -> bool:
    return impact_ratio(quantity, reference_price, prior_quote_volume) <= 0.001


def required_exit_impact(quantity: float, reference_price: float,
                         prior_quote_volume: float) -> tuple[float, bool]:
    """Required exits always execute; return slippage multiplier and incompleteness."""
    ratio = impact_ratio(quantity, reference_price, prior_quote_volume)
    return max(1.0, ratio / 0.001), ratio > 0.001


def stop_reference(*, position_side: int, entry_reference: float, atr24: float, bar: Bar) -> float | None:
    bar.validate()
    if position_side not in {-1, 1} or not all(math.isfinite(v) and v > 0 for v in (entry_reference, atr24)):
        raise C258Error("invalid stop inputs")
    stop = entry_reference - 2 * atr24 if position_side == 1 else entry_reference + 2 * atr24
    if stop <= 0:
        raise C258Error("non-positive frozen stop")
    if position_side == 1:
        if bar.open <= stop:
            return min(stop, bar.open)
        return stop if bar.low <= stop else None
    if bar.open >= stop:
        return max(stop, bar.open)
    return stop if bar.high >= stop else None


def signed_funding_cash(*, position_side: int, quantity: float, causal_mark: float, funding_rate: float,
                        stress: bool = False) -> float:
    if position_side not in {-1, 1} or not all(math.isfinite(v) for v in (quantity, causal_mark, funding_rate)):
        raise IncompleteAuthority("invalid funding authority")
    if quantity <= 0 or causal_mark <= 0:
        raise IncompleteAuthority("invalid funding quantity/mark")
    cash = -position_side * abs(quantity) * causal_mark * funding_rate
    if stress:
        cash = cash * 1.5 if cash < 0 else cash / 1.5
    return cash


@dataclass(frozen=True)
class MarkClose:
    close_ms: int
    price: float


def causal_mark_strictly_before(marks: Sequence[MarkClose], event_ms: int) -> MarkClose:
    seen: set[int] = set()
    eligible = []
    for mark in marks:
        if mark.close_ms in seen:
            raise IncompleteAuthority("duplicate 1h mark close")
        seen.add(mark.close_ms)
        if not isinstance(mark.close_ms, int) or not math.isfinite(mark.price) or mark.price <= 0:
            raise IncompleteAuthority("invalid 1h mark close")
        if mark.close_ms < event_ms:
            eligible.append(mark)
    if not eligible:
        raise IncompleteAuthority("missing causal mark strictly before event")
    result = max(eligible, key=lambda mark: mark.close_ms)
    if result.close_ms != event_ms - HOUR_MS:
        raise IncompleteAuthority("strict-prior causal mark timestamp is missing or stale")
    return result


def funding_belongs_to_exposure(*, nominal_time_ms: int, entry_open_ms: int, exit_open_ms: int) -> bool:
    """Entry precedes same-time funding; open exit precedes and excludes it."""
    if exit_open_ms <= entry_open_ms:
        raise C258Error("invalid exposure interval")
    return entry_open_ms <= nominal_time_ms < exit_open_ms


OPEN_EVENT_ORDER = ("FUNDING_STRICTLY_BEFORE_OPEN", "CAUSAL_MARK", "DUE_EXITS_AND_CLEANUP",
                    "BOOK_EQUITY", "NEW_ENTRIES", "FUNDING_AT_OPEN")


@dataclass(frozen=True)
class ClosedAttempt:
    attempt_id: str
    entry_ms: int
    close_ms: int
    long_symbol: str
    short_symbol: str
    regime: str
    movement_cash: float
    funding_cash: float
    fee_drag: float
    slippage_drag: float
    actual_net_cash: float
    verdict_net_cash: float
    initial_risk_cash: float
    verdict_R: float
    atomic_one_leg_loss: bool
    conservative_adjustment_cash: float
    gross_R: float = 0.0
    long_contribution_R: float = 0.0
    short_contribution_R: float = 0.0
    lifecycle_identity: tuple[tuple[object, ...], ...] = ()
    fills: tuple[FillCash, ...] = ()
    funding_events: tuple["FundingCashEvent", ...] = ()
    legs: tuple["LegResult", ...] = ()
    derivation_identity_sha256: str = ""


@dataclass(frozen=True)
class FundingCashEvent:
    timestamp_ms: int
    symbol: str
    cash: float


def derive_funding_events(*, funding_points: Sequence["FundingPoint"],
                          funding_authority: Sequence["FundingPoint"],
                          symbol_marks: Mapping[str, Sequence[MarkClose]],
                          position_sides: Mapping[str, int], quantities: Mapping[str, Decimal],
                          entry_ms: int, close_ms: int, stress: bool) -> tuple[FundingCashEvent, ...]:
    authority: dict[tuple[str, int], float] = {}
    for row in funding_authority:
        key = (row.symbol, row.nominal_time_ms)
        if key in authority or not math.isfinite(row.rate):
            raise IncompleteAuthority("duplicate or invalid funding authority")
        authority[key] = row.rate
    eight_hours = 8 * HOUR_MS
    first = ((entry_ms + eight_hours - 1) // eight_hours) * eight_hours
    required = {(symbol, timestamp) for symbol in position_sides
                for timestamp in range(first, close_ms, eight_hours)}
    if set(authority) != required:
        raise IncompleteEpisode("funding authority does not exactly cover canonical 8h exposure settlements")
    seen: set[tuple[str, int]] = set()
    events = []
    for row in funding_points:
        key = (row.symbol, row.nominal_time_ms)
        if key in seen:
            raise IncompleteEpisode("duplicate funding row")
        seen.add(key)
        if (row.symbol not in position_sides or not entry_ms <= row.nominal_time_ms < close_ms
                or key not in authority or authority[key] != row.rate):
            raise IncompleteEpisode("funding row is not exactly bound to exposure authority")
        mark = causal_mark_strictly_before(symbol_marks.get(row.symbol, ()), row.nominal_time_ms)
        cash = signed_funding_cash(position_side=position_sides[row.symbol],
                                   quantity=float(quantities[row.symbol]), causal_mark=mark.price,
                                   funding_rate=row.rate, stress=stress)
        events.append(FundingCashEvent(row.nominal_time_ms, row.symbol, cash))
    if seen != required:
        raise IncompleteEpisode("funding rows do not exactly cover exposure authority")
    return tuple(events)


@dataclass(frozen=True)
class LegResult:
    symbol: str
    position_side: int
    quantity: float
    movement_cash: float
    funding_cash: float
    fee_drag: float
    slippage_drag: float
    net_cash: float
    net_R: float


def attempt_derivation_digest(row: ClosedAttempt) -> str:
    payload = {
        "attempt_id": row.attempt_id, "entry_ms": row.entry_ms, "close_ms": row.close_ms,
        "long_symbol": row.long_symbol, "short_symbol": row.short_symbol, "regime": row.regime,
        "movement_cash": row.movement_cash, "funding_cash": row.funding_cash,
        "fee_drag": row.fee_drag, "slippage_drag": row.slippage_drag,
        "actual_net_cash": row.actual_net_cash, "verdict_net_cash": row.verdict_net_cash,
        "initial_risk_cash": row.initial_risk_cash, "verdict_R": row.verdict_R,
        "atomic_one_leg_loss": row.atomic_one_leg_loss,
        "conservative_adjustment_cash": row.conservative_adjustment_cash,
        "gross_R": row.gross_R, "long_contribution_R": row.long_contribution_R,
        "short_contribution_R": row.short_contribution_R,
        "lifecycle_identity": row.lifecycle_identity,
        "fills": [asdict(fill) for fill in row.fills],
        "funding_events": [asdict(event) for event in row.funding_events],
        "legs": [asdict(leg) for leg in row.legs],
    }
    return _sha256_bytes(_canonical_bytes(payload))


def bind_attempt_derivation(row: ClosedAttempt) -> ClosedAttempt:
    return replace(row, derivation_identity_sha256=attempt_derivation_digest(row))


def _fill_lifecycle_identity(fills: Sequence[FillCash]) -> tuple[tuple[object, ...], ...]:
    return tuple((fill.timestamp_ms, fill.symbol, fill.side, repr(fill.quantity),
                  repr(fill.reference_price), fill.phase) for fill in fills)


def _validate_fill_lifecycle(*, fills: Sequence[FillCash], entry_ms: int, close_ms: int,
                             declared_sides: Mapping[str, int], atomic_one_leg: bool) -> dict[str, Decimal]:
    by_symbol: dict[str, list[FillCash]] = {}
    for fill in fills:
        by_symbol.setdefault(fill.symbol, []).append(fill)
    if set(by_symbol) != set(declared_sides):
        raise IncompleteEpisode("fill symbols do not match the accepted lifecycle")
    if len(fills) != 2 * len(declared_sides):
        raise IncompleteEpisode("accepted lifecycle is not fully flat")
    accepted: dict[str, Decimal] = {}
    for symbol, position_side in declared_sides.items():
        symbol_fills = by_symbol[symbol]
        entries = [fill for fill in symbol_fills if fill.phase == "entry"]
        closes = [fill for fill in symbol_fills if fill.phase in {"exit", "stop", "cleanup"}]
        if len(entries) != 1 or len(closes) != 1:
            raise IncompleteEpisode("each accepted leg requires one entry and one complete close")
        entry, final = entries[0], closes[0]
        if entry.side != position_side or final.side != -position_side:
            raise IncompleteEpisode("fill side does not match the accepted leg")
        if entry.movement_from is not None or final.movement_from != entry.reference_price:
            raise IncompleteEpisode("movement reference is not bound to the accepted entry reference")
        accepted_quantity = Decimal(str(entry.quantity))
        if accepted_quantity <= 0 or Decimal(str(final.quantity)) != accepted_quantity:
            raise IncompleteEpisode("fill quantity does not conserve the accepted quantity")
        accepted[symbol] = accepted_quantity
        if entry.timestamp_ms != entry_ms:
            raise IncompleteEpisode("entry timestamp does not match the accepted lifecycle")
        if final.timestamp_ms <= entry.timestamp_ms:
            raise IncompleteEpisode("close must occur after the entry")
    if max(fill.timestamp_ms for fill in fills if fill.phase != "entry") != close_ms:
        raise IncompleteEpisode("attempt close time is not the final lifecycle fill")
    if atomic_one_leg and len(declared_sides) != 1:
        raise IncompleteEpisode("atomic failure must contain exactly one accepted leg")
    if not atomic_one_leg and len(declared_sides) != 2:
        raise IncompleteEpisode("normal pair must contain both accepted legs")
    return accepted


def close_attempt(*, attempt_id: str, entry_ms: int, close_ms: int, long_symbol: str,
                  short_symbol: str, regime: str, fills: Sequence[FillCash],
                  symbol_bars: Mapping[str, Sequence[Bar]],
                  accepted_pair: AcceptedPair | None = None,
                  accepted_atomic_leg: LegSpec | None = None,
                  funding_points: Sequence["FundingPoint"] = (),
                  funding_authority: Sequence["FundingPoint"] = (),
                  funding_marks: Mapping[str, Sequence[MarkClose]] = {},
                  stress: bool = False,
                  atomic_one_leg: bool = False) -> ClosedAttempt:
    if not fills or close_ms < entry_ms:
        raise IncompleteEpisode("attempt cashflows are incomplete")
    present_symbols = {fill.symbol for fill in fills}
    pair_sides = {long_symbol: 1, short_symbol: -1}
    if atomic_one_leg:
        if len(present_symbols) != 1 or not present_symbols <= set(pair_sides):
            raise IncompleteEpisode("atomic accepted leg is not one declared pair symbol")
        declared_sides = {symbol: pair_sides[symbol] for symbol in present_symbols}
    else:
        declared_sides = pair_sides
    if set(symbol_bars) != set(declared_sides):
        raise IncompleteAuthority("fill bar authority symbols do not match accepted lifecycle")
    if atomic_one_leg:
        if accepted_atomic_leg is None:
            raise IncompleteEpisode("atomic lifecycle requires accepted LegSpec")
        legs_by_symbol = {accepted_atomic_leg.symbol: accepted_atomic_leg}
    else:
        if accepted_pair is None:
            raise IncompleteEpisode("normal lifecycle requires accepted pair")
        legs_by_symbol = {accepted_pair.long.symbol: accepted_pair.long,
                          accepted_pair.short.symbol: accepted_pair.short}
    entry_fills = [fill for fill in fills if fill.phase == "entry"]
    if (len(entry_fills) != len(legs_by_symbol)
            or {fill.symbol for fill in entry_fills} != set(legs_by_symbol)
            or any(fill.timestamp_ms != entry_ms or fill.quantity <= 0 for fill in entry_fills)):
        raise IncompleteEpisode("submitted lifecycle entries are not exact")
    proposed_quantities = {fill.symbol: Decimal(str(fill.quantity)) for fill in entry_fills}
    authority_end = entry_ms + (2 if atomic_one_leg else 7) * HOUR_MS
    required_bar_times = set(range(entry_ms-HOUR_MS, authority_end+HOUR_MS, HOUR_MS))
    for symbol, bars in symbol_bars.items():
        available = set(exact_index(bars))
        if not required_bar_times <= available:
            raise IncompleteAuthority(f"{symbol} lifecycle bar authority is missing/compressed")
    expected_finals = _derive_final_events(
        legs=legs_by_symbol, quantities=proposed_quantities, symbol_bars=symbol_bars,
        entry_ms=entry_ms, atomic_one_leg=atomic_one_leg)
    submitted_finals = [fill for fill in fills if fill.phase != "entry"]
    expected_identity = {(event.symbol, event.timestamp_ms, event.phase, event.side,
                          float(event.quantity), event.reference) for event in expected_finals}
    submitted_identity = {(fill.symbol, fill.timestamp_ms, fill.phase, fill.side,
                           fill.quantity, fill.reference_price) for fill in submitted_finals}
    derived_close_ms = max(event.timestamp_ms for event in expected_finals)
    if (len(submitted_finals) != len(expected_finals) or submitted_identity != expected_identity
            or close_ms != derived_close_ms):
        raise IncompleteEpisode("submitted fills do not match deterministic lifecycle replay")
    finals_by_symbol = {event.symbol: event for event in expected_finals}
    fills = tuple(_derive_fill_from_bar_authority(
        fill, leg=legs_by_symbol[fill.symbol], bars=symbol_bars[fill.symbol], stress=stress,
        final_event=None if fill.phase == "entry" else finals_by_symbol[fill.symbol])
        for fill in fills)
    accepted = _validate_fill_lifecycle(fills=fills, entry_ms=entry_ms, close_ms=close_ms,
                                        declared_sides=declared_sides, atomic_one_leg=atomic_one_leg)
    if atomic_one_leg:
        if accepted_pair is not None or accepted_atomic_leg is None:
            raise IncompleteEpisode("atomic lifecycle requires exactly its accepted LegSpec")
        accepted_atomic_leg.validate()
        only_symbol = next(iter(declared_sides))
        if accepted_atomic_leg.symbol != only_symbol or accepted_atomic_leg.side != declared_sides[only_symbol]:
            raise IncompleteEpisode("atomic accepted leg does not match lifecycle")
        entry_fill = next(fill for fill in fills if fill.phase == "entry")
        if Decimal(str(entry_fill.reference_price)) != accepted_atomic_leg.entry_reference:
            raise IncompleteEpisode("atomic entry fill is not bound to LegSpec reference")
        initial_risk = accepted[only_symbol] * stressed_loss_per_unit(accepted_atomic_leg)
    else:
        if accepted_pair is None or accepted_atomic_leg is not None:
            raise IncompleteEpisode("normal lifecycle requires exact SizedPair and LegSpecs")
        initial_risk = accepted_pair.exact_initial_risk()
        expected_qty = {accepted_pair.long.symbol: accepted_pair.sizing.long_qty,
                        accepted_pair.short.symbol: accepted_pair.sizing.short_qty}
        if (accepted_pair.long.symbol != long_symbol or accepted_pair.short.symbol != short_symbol
                or accepted != expected_qty):
            raise IncompleteEpisode("fills do not match exact accepted SizedPair quantities")
        entry_refs = {fill.symbol: Decimal(str(fill.reference_price))
                      for fill in fills if fill.phase == "entry"}
        if entry_refs != {long_symbol: accepted_pair.long.entry_reference,
                          short_symbol: accepted_pair.short.entry_reference}:
            raise IncompleteEpisode("entry fills are not bound to LegSpec references")
    if not initial_risk.is_finite() or initial_risk <= Decimal("1e-12"):
        raise IncompleteEpisode("initial risk must be finite and positive")
    initial_risk_cash = float(initial_risk)
    funding_events = derive_funding_events(
        funding_points=funding_points, funding_authority=funding_authority,
        symbol_marks=funding_marks, position_sides=declared_sides, quantities=accepted,
        entry_ms=entry_ms, close_ms=close_ms, stress=stress)
    funding_cash = math.fsum(event.cash for event in funding_events)
    movement = math.fsum(fill.movement_cash for fill in fills)
    fee = math.fsum(fill.fee_drag for fill in fills)
    slippage = math.fsum(fill.slippage_drag for fill in fills)
    actual = movement + funding_cash - fee - slippage
    verdict = min(actual, -(fee + slippage)) if atomic_one_leg else actual
    adjustment = verdict - actual
    if not math.isfinite(initial_risk_cash) or initial_risk_cash <= 0:
        raise IncompleteEpisode("initial risk must be finite and positive")
    if min(fee, slippage) < 0 or (atomic_one_leg and (verdict > 0 or adjustment > 1e-12)):
        raise IncompleteEpisode("atomic verdict or drag sign violated")
    funding_events = tuple(funding_events)
    leg_rows = []
    for symbol, position_side in declared_sides.items():
        symbol_fills = [fill for fill in fills if fill.symbol == symbol]
        leg_movement = math.fsum(fill.movement_cash for fill in symbol_fills)
        leg_fee = math.fsum(fill.fee_drag for fill in symbol_fills)
        leg_slippage = math.fsum(fill.slippage_drag for fill in symbol_fills)
        leg_funding = math.fsum(event.cash for event in funding_events if event.symbol == symbol)
        leg_net = leg_movement + leg_funding - leg_fee - leg_slippage
        leg_rows.append(LegResult(symbol, position_side, float(accepted[symbol]), leg_movement,
                                  leg_funding, leg_fee, leg_slippage, leg_net,
                                  leg_net / initial_risk_cash))
    if not math.isclose(math.fsum(leg.net_cash for leg in leg_rows), actual, rel_tol=0, abs_tol=1e-12):
        raise IncompleteEpisode("pair actual total is not exact fsum of leg cashflows")
    if atomic_one_leg:
        leg = leg_rows[0]
        adjusted = leg.net_cash + adjustment
        leg_rows[0] = LegResult(leg.symbol, leg.position_side, leg.quantity, leg.movement_cash,
                                leg.funding_cash, leg.fee_drag, leg.slippage_drag,
                                adjusted, adjusted / initial_risk_cash)
    if not math.isclose(math.fsum(leg.net_cash for leg in leg_rows), verdict, rel_tol=0, abs_tol=1e-12):
        raise IncompleteEpisode("pair verdict is not exact fsum of leg cashflows")
    contributions = {leg.symbol: leg.net_R for leg in leg_rows}
    row = ClosedAttempt(attempt_id, entry_ms, close_ms, long_symbol, short_symbol, regime,
                         movement, funding_cash, fee, slippage, actual, verdict,
                         initial_risk_cash, verdict / initial_risk_cash, atomic_one_leg, adjustment,
                         movement / initial_risk_cash, contributions.get(long_symbol, 0.0),
                         contributions.get(short_symbol, 0.0), _fill_lifecycle_identity(fills),
                         tuple(fills), funding_events, tuple(leg_rows))
    return bind_attempt_derivation(row)


def account_atomic_one_leg(*, attempt_id: str, expected_long_symbol: str, expected_short_symbol: str,
                           accepted_leg: LegSpec, fills: Sequence[FillCash],
                           symbol_bars: Mapping[str, Sequence[Bar]],
                           funding_points: Sequence["FundingPoint"] = (),
                           funding_authority: Sequence["FundingPoint"] = (),
                           funding_marks: Mapping[str, Sequence[MarkClose]] = {},
                           regime: str, stress: bool = False) -> ClosedAttempt:
    accepted_leg.validate()
    if len(fills) != 2:
        raise IncompleteEpisode("atomic accepted quantity/lifecycle is incomplete")
    entry = next((fill for fill in fills if fill.phase == "entry"), None)
    final = next((fill for fill in fills if fill.phase in {"stop", "cleanup"}), None)
    if entry is None or final is None or entry.symbol != accepted_leg.symbol or final.symbol != accepted_leg.symbol:
        raise IncompleteEpisode("atomic fills do not match the accepted leg")
    if final.timestamp_ms <= entry.timestamp_ms:
        raise IncompleteEpisode("atomic cleanup/stop must occur after the entry")
    declared_side = 1 if accepted_leg.symbol == expected_long_symbol else (
        -1 if accepted_leg.symbol == expected_short_symbol else 0)
    if declared_side == 0 or accepted_leg.side != declared_side:
        raise IncompleteEpisode("atomic leg symbol/side is not one declared pair leg")
    if final.phase == "stop":
        if accepted_leg.side == 1 and final.reference_price > float(accepted_leg.stop_reference):
            raise IncompleteEpisode("long atomic stop was filled favorably above its frozen reference")
        if accepted_leg.side == -1 and final.reference_price < float(accepted_leg.stop_reference):
            raise IncompleteEpisode("short atomic stop was filled favorably below its frozen reference")
    return close_attempt(attempt_id=attempt_id, entry_ms=entry.timestamp_ms, close_ms=final.timestamp_ms,
                         long_symbol=expected_long_symbol, short_symbol=expected_short_symbol,
                         regime=regime, fills=fills, symbol_bars=symbol_bars,
                         accepted_atomic_leg=accepted_leg,
                         funding_points=funding_points, funding_authority=funding_authority,
                         funding_marks=funding_marks, stress=stress,
                         atomic_one_leg=True)


def mechanical_stress_attempt(base: ClosedAttempt) -> ClosedAttempt:
    """Replay the same lifecycle with frozen 5bp fee, 10bp adverse slippage and funding rule."""
    fills = tuple(_rederive_fill(fill, stress=True) for fill in base.fills)
    funding_events = tuple(FundingCashEvent(event.timestamp_ms, event.symbol,
        event.cash * 1.5 if event.cash < 0 else event.cash / 1.5)
        for event in base.funding_events)
    movement = math.fsum(fill.movement_cash for fill in fills)
    fee = math.fsum(fill.fee_drag for fill in fills)
    slippage = math.fsum(fill.slippage_drag for fill in fills)
    funding = math.fsum(event.cash for event in funding_events)
    actual = movement + funding - fee - slippage
    verdict = min(actual, -(fee + slippage)) if base.atomic_one_leg_loss else actual
    adjustment = verdict - actual
    legs = []
    for source in base.legs:
        symbol_fills = [fill for fill in fills if fill.symbol == source.symbol]
        leg_movement = math.fsum(fill.movement_cash for fill in symbol_fills)
        leg_fee = math.fsum(fill.fee_drag for fill in symbol_fills)
        leg_slip = math.fsum(fill.slippage_drag for fill in symbol_fills)
        leg_funding = math.fsum(event.cash for event in funding_events if event.symbol == source.symbol)
        leg_net = leg_movement + leg_funding - leg_fee - leg_slip
        if base.atomic_one_leg_loss:
            leg_net += adjustment
        legs.append(LegResult(source.symbol, source.position_side, source.quantity, leg_movement,
                              leg_funding, leg_fee, leg_slip, leg_net,
                              leg_net / base.initial_risk_cash))
    contributions = {leg.symbol: leg.net_R for leg in legs}
    row = replace(base, movement_cash=movement, funding_cash=funding, fee_drag=fee,
                  slippage_drag=slippage, actual_net_cash=actual, verdict_net_cash=verdict,
                  verdict_R=verdict / base.initial_risk_cash,
                  conservative_adjustment_cash=adjustment,
                  gross_R=movement / base.initial_risk_cash,
                  long_contribution_R=contributions.get(base.long_symbol, 0.0),
                  short_contribution_R=contributions.get(base.short_symbol, 0.0),
                  fills=fills, funding_events=funding_events, legs=tuple(legs),
                  derivation_identity_sha256="")
    return bind_attempt_derivation(row)


@dataclass(frozen=True)
class FundingPoint:
    symbol: str
    nominal_time_ms: int
    rate: float


def validate_exposure_authority(*, symbol_bars: Mapping[str, Sequence[Bar]],
                                symbol_marks: Mapping[str, Sequence[MarkClose]],
                                funding_points: Sequence[FundingPoint],
                                entry_ms: int, exit_ms: int) -> None:
    if exit_ms <= entry_ms or any(value % HOUR_MS for value in (entry_ms, exit_ms)):
        raise IncompleteEpisode("exposure boundaries are not canonical")
    symbols = set(symbol_bars)
    if not symbols or set(symbol_marks) != symbols:
        raise IncompleteEpisode("bar/mark exposure symbols differ")
    bar_grid = set(range(entry_ms, exit_ms + HOUR_MS, HOUR_MS))
    mark_grid = set(range(entry_ms - HOUR_MS, exit_ms, HOUR_MS))
    for symbol in symbols:
        indexed_bars = exact_index(symbol_bars[symbol])
        bar_times = {timestamp for timestamp in indexed_bars if entry_ms <= timestamp <= exit_ms}
        selected_marks = [mark for mark in symbol_marks[symbol]
                          if entry_ms - HOUR_MS <= mark.close_ms < exit_ms]
        mark_times = {mark.close_ms for mark in selected_marks}
        if (len(mark_times) != len(selected_marks)
                or any(not math.isfinite(mark.price) or mark.price <= 0 for mark in selected_marks)):
            raise IncompleteEpisode("duplicate/non-finite mark authority across exposure")
        if bar_times != bar_grid or mark_times != mark_grid:
            raise IncompleteEpisode("exposure bar/mark authority is not contiguous")
    eight_hours = 8 * HOUR_MS
    first_funding = ((entry_ms + eight_hours - 1) // eight_hours) * eight_hours
    expected = {(symbol, timestamp) for symbol in symbols
                for timestamp in range(first_funding, exit_ms, eight_hours)}
    selected_funding = [row for row in funding_points if entry_ms <= row.nominal_time_ms < exit_ms]
    observed = {(row.symbol, row.nominal_time_ms) for row in selected_funding}
    if (len(observed) != len(selected_funding) or any(not math.isfinite(row.rate) for row in funding_points)
            or observed != expected):
        raise IncompleteEpisode("funding authority is incomplete across exposure")


def fixed_exit_open(bars: Sequence[Bar], actual_entry_ms: int, *, marks: Sequence[MarkClose],
                    funding_points: Sequence[FundingPoint], symbol: str) -> Bar:
    target = actual_entry_ms + 6 * HOUR_MS
    validate_exposure_authority(symbol_bars={symbol: bars}, symbol_marks={symbol: marks},
                                funding_points=funding_points, entry_ms=actual_entry_ms, exit_ms=target)
    indexed = exact_index(bars)
    if target not in indexed:
        raise IncompleteEpisode("missing exact six-hour fixed-exit open")
    return indexed[target]


DRAW_PHASE_ORDER = {
    "FUNDING_BEFORE": 0, "MARK_BEFORE_EXIT": 1, "EXIT": 2,
    "CONSERVATIVE_ADJUSTMENT": 3, "MARK_AFTER_EXIT": 4, "ENTRY": 5,
    "FUNDING_AT_OPEN": 6, "MARK_END": 7,
}


@dataclass(frozen=True)
class DrawdownEvent:
    timestamp_ms: int
    phase: str
    sequence: int
    cashflow_R: float
    marked_open_R: float


@dataclass(frozen=True)
class PortfolioMark:
    timestamp_ms: int
    symbol: str
    price: float


def derive_drawdown_events(attempts: Sequence[ClosedAttempt], marks: Sequence[PortfolioMark]) -> tuple[DrawdownEvent, ...]:
    """Derive ordered R cashflows and open-position marks from accepted lifecycles."""
    if not attempts:
        raise IncompleteEpisode("mark-only/no-entry drawdown stream is forbidden")
    mark_index: dict[tuple[int, str], float] = {}
    for mark in marks:
        key = (mark.timestamp_ms, mark.symbol)
        if key in mark_index or not math.isfinite(mark.price) or mark.price <= 0:
            raise IncompleteEpisode("duplicate/non-finite causal portfolio mark")
        mark_index[key] = mark.price
    actions: list[tuple[int, int, str, ClosedAttempt, object]] = []
    required_marks: set[tuple[int, str]] = set()
    for attempt in attempts:
        if not attempt.fills or not attempt.legs:
            raise IncompleteEpisode("accepted attempt lacks entry/close-derived legs")
        for leg in attempt.legs:
            for timestamp in range(attempt.entry_ms, attempt.close_ms + HOUR_MS, HOUR_MS):
                required_marks.add((timestamp, leg.symbol))
        for fill in attempt.fills:
            phase = "ENTRY" if fill.phase == "entry" else "EXIT"
            actions.append((fill.timestamp_ms, DRAW_PHASE_ORDER[phase], phase, attempt, fill))
        for funding in attempt.funding_events:
            phase = "FUNDING_AT_OPEN" if funding.timestamp_ms == attempt.entry_ms else "FUNDING_BEFORE"
            actions.append((funding.timestamp_ms, DRAW_PHASE_ORDER[phase], phase, attempt, funding))
        if attempt.conservative_adjustment_cash:
            actions.append((attempt.close_ms, DRAW_PHASE_ORDER["CONSERVATIVE_ADJUSTMENT"],
                            "CONSERVATIVE_ADJUSTMENT", attempt, attempt.conservative_adjustment_cash))
    if not required_marks <= set(mark_index):
        raise IncompleteEpisode("missing causal marks across accepted exposure")
    positions: dict[str, tuple[int, float, float, float]] = {}
    events: list[DrawdownEvent] = []
    sequence_by_key: dict[tuple[int, str], int] = {}

    def marked_R(timestamp: int) -> float:
        return math.fsum(side * quantity * (mark_index[(timestamp, symbol)] - entry_reference) / risk
                         for symbol, (side, quantity, entry_reference, risk) in positions.items())

    for timestamp, _, phase, attempt, payload in sorted(
            actions, key=lambda row: (row[0], row[1], row[3].attempt_id,
                                      getattr(row[4], "symbol", ""))):
        cashflow_R = 0.0
        if isinstance(payload, FillCash):
            cashflow_R = (payload.movement_cash - payload.fee_drag - payload.slippage_drag) / attempt.initial_risk_cash
            if payload.phase == "entry":
                if payload.symbol in positions:
                    raise IncompleteEpisode("overlapping symbol position in drawdown derivation")
                position_side = 1 if payload.symbol == attempt.long_symbol else -1
                positions[payload.symbol] = (position_side, payload.quantity,
                                             payload.reference_price, attempt.initial_risk_cash)
            else:
                if payload.symbol not in positions:
                    raise IncompleteEpisode("close without derived open position")
                del positions[payload.symbol]
        elif isinstance(payload, FundingCashEvent):
            if payload.symbol not in positions:
                raise IncompleteEpisode("funding without derived open position")
            cashflow_R = payload.cash / attempt.initial_risk_cash
        else:
            cashflow_R = float(payload) / attempt.initial_risk_cash
        key = (timestamp, phase)
        sequence = sequence_by_key.get(key, 0)
        sequence_by_key[key] = sequence + 1
        events.append(DrawdownEvent(timestamp, phase, sequence, cashflow_R, marked_R(timestamp)))
    # Add mark-only hourly observations so intra-exposure drawdown cannot disappear.
    action_times = {event.timestamp_ms for event in events}
    for timestamp in sorted({timestamp for timestamp, _ in required_marks} - action_times):
        # Replaying positions for arbitrary intermediate hours is intentionally avoided:
        # derive them from the immutable attempt intervals and legs.
        open_legs = [(attempt, leg) for attempt in attempts for leg in attempt.legs
                     if attempt.entry_ms <= timestamp < attempt.close_ms]
        value = math.fsum(leg.position_side * leg.quantity
                          * (mark_index[(timestamp, leg.symbol)]
                             - next(fill.reference_price for fill in attempt.fills
                                    if fill.symbol == leg.symbol and fill.phase == "entry"))
                          / attempt.initial_risk_cash for attempt, leg in open_legs)
        events.append(DrawdownEvent(timestamp, "MARK_END", 0, 0.0, value))
    if positions:
        raise IncompleteEpisode("drawdown lifecycle did not finish flat")
    return tuple(sorted(events, key=lambda event: (
        event.timestamp_ms, DRAW_PHASE_ORDER[event.phase], event.sequence)))


def chronological_drawdown(events: Sequence[DrawdownEvent], initial_equity_R: float = 0.0) -> tuple[float, int]:
    """Marked+realized R drawdown with exact timestamp/phase/sequence ordering."""
    if not math.isfinite(initial_equity_R):
        raise C258Error("invalid initial equity R")
    if not events:
        raise IncompleteEpisode("drawdown series is empty")
    keys: set[tuple[int, int, int]] = set()
    for event in events:
        if event.phase not in DRAW_PHASE_ORDER:
            raise IncompleteEpisode("unknown drawdown phase")
        if not all(math.isfinite(value) for value in (event.cashflow_R, event.marked_open_R)):
            raise IncompleteEpisode("missing/non-finite drawdown cashflow or mark")
        key = (event.timestamp_ms, DRAW_PHASE_ORDER[event.phase], event.sequence)
        if key in keys:
            raise IncompleteEpisode("duplicate drawdown event identity")
        keys.add(key)
    ordered = sorted(events, key=lambda event: (
        event.timestamp_ms, DRAW_PHASE_ORDER[event.phase], event.sequence))
    cash = high = initial_equity_R
    max_dd, high_ts, recovery = 0.0, ordered[0].timestamp_ms, 0
    for event in ordered:
        cash += event.cashflow_R
        equity = cash + event.marked_open_R
        if equity > high:
            high, high_ts = equity, event.timestamp_ms
        else:
            dd = high - equity
            if dd > max_dd:
                max_dd, recovery = dd, event.timestamp_ms - high_ts
    return max_dd, recovery


def profit_factor(values: Sequence[float]) -> float:
    wins = math.fsum(v for v in values if v > 0)
    losses = -math.fsum(v for v in values if v < 0)
    if losses == 0:
        return math.nan
    return wins / losses


def stationary_bootstrap(values: Sequence[float], *, centered: bool, seed: int,
                         resamples: int = 10_000) -> np.ndarray:
    ordered = np.asarray(values, dtype=np.float64)
    n = ordered.size
    if n == 0 or np.any(~np.isfinite(ordered)):
        raise C258Error("bootstrap requires finite non-empty observations")
    observed = math.fsum(map(float, ordered)) / n
    source = ordered - observed if centered else ordered
    draws = np.random.Generator(np.random.PCG64(seed)).random(size=(resamples, n, 2), dtype=np.float64)
    output = np.empty(resamples, dtype=np.float64)
    for b in range(resamples):
        index = int(math.floor(draws[b, 0, 1] * n))
        sample = [float(source[index])]
        for j in range(1, n):
            restart, u_index = draws[b, j]
            index = int(math.floor(u_index * n)) if restart < 1 / 21 else (index + 1) % n
            sample.append(float(source[index]))
        output[b] = math.fsum(sample) / n
    return output


def bootstrap_statistics(values: Sequence[float], *, resamples: int = 10_000) -> dict[str, float]:
    n = len(values)
    if n == 0:
        raise C258Error("undefined bootstrap")
    observed = math.fsum(values) / n
    null = stationary_bootstrap(values, centered=True, seed=25820261107, resamples=resamples)
    ci = stationary_bootstrap(values, centered=False, seed=25820261108, resamples=resamples)
    return {
        "mean": observed,
        "p_value": (1 + int(np.count_nonzero(null >= observed))) / (resamples + 1),
        "ci_low": float(np.quantile(ci, 0.025, method="linear")),
        "ci_high": float(np.quantile(ci, 0.975, method="linear")),
    }


@dataclass(frozen=True)
class ReplayResult:
    capital: int
    attempts: tuple[ClosedAttempt, ...]
    skips: tuple[str, ...] = ()
    incomplete: bool = False


@dataclass
class BookState:
    """Replay-local admission state; no state is shared between capital simulations."""
    equity: Decimal
    open_gross: Decimal = Decimal(0)
    open_stressed_stop_loss: Decimal = Decimal(0)
    open_pairs: int = 0
    occupied_symbols: set[str] = field(default_factory=set)

    def can_admit(self, long_symbol: str, short_symbol: str, sized: SizedPair) -> bool:
        if self.equity <= 0 or long_symbol == short_symbol:
            return False
        if long_symbol in self.occupied_symbols or short_symbol in self.occupied_symbols:
            return False
        return (self.open_pairs < 3
                and sized.gross <= Decimal("0.5") * self.equity
                and self.open_gross + sized.gross <= Decimal("1.5") * self.equity
                and self.open_stressed_stop_loss + sized.initial_pair_risk_cash <= Decimal("0.03") * self.equity)

    def admit(self, long_symbol: str, short_symbol: str, sized: SizedPair) -> None:
        if not self.can_admit(long_symbol, short_symbol, sized):
            raise C258Error("book caps or duplicate rule refuse pair")
        self.open_gross += sized.gross
        self.open_stressed_stop_loss += sized.initial_pair_risk_cash
        self.open_pairs += 1
        self.occupied_symbols.update((long_symbol, short_symbol))

    def release(self, long_symbol: str, short_symbol: str, sized: SizedPair) -> None:
        if long_symbol not in self.occupied_symbols or short_symbol not in self.occupied_symbols:
            raise C258Error("cannot release an absent pair")
        self.open_gross -= sized.gross
        self.open_stressed_stop_loss -= sized.initial_pair_risk_cash
        self.open_pairs -= 1
        self.occupied_symbols.remove(long_symbol); self.occupied_symbols.remove(short_symbol)


def replay_capitals(factory: Callable[[int], ReplayResult]) -> dict[int, ReplayResult]:
    """Execute four isolated books; diagnostics can never mutate primary."""
    results = {capital: factory(capital) for capital in CAPITALS}
    for capital, result in results.items():
        if result.capital != capital:
            raise C258Error("capital replay identity mismatch")
    return results


def primary_result(results: Mapping[int, ReplayResult]) -> ReplayResult:
    if set(results) != set(CAPITALS):
        raise C258Error("all isolated capital replays are required")
    return results[PRIMARY_CAPITAL]


def fold_metrics(attempts: Sequence[ClosedAttempt]) -> dict[str, object]:
    ordered = sorted(attempts, key=lambda row: (row.entry_ms, row.long_symbol, row.short_symbol))
    values = [row.verdict_R for row in ordered]
    if not values or any(not math.isfinite(value) for value in values):
        raise C258Error("undefined primary statistics")
    symbol_net: dict[str, float] = {}
    regime: dict[str, list[float]] = {}
    for row in ordered:
        if not row.legs or not math.isclose(math.fsum(leg.net_R for leg in row.legs),
                                            row.verdict_R, rel_tol=0, abs_tol=1e-12):
            raise IncompleteEpisode("symbol contributions are not exact derived leg R")
        for leg in row.legs:
            symbol_net[leg.symbol] = symbol_net.get(leg.symbol, 0.0) + leg.net_R
        regime.setdefault(row.regime, []).append(row.verdict_R)
    denominator = math.fsum(abs(v) for v in symbol_net.values())
    concentration = math.inf if denominator == 0 else max(abs(v) for v in symbol_net.values()) / denominator
    return {
        "closed_attempts": len(ordered),
        "net_R": math.fsum(values),
        "mean_R": math.fsum(values) / len(values),
        "profit_factor": profit_factor(values),
        "win_rate": sum(value > 0 for value in values) / len(values),
        "atomic_failures": sum(row.atomic_one_leg_loss for row in ordered),
        "symbols": len(symbol_net),
        "max_symbol_concentration": concentration,
        "regime": {key: {"count": len(v), "mean_R": math.fsum(v) / len(v)} for key, v in regime.items()},
    }


@dataclass(frozen=True)
class FoldEvidence:
    """Complete synthetic/loaded fold evidence consumed by the frozen gates."""
    name: str
    base: ReplayResult
    stress: ReplayResult
    weekly_closed_counts_including_zero: tuple[int, ...]
    monthly_net_R_including_zero: tuple[float, ...]
    base_marks: tuple[PortfolioMark, ...]
    stress_marks: tuple[PortfolioMark, ...]
    scheduled_decisions: int
    eligible_decisions: int
    admitted_attempts: int
    zero_trade_decisions: int
    indexed_skips: int
    complete_required_reports: bool = True
    causal_exchange_impact_atomicity_pass: bool = True


@dataclass(frozen=True)
class TrialEvent:
    sequence: int
    fold: str
    kind: str
    attempt_id: str | None
    detail: str


@dataclass
class TrialLedger:
    """Append-only ledger for decisions, skips, rows, stress rows, and rejection."""
    events: list[TrialEvent] = field(default_factory=list)

    def append(self, *, fold: str, kind: str, detail: str, attempt_id: str | None = None) -> TrialEvent:
        allowed = {"SCHEDULED", "ZERO_TRADE", "INDEXED_SKIP", "BASE_ROW", "STRESS_ROW", "REJECTION"}
        if kind not in allowed or not fold or not detail:
            raise C258Error("invalid trial-ledger event")
        event = TrialEvent(len(self.events), fold, kind, attempt_id, detail)
        self.events.append(event)
        return event

    def digest(self) -> str:
        return _sha256_bytes(_canonical_bytes([asdict(event) for event in self.events]))


def validate_fold_evidence(fold: FoldEvidence) -> None:
    if fold.base.capital != PRIMARY_CAPITAL or fold.stress.capital != PRIMARY_CAPITAL:
        raise C258Error("gates may consume only the 2000 USD primary replay")
    if not fold.weekly_closed_counts_including_zero or not fold.monthly_net_R_including_zero:
        raise C258Error("zero-inclusive week/month grids are required")
    if any(value < 0 for value in fold.weekly_closed_counts_including_zero):
        raise C258Error("weekly counts cannot be negative")
    if any(not math.isfinite(value) for value in fold.monthly_net_R_including_zero):
        raise C258Error("monthly R must be finite")
    counts = (fold.scheduled_decisions, fold.eligible_decisions, fold.admitted_attempts,
              fold.zero_trade_decisions, fold.indexed_skips)
    if any(not isinstance(value, int) or value < 0 for value in counts):
        raise C258Error("decision counts must be nonnegative integers")
    if fold.admitted_attempts != len(fold.base.attempts):
        raise C258Error("admitted/base attempt count mismatch")
    if fold.base.incomplete or fold.stress.incomplete:
        raise IncompleteEpisode(f"{fold.name} is incomplete")
    if any(not row.lifecycle_identity for row in (*fold.base.attempts, *fold.stress.attempts)):
        raise IncompleteEpisode("every admitted row requires a complete fill lifecycle identity")
    for row in (*fold.base.attempts, *fold.stress.attempts):
        movement = math.fsum(fill.movement_cash for fill in row.fills)
        fee = math.fsum(fill.fee_drag for fill in row.fills)
        slippage = math.fsum(fill.slippage_drag for fill in row.fills)
        funding = math.fsum(event.cash for event in row.funding_events)
        actual = movement + funding - fee - slippage
        verdict = min(actual, -(row.fee_drag + row.slippage_drag)) if row.atomic_one_leg_loss else actual
        if (row.derivation_identity_sha256 != attempt_derivation_digest(row)
                or not math.isclose(movement, row.movement_cash, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(fee, row.fee_drag, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(slippage, row.slippage_drag, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(funding, row.funding_cash, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(actual, row.actual_net_cash, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(verdict, row.verdict_net_cash, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(row.verdict_R, verdict / row.initial_risk_cash,
                                    rel_tol=0, abs_tol=1e-12)
                or _fill_lifecycle_identity(row.fills) != row.lifecycle_identity
                or not math.isclose(math.fsum(leg.net_R for leg in row.legs), row.verdict_R,
                                    rel_tol=0, abs_tol=1e-12)
                or not math.isclose(math.fsum(leg.net_R for leg in row.legs
                                              if leg.symbol == row.long_symbol),
                                    row.long_contribution_R, rel_tol=0, abs_tol=1e-12)
                or not math.isclose(math.fsum(leg.net_R for leg in row.legs
                                              if leg.symbol == row.short_symbol),
                                    row.short_contribution_R, rel_tol=0, abs_tol=1e-12)):
            raise IncompleteEpisode("attempt stress/accounting derivation identity mismatch")
    base_ids = [(row.attempt_id, row.entry_ms, row.close_ms, row.long_symbol, row.short_symbol,
                 row.regime, row.atomic_one_leg_loss, row.initial_risk_cash, row.lifecycle_identity,
                 tuple((event.timestamp_ms, event.symbol) for event in row.funding_events))
                for row in fold.base.attempts]
    stress_ids = [(row.attempt_id, row.entry_ms, row.close_ms, row.long_symbol, row.short_symbol,
                   row.regime, row.atomic_one_leg_loss, row.initial_risk_cash, row.lifecycle_identity,
                   tuple((event.timestamp_ms, event.symbol) for event in row.funding_events))
                  for row in fold.stress.attempts]
    if base_ids != stress_ids:
        raise C258Error("stress replay changed selection/order")
    for base_row, stress_row in zip(fold.base.attempts, fold.stress.attempts):
        for fill in base_row.fills:
            _rederive_fill(fill, stress=False)
        if stress_row != mechanical_stress_attempt(base_row):
            raise IncompleteEpisode("stress fills/funding/costs are not mechanically derived")


def _median(values: Sequence[int]) -> float:
    if not values:
        raise C258Error("undefined zero-inclusive median")
    return float(np.median(np.asarray(values, dtype=np.float64)))


def _max_closed_r_drawdown(attempts: Sequence[ClosedAttempt]) -> float:
    equity = peak = 0.0
    maximum = 0.0
    for row in sorted(attempts, key=lambda item: (
            item.close_ms, item.entry_ms, item.long_symbol, item.short_symbol)):
        equity += row.verdict_R
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def fold_drawdown(fold: FoldEvidence, *, stress: bool = False) -> dict[str, float | int]:
    attempts = fold.stress.attempts if stress else fold.base.attempts
    marks = fold.stress_marks if stress else fold.base_marks
    events = derive_drawdown_events(attempts, marks)
    marked, recovery = chronological_drawdown(events)
    closed = _max_closed_r_drawdown(attempts)
    return {"marked_cashflow_R": marked, "closed_R": closed,
            "worse_of_R": max(marked, closed), "recovery_ms": recovery}


def drawdown_tier(*, net_R: float, max_drawdown_R: float,
                  stress_net_R: float, stress_max_drawdown_R: float) -> dict[str, object]:
    if any(not math.isfinite(v) for v in (net_R, max_drawdown_R, stress_net_R, stress_max_drawdown_R)):
        raise C258Error("undefined drawdown tier")
    if max_drawdown_R > 60:
        return {"tier": "reject", "pass": False, "paper_pair_risk_multiplier": 0.0}
    ratio = math.inf if max_drawdown_R == 0 and net_R > 0 else (
        net_R / max_drawdown_R if max_drawdown_R > 0 else math.nan)
    stress_ratio = math.inf if stress_max_drawdown_R == 0 and stress_net_R > 0 else (
        stress_net_R / stress_max_drawdown_R if stress_max_drawdown_R > 0 else math.nan)
    if max_drawdown_R <= 15:
        return {"tier": "standard", "pass": True, "paper_pair_risk_multiplier": 1.0}
    if max_drawdown_R <= 30:
        passed = math.isfinite(ratio) and ratio >= 1.5
        return {"tier": "elevated", "pass": passed, "paper_pair_risk_multiplier": 1.0 if passed else 0.0}
    passed = (math.isfinite(ratio) and ratio >= 2.0
              and math.isfinite(stress_ratio) and stress_ratio >= 1.0)
    multiplier = min(0.5, 15 / max_drawdown_R) if passed else 0.0
    return {"tier": "high", "pass": passed, "paper_pair_risk_multiplier": multiplier}


def development_gate(fold: FoldEvidence) -> dict[str, object]:
    validate_fold_evidence(fold)
    base, stress = fold_metrics(fold.base.attempts), fold_metrics(fold.stress.attempts)
    gross = math.fsum(row.gross_R for row in fold.base.attempts)
    reasons = []
    checks = {
        "pre_cost_gross_expectancy_positive": gross / len(fold.base.attempts) > 0,
        "mean_after_cost_R_positive": float(base["mean_R"]) > 0,
        "profit_factor_gt_1_05": float(base["profit_factor"]) > 1.05,
        "closed_attempts_gte_300": int(base["closed_attempts"]) >= 300,
        "symbols_gte_12": int(base["symbols"]) >= 12,
        "median_weekly_closed_gte_25": _median(fold.weekly_closed_counts_including_zero) >= 25,
        "stress_mean_R_positive": float(stress["mean_R"]) > 0,
        "closed_R_drawdown_lte_60": float(fold_drawdown(fold)["worse_of_R"]) <= 60,
        "complete": fold.complete_required_reports and fold.causal_exchange_impact_atomicity_pass,
    }
    reasons.extend(name for name, passed in checks.items() if not passed)
    return {"pass": not reasons, "checks": checks, "reasons": reasons, "base": base, "stress": stress}


def forward_gate(folds: Sequence[FoldEvidence], *, bootstrap_resamples: int = 10_000) -> dict[str, object]:
    if len(folds) != 3:
        raise C258Error("exactly three frozen forward folds are required")
    for fold in folds:
        validate_fold_evidence(fold)
    attempts = tuple(row for fold in folds for row in fold.base.attempts)
    stress_attempts = tuple(row for fold in folds for row in fold.stress.attempts)
    base, stress = fold_metrics(attempts), fold_metrics(stress_attempts)
    fold_base = [fold_metrics(fold.base.attempts) for fold in folds]
    weeks = tuple(value for fold in folds for value in fold.weekly_closed_counts_including_zero)
    months = tuple(value for fold in folds for value in fold.monthly_net_R_including_zero)
    long_net = math.fsum(row.long_contribution_R for row in attempts)
    short_net = math.fsum(row.short_contribution_R for row in attempts)
    regime_rows: dict[str, list[float]] = {"BTC_ABOVE_OR_ON_EMA200": [], "BTC_BELOW_EMA200": []}
    for row in attempts:
        if row.regime not in regime_rows:
            raise C258Error("unfrozen regime label")
        regime_rows[row.regime].append(row.verdict_R)
    regime_pass = all(len(values) >= 100 and math.fsum(values) / len(values) > -0.03
                      for values in regime_rows.values())
    bootstrap = bootstrap_statistics([row.verdict_R for row in sorted(
        attempts, key=lambda item: (item.entry_ms, item.long_symbol, item.short_symbol))],
        resamples=bootstrap_resamples)
    net_R = float(base["net_R"]); stress_net_R = float(stress["net_R"])
    max_dd = max(float(fold_drawdown(fold)["worse_of_R"]) for fold in folds)
    stress_max_dd = max(float(fold_drawdown(fold, stress=True)["worse_of_R"]) for fold in folds)
    tier = drawdown_tier(net_R=net_R, max_drawdown_R=max_dd,
                         stress_net_R=stress_net_R, stress_max_drawdown_R=stress_max_dd)
    checks = {
        "aggregate_mean_R_gt_0_03": float(base["mean_R"]) > 0.03,
        "aggregate_pf_gt_1_10": float(base["profit_factor"]) > 1.10,
        "attempts_gte_1000_and_symbols_gte_16": int(base["closed_attempts"]) >= 1000 and int(base["symbols"]) >= 16,
        "median_weekly_closed_gte_30": _median(weeks) >= 30,
        "two_of_three_folds_positive": sum(float(item["net_R"]) > 0 for item in fold_base) >= 2,
        "worst_fold_mean_gt_minus_0_05": min(float(item["mean_R"]) for item in fold_base) > -0.05,
        "both_leg_contributions_nonnegative": long_net >= 0 and short_net >= 0,
        "strictly_more_than_55pct_months_positive": sum(value > 0 for value in months) / len(months) > 0.55,
        "both_regimes": regime_pass,
        "stress_mean_and_pf": float(stress["mean_R"]) > 0 and float(stress["profit_factor"]) > 1.02,
        "bootstrap_bonferroni": float(bootstrap["p_value"]) <= 0.05 / 258,
        "symbol_concentration": float(base["max_symbol_concentration"]) <= 0.25,
        "drawdown_tier": bool(tier["pass"]),
        "complete": all(fold.complete_required_reports and fold.causal_exchange_impact_atomicity_pass for fold in folds),
    }
    reasons = [name for name, passed in checks.items() if not passed]
    return {"pass": not reasons, "checks": checks, "reasons": reasons, "base": base,
            "stress": stress, "bootstrap": bootstrap, "drawdown": tier,
            "long_net_R": long_net, "short_net_R": short_net}


def evaluate_stage_a(development: FoldEvidence, forward_folds: Sequence[FoldEvidence], *,
                     bootstrap_resamples: int = 10_000) -> dict[str, object]:
    """Apply development veto first, then the one-shot three-fold forward gate."""
    development_result = development_gate(development)
    if not development_result["pass"]:
        return {"candidate_id": 258, "verdict": "REJECT_DEVELOPMENT", "default_off": True,
                "development": development_result, "forward_opened": False,
                "promotable_primary_count": 1, "familywise_alpha": 0.05 / 258}
    forward_result = forward_gate(forward_folds, bootstrap_resamples=bootstrap_resamples)
    verdict = "FORWARD_PASS_PAPER_BLOCKED" if forward_result["pass"] else "REJECT_FORWARD"
    return {"candidate_id": 258, "verdict": verdict, "default_off": True,
            "paper_blocked_until_independent_oos": True, "development": development_result,
            "forward": forward_result, "forward_opened": True,
            "promotable_primary_count": 1, "familywise_alpha": 0.05 / 258}


def immutable_output_identity(*, pins: AuthorityPins, source_digest: str, result: Mapping[str, object]) -> dict[str, object]:
    """Bind an output to contract, authorities, input identity, and exact result bytes."""
    if len(source_digest) != 64 or any(ch not in "0123456789abcdef" for ch in source_digest):
        raise C258Error("invalid source digest")
    body = {
        "format": "zb-c258-stagea-result/1",
        "candidate_id": 258,
        "default_off": True,
        "paper_testnet_only": True,
        "sealed_holdout_opened": False,
        "contract_sha256": CONTRACT_SHA256,
        "authority_plan_sha256": PLAN_SHA256,
        "authority_pins": asdict(pins),
        "source_digest": source_digest,
        "result": result,
    }
    return {**body, "output_identity_sha256": _sha256_bytes(_canonical_bytes(body))}
