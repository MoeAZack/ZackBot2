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
from dataclasses import asdict, dataclass, field
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
    protected_ref: str
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


def validate_authority_pins(pins: AuthorityPins, *, root: Path = ROOT,
                            required_ancestor: str = C258_FROZEN_HEAD) -> dict[str, dict]:
    """Validate all v6 identities before any market-row loader may be called."""
    load_frozen_contract()
    if len(pins.protected_head) != 40 or any(ch not in "0123456789abcdef" for ch in pins.protected_head):
        raise IncompleteAuthority("accepted protected head is not exactly pinned")
    if not pins.protected_ref.startswith("refs/"):
        raise IncompleteAuthority("protected ref must be fully qualified")
    resolved_head = _git(root, "rev-parse", "--verify", f"{pins.protected_head}^{{commit}}")
    resolved_ref = _git(root, "rev-parse", "--verify", f"{pins.protected_ref}^{{commit}}")
    if resolved_head != pins.protected_head or resolved_ref != pins.protected_head:
        raise IncompleteAuthority("protected head/ref exact equality failed")
    _git(root, "merge-base", "--is-ancestor", required_ancestor, pins.protected_head)
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
    gaps = coverage.get("guaranteed_period_gaps", coverage.get("required_guaranteed_period_gaps"))
    status = coverage.get("result", coverage.get("status"))
    if gaps != 0 or status not in {"ZERO_GUARANTEED_PERIOD_GAPS", "PASS"}:
        raise IncompleteAuthority("coverage-v4 is not ZERO_GUARANTEED_PERIOD_GAPS")
    cutoff = manifest.get("cutoff_exclusive_utc", manifest.get("cutoff", {}).get("exclusive_utc")
                          if isinstance(manifest.get("cutoff"), Mapping) else None)
    if cutoff != "2026-11-05T00:00:00Z":
        raise IncompleteAuthority("manifest-v6 cutoff is not exact and exclusive")
    for key, expected in (("manifest_v6_byte_sha256", pins.manifest_v6.byte_sha256),
                          ("manifest_v6_content_digest", pins.manifest_v6.content_digest)):
        _require_binding(pit, key, expected)
    for key, expected in (("pit_v9_byte_sha256", pins.pit_v9.byte_sha256),
                          ("pit_v9_content_digest", pins.pit_v9.content_digest)):
        _require_binding(filters, key, expected)
    for name in ("manifest_v6", "pit_v9", "filter_v4"):
        pin = getattr(pins, name)
        _require_binding(coverage, f"{name}_byte_sha256", pin.byte_sha256)
        _require_binding(coverage, f"{name}_content_digest", pin.content_digest)
    return decoded


def run_with_authority(pins: AuthorityPins, loader: Callable[[dict[str, dict]], object], *,
                       root: Path = ROOT, required_ancestor: str = C258_FROZEN_HEAD) -> object:
    """The only real-data boundary: pins and schemas pass before loader invocation."""
    authorities = validate_authority_pins(pins, root=root, required_ancestor=required_ancestor)
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


def make_fill(*, timestamp_ms: int, symbol: str, side: int, quantity: float,
              reference_price: float, phase: str, movement_from: float | None,
              slippage_bps: float = BASE_SLIPPAGE_BPS, impact_multiplier: float = 1.0) -> FillCash:
    if side not in {-1, 1} or phase not in {"entry", "exit", "stop", "cleanup"}:
        raise C258Error("invalid fill side/phase")
    if not all(math.isfinite(v) and v > 0 for v in (quantity, reference_price, impact_multiplier)):
        raise C258Error("invalid fill quantity/reference/impact")
    effective_slip = slippage_bps * max(1.0, impact_multiplier)
    # side is the transaction direction: +1 buy, -1 sell.
    execution = reference_price * (1 + side * effective_slip / 10_000)
    movement = 0.0 if movement_from is None else (-side) * quantity * (reference_price - movement_from)
    fee = abs(quantity * execution) * FEE_BPS / 10_000
    slip = abs(quantity) * abs(execution - reference_price)
    if not all(math.isfinite(v) for v in (execution, movement, fee, slip)) or min(fee, slip) < 0:
        raise C258Error("non-finite or signed drag")
    return FillCash(timestamp_ms, symbol, side, quantity, reference_price, execution,
                    movement, fee, slip, phase)


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
    return max(eligible, key=lambda mark: mark.close_ms)


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


def _fill_lifecycle_identity(fills: Sequence[FillCash]) -> tuple[tuple[object, ...], ...]:
    return tuple((fill.timestamp_ms, fill.symbol, fill.side, repr(fill.quantity),
                  repr(fill.reference_price), fill.phase) for fill in fills)


def _validate_fill_lifecycle(*, fills: Sequence[FillCash], entry_ms: int, close_ms: int,
                             expected: Mapping[str, tuple[int, Decimal]], atomic_one_leg: bool) -> None:
    by_symbol: dict[str, list[FillCash]] = {}
    for fill in fills:
        by_symbol.setdefault(fill.symbol, []).append(fill)
    if set(by_symbol) != set(expected):
        raise IncompleteEpisode("fill symbols do not match the accepted lifecycle")
    if len(fills) != 2 * len(expected):
        raise IncompleteEpisode("accepted lifecycle is not fully flat")
    for symbol, (position_side, accepted_quantity) in expected.items():
        symbol_fills = by_symbol[symbol]
        entries = [fill for fill in symbol_fills if fill.phase == "entry"]
        closes = [fill for fill in symbol_fills if fill.phase in {"exit", "stop", "cleanup"}]
        if len(entries) != 1 or len(closes) != 1:
            raise IncompleteEpisode("each accepted leg requires one entry and one complete close")
        entry, final = entries[0], closes[0]
        if entry.side != position_side or final.side != -position_side:
            raise IncompleteEpisode("fill side does not match the accepted leg")
        if (Decimal(str(entry.quantity)) != accepted_quantity
                or Decimal(str(final.quantity)) != accepted_quantity):
            raise IncompleteEpisode("fill quantity does not conserve the accepted quantity")
        if entry.timestamp_ms != entry_ms:
            raise IncompleteEpisode("entry timestamp does not match the accepted lifecycle")
        if final.timestamp_ms <= entry.timestamp_ms:
            raise IncompleteEpisode("close must occur after the entry")
    if max(fill.timestamp_ms for fill in fills if fill.phase != "entry") != close_ms:
        raise IncompleteEpisode("attempt close time is not the final lifecycle fill")
    if atomic_one_leg and len(expected) != 1:
        raise IncompleteEpisode("atomic failure must contain exactly one accepted leg")
    if not atomic_one_leg and len(expected) != 2:
        raise IncompleteEpisode("normal pair must contain both accepted legs")


def close_attempt(*, attempt_id: str, entry_ms: int, close_ms: int, long_symbol: str,
                  short_symbol: str, regime: str, fills: Sequence[FillCash], funding_cash: float,
                  initial_risk_cash: float, accepted_quantities: Mapping[str, tuple[int, Decimal]],
                  atomic_one_leg: bool = False) -> ClosedAttempt:
    if not fills or close_ms < entry_ms or not math.isfinite(funding_cash):
        raise IncompleteEpisode("attempt cashflows are incomplete")
    _validate_fill_lifecycle(fills=fills, entry_ms=entry_ms, close_ms=close_ms,
                             expected=accepted_quantities, atomic_one_leg=atomic_one_leg)
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
    return ClosedAttempt(attempt_id, entry_ms, close_ms, long_symbol, short_symbol, regime,
                         movement, funding_cash, fee, slippage, actual, verdict,
                         initial_risk_cash, verdict / initial_risk_cash, atomic_one_leg, adjustment,
                         movement / initial_risk_cash, 0.0, 0.0, _fill_lifecycle_identity(fills))


def account_atomic_one_leg(*, attempt_id: str, expected_long_symbol: str, expected_short_symbol: str,
                           accepted_leg: LegSpec, quantity: Decimal, fills: Sequence[FillCash],
                           funding_cash: float, regime: str) -> ClosedAttempt:
    accepted_leg.validate()
    if quantity <= 0 or len(fills) != 2:
        raise IncompleteEpisode("atomic accepted quantity/lifecycle is incomplete")
    risk = float(quantity * stressed_loss_per_unit(accepted_leg))
    entry = next((fill for fill in fills if fill.phase == "entry"), None)
    final = next((fill for fill in fills if fill.phase in {"stop", "cleanup"}), None)
    if entry is None or final is None or entry.symbol != accepted_leg.symbol or final.symbol != accepted_leg.symbol:
        raise IncompleteEpisode("atomic fills do not match the accepted leg")
    if final.phase == "stop":
        if accepted_leg.side == 1 and final.reference_price > float(accepted_leg.stop_reference):
            raise IncompleteEpisode("long atomic stop was filled favorably above its frozen reference")
        if accepted_leg.side == -1 and final.reference_price < float(accepted_leg.stop_reference):
            raise IncompleteEpisode("short atomic stop was filled favorably below its frozen reference")
    return close_attempt(attempt_id=attempt_id, entry_ms=entry.timestamp_ms, close_ms=final.timestamp_ms,
                         long_symbol=expected_long_symbol, short_symbol=expected_short_symbol,
                         regime=regime, fills=fills, funding_cash=funding_cash,
                         initial_risk_cash=risk,
                         accepted_quantities={accepted_leg.symbol: (accepted_leg.side, quantity)},
                         atomic_one_leg=True)


def fixed_exit_open(bars: Sequence[Bar], actual_entry_ms: int) -> Bar:
    candidates = sorted((bar for bar in bars if bar.open_ms >= actual_entry_ms + 6 * HOUR_MS),
                        key=lambda bar: bar.open_ms)
    if not candidates:
        raise IncompleteEpisode("missing six-hour fixed-exit open")
    candidates[0].validate()
    return candidates[0]


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
        symbol_net[row.long_symbol] = symbol_net.get(row.long_symbol, 0.0) + row.verdict_R / 2
        symbol_net[row.short_symbol] = symbol_net.get(row.short_symbol, 0.0) + row.verdict_R / 2
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
    base_drawdown_events: tuple[DrawdownEvent, ...]
    stress_drawdown_events: tuple[DrawdownEvent, ...]
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
    base_ids = [(row.attempt_id, row.entry_ms, row.close_ms, row.long_symbol, row.short_symbol,
                 row.regime, row.lifecycle_identity) for row in fold.base.attempts]
    stress_ids = [(row.attempt_id, row.entry_ms, row.close_ms, row.long_symbol, row.short_symbol,
                   row.regime, row.lifecycle_identity) for row in fold.stress.attempts]
    if base_ids != stress_ids:
        raise C258Error("stress replay changed selection/order")


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
    events = fold.stress_drawdown_events if stress else fold.base_drawdown_events
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
