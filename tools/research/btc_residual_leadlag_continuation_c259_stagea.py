"""C259 causal Stage-A evaluator primitives.

Importing this module never opens market, outcome, packet, identity, or holdout
data.  The only loader boundary validates a protected Git-tree authority-v6
pin set first.  The checked-in contract has no final pins, so the CLI remains
locked while pure functions support hostile synthetic tests.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import statistics
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "research_evidence/prereg/btc_residual_leadlag_continuation_c259_stagea.v1.json"
CONTRACT_HEAD = "82d02b82b774e92ffa374e6ea1be8ba5c0cfb98c"
CONTRACT_SHA256 = "1aad55f092c0c308cb901701e2ee8bd3062d1eee9a07e13729673efbf5b17719"
PLAN_PATH = ROOT / "contracts/strategy_causal_pack_01/authority_extension_plan.v6.json"
PLAN_SHA256 = "30080d16132348fabceb91bffe110f43ee33824c4496622f736ee6a6c4d7af60"
AUTHORITY_ID = "BINANCE-UM-PRECUTOFF-AUTHORITY-V6"
HOUR_MS = 3_600_000
FEE_BPS = 5.0
BASE_SLIPPAGE_BPS = 2.0
STRESS_SLIPPAGE_BPS = 10.0
PRIMARY_CAPITAL = 2000
CAPITALS = (1000, 2000, 3000, 4000)
SIDES = {1: "long_after_positive_btc_impulse", -1: "short_after_negative_btc_impulse"}
MULTIPLICITY_ALPHA = 0.05 / (259 * 2)
EXPECTED_PATHS = {
    "manifest_v6": "research_evidence/manifests/binance-um-archive-precutoff-v6.json.gz",
    "pit_v9": "research_evidence/universe/pit-top40-qv30d-precutoff-v9.json",
    "filter_v4": "contracts/strategy_causal_pack_01/exchange_filter_authority.v4.json",
    "coverage_v4": "contracts/strategy_causal_pack_01/precutoff_authority_coverage_preflight.v4.json",
}


class C259Error(RuntimeError):
    pass


class IncompleteAuthority(C259Error):
    pass


class IncompleteEpisode(C259Error):
    pass


class EvaluationLocked(C259Error):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def load_frozen_contract() -> dict:
    if _sha(CONTRACT_PATH.read_bytes()) != CONTRACT_SHA256:
        raise IncompleteAuthority("C259 frozen contract byte mismatch")
    if _sha(PLAN_PATH.read_bytes()) != PLAN_SHA256:
        raise IncompleteAuthority("authority-v6 plan byte mismatch")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (contract.get("candidate_id"), contract.get("status"), contract.get("default_off"),
            contract.get("evaluation_locked")) != (
        259, "FROZEN_NOT_EVALUATED_RESEARCH_ONLY", True, True
    ):
        raise IncompleteAuthority("C259 contract is not frozen/default-OFF")
    if contract.get("authority", {}).get("plan_sha256") != PLAN_SHA256:
        raise IncompleteAuthority("C259 plan binding mismatch")
    return contract


@dataclass(frozen=True)
class ArtifactPin:
    path: str
    byte_sha256: str
    content_digest: str


@dataclass(frozen=True)
class AuthorityPins:
    protected_head: str
    manifest_v6: ArtifactPin
    pit_v9: ArtifactPin
    filter_v4: ArtifactPin
    coverage_v4: ArtifactPin


def _git(repo: Path, *args: str, raw: bool = False) -> str | bytes:
    run = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if run.returncode:
        raise IncompleteAuthority(run.stderr.decode(errors="replace").strip() or "git authority failure")
    return run.stdout if raw else run.stdout.decode("ascii").strip()


def _tree_bytes(repo: Path, head: str, path: str) -> bytes:
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise IncompleteAuthority("invalid authority tree path")
    raw = _git(repo, "cat-file", "blob", f"{head}:{path}", raw=True)
    assert isinstance(raw, bytes)
    return raw


def _decode(name: str, raw: bytes) -> dict:
    try:
        payload = gzip.decompress(raw) if name == "manifest_v6" else raw
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IncompleteAuthority(f"invalid {name}") from exc
    if not isinstance(value, dict):
        raise IncompleteAuthority(f"{name} is not an object")
    return value


def _declared_digest(value: Mapping[str, object]) -> str | None:
    return value.get("digest") if isinstance(value.get("digest"), str) else value.get("content_digest")  # type: ignore[return-value]


def _computed_digest(value: Mapping[str, object]) -> str:
    return _sha(_canon({key: item for key, item in value.items()
                        if key not in {"digest", "content_digest"}}))


def validate_authority_pins(pins: AuthorityPins, *, repo: Path = ROOT,
                            protected_ref: str = "refs/remotes/origin/master") -> dict[str, dict]:
    """Validate exact protected Git-tree bytes before any loader can run."""
    load_frozen_contract()
    if len(pins.protected_head) != 40 or any(c not in "0123456789abcdef" for c in pins.protected_head):
        raise IncompleteAuthority("invalid protected authority head")
    if _git(repo, "rev-parse", "--verify", f"{pins.protected_head}^{{commit}}") != pins.protected_head:
        raise IncompleteAuthority("authority head does not resolve exactly")
    if _git(repo, "rev-parse", "--verify", f"{protected_ref}^{{commit}}") != pins.protected_head:
        raise IncompleteAuthority("authority head is not protected ref")
    ancestry = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor",
                               CONTRACT_HEAD, pins.protected_head], capture_output=True)
    if ancestry.returncode:
        raise IncompleteAuthority("authority head does not descend from C259 contract")
    contract_tree_path = CONTRACT_PATH.relative_to(ROOT).as_posix()
    contract_raw = _tree_bytes(repo, pins.protected_head, contract_tree_path)
    if _sha(contract_raw) != CONTRACT_SHA256:
        raise IncompleteAuthority("protected C259 contract blob mismatch")
    try:
        protected_contract = json.loads(contract_raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IncompleteAuthority("protected C259 contract is invalid") from exc
    if (protected_contract.get("candidate_id"), protected_contract.get("default_off"),
            protected_contract.get("evaluation_locked")) != (259, True, True):
        raise IncompleteAuthority("protected C259 contract semantics mismatch")
    if _sha(_tree_bytes(repo, pins.protected_head, PLAN_PATH.relative_to(ROOT).as_posix())) != PLAN_SHA256:
        raise IncompleteAuthority("protected authority plan bytes changed")
    docs: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for name, expected in EXPECTED_PATHS.items():
        pin = getattr(pins, name)
        if pin.path != expected:
            raise IncompleteAuthority(f"{name} path mismatch")
        if any(len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
               for v in (pin.byte_sha256, pin.content_digest)):
            raise IncompleteAuthority(f"{name} malformed pin")
        raw = _tree_bytes(repo, pins.protected_head, expected)
        if _sha(raw) != pin.byte_sha256:
            raise IncompleteAuthority(f"{name} byte mismatch")
        doc = _decode(name, raw)
        if _declared_digest(doc) != pin.content_digest or _computed_digest(doc) != pin.content_digest:
            raise IncompleteAuthority(f"{name} content mismatch")
        docs[name], hashes[name] = doc, _sha(raw)
    manifest, pit, filters, coverage = (docs[n] for n in EXPECTED_PATHS)
    if manifest.get("format") != "zb-binance-um-archive-manifest/6":
        raise IncompleteAuthority("manifest-v6 identity mismatch")
    cutoff = manifest.get("cutoff_exclusive_utc", manifest.get("cutoff_exclusive"))
    cutoff_ms = manifest.get("cutoff_exclusive_ms")
    if cutoff != "2026-11-05T00:00:00Z" or cutoff_ms != 1793836800000:
        raise IncompleteAuthority("manifest cutoff mismatch")
    if pit.get("format") != "zb-pit-universe/2" or pit.get("manifest_digest") != _declared_digest(manifest):
        raise IncompleteAuthority("PIT-v9 manifest binding mismatch")
    cells = pit.get("cells")
    def pit_members(cell: Mapping[str, object]) -> object:
        books = cell.get("books")
        return books.get("crypto") if isinstance(books, Mapping) else cell.get("symbols")
    if (not isinstance(cells, list) or not cells
            or any(not isinstance(cell, Mapping) or not isinstance(cell.get("monday_ms"), int)
                   or int(cell["monday_ms"]) % (7 * 24 * HOUR_MS) != 4 * 24 * HOUR_MS
                   or int(cell["monday_ms"]) >= 1793836800000
                   or not isinstance(pit_members(cell), list) or not pit_members(cell)
                   or len(pit_members(cell)) != len(set(pit_members(cell)))  # type: ignore[arg-type]
                   or not all(isinstance(symbol, str) and symbol
                              for symbol in pit_members(cell))  # type: ignore[union-attr]
                   for cell in cells)):
        raise IncompleteAuthority("PIT-v9 weekly cells are empty or malformed")
    mondays = tuple(int(cell["monday_ms"]) for cell in cells)
    expected_mondays = tuple(range(1728864000000, 1793577600000 + 1,
                                   7 * 24 * HOUR_MS))
    if mondays != expected_mondays:
        raise IncompleteAuthority("PIT-v9 weekly range coverage mismatch")
    if (filters.get("authority"), filters.get("version")) != (
        "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", 4
    ):
        raise IncompleteAuthority("filter-v4 identity mismatch")
    if (filters.get("pit_universe_sha256") != hashes["pit_v9"]
            or filters.get("pit_universe_digest") != _declared_digest(pit)):
        raise IncompleteAuthority("filter-v4 PIT binding mismatch")
    expected_folds = {
        "development_eligible_segment": "2024-12-13T04:44:48Z",
        "wf1": "2024-12-13T04:44:48Z", "wf2": "2024-12-13T04:44:48Z",
        "wf3": "2026-02-17T04:13:28Z", "sealed_holdout": "2026-02-17T04:13:28Z",
    }
    if filters.get("fold_bindings") != expected_folds:
        raise IncompleteAuthority("filter-v4 fold bindings mismatch")
    gaps = coverage.get("guaranteed_period_gaps")
    result = coverage.get("required_result", coverage.get("result"))
    if (coverage.get("format") != "zb-strategy-causal-pack-01-authority-coverage-preflight/1"
            or gaps != 0 or coverage.get("gaps") not in ([], None)
            or result != "ZERO_GUARANTEED_PERIOD_GAPS"
            or coverage.get("required_streams") != [
                "klines:1d", "klines:1h", "markPriceKlines:1h", "fundingRate:null"]
            or coverage.get("coverage_start_ms") != 1728864000000
            or coverage.get("cutoff_exclusive_ms") != 1793836800000):
        raise IncompleteAuthority("coverage-v4 is not ZERO_GUARANTEED_PERIOD_GAPS")
    if (coverage.get("manifest_digest") != _declared_digest(manifest)
            or coverage.get("pit_universe_digest") != _declared_digest(pit)
            or coverage.get("filter_authority_digest") != _declared_digest(filters)):
        raise IncompleteAuthority("coverage-v4 cross-binding mismatch")
    return docs


def run_with_authority(pins: AuthorityPins, loader: Callable[[dict[str, dict]], object], *,
                       repo: Path = ROOT, protected_ref: str = "refs/remotes/origin/master") -> object:
    authorities = validate_authority_pins(pins, repo=repo, protected_ref=protected_ref)
    return loader(authorities)


def assert_real_evaluation_authorized() -> None:
    load_frozen_contract()
    raise EvaluationLocked("C259_REAL_EVALUATION_LOCKED: final protected authority pins are absent; no loader invoked")


@dataclass(frozen=True)
class Bar:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float

    def validate(self) -> None:
        if self.open_ms % HOUR_MS or not all(math.isfinite(v) and v > 0 for v in (
            self.open, self.high, self.low, self.close, self.quote_volume
        )):
            raise IncompleteAuthority("invalid canonical bar")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise IncompleteAuthority("invalid OHLC")


def exact_index(bars: Sequence[Bar]) -> dict[int, Bar]:
    result: dict[int, Bar] = {}
    for bar in bars:
        bar.validate()
        if bar.open_ms in result:
            raise IncompleteAuthority("duplicate canonical bar")
        result[bar.open_ms] = bar
    return result


def next_open(bars: Sequence[Bar], decision_close_ms: int) -> Bar:
    if not isinstance(decision_close_ms, int) or decision_close_ms % HOUR_MS:
        raise IncompleteAuthority("decision close is off canonical grid")
    index = exact_index(bars)
    found = sorted((bar for bar in index.values() if bar.open_ms > decision_close_ms), key=lambda b: b.open_ms)
    if not found or found[0].open_ms != decision_close_ms + HOUR_MS:
        raise IncompleteAuthority("missing exact next canonical open")
    return found[0]


@dataclass(frozen=True)
class SignalFit:
    prior_btc_closes: tuple[ClosePrice, ...]
    prior_alt_closes: tuple[ClosePrice, ...]
    btc_decision: ClosePrice
    alt_decision: ClosePrice
    decision_close_ms: int
    btc_decision_price: float
    alt_decision_price: float
    window_start_ms: int
    window_end_ms: int
    window_identity_sha256: str
    beta0: float
    gamma: float
    se_gamma: float
    t_gamma: float
    residual_d: float
    forecast: float
    impulse_threshold: float
    side: int | None
    qualifies: bool


@dataclass(frozen=True)
class ClosePrice:
    close_ms: int
    price: float


def _prior_returns(closes: Sequence[ClosePrice], decision_close_ms: int) -> tuple[float, ...]:
    if len(closes) != 721:
        raise IncompleteAuthority("return window requires exactly 721 causal closes")
    expected = tuple(decision_close_ms - (721 - index) * HOUR_MS for index in range(721))
    if tuple(item.close_ms for item in closes) != expected:
        raise IncompleteAuthority("price window is not exact contiguous prior 721h")
    if not all(math.isfinite(item.price) and item.price > 0 for item in closes):
        raise IncompleteAuthority("price window contains non-positive/non-finite close")
    return tuple(math.log(current.price / previous.price)
                 for previous, current in zip(closes, closes[1:]))


def fit_signal(prior_btc_closes: Sequence[ClosePrice], prior_alt_closes: Sequence[ClosePrice], *,
               btc_decision: ClosePrice, alt_decision: ClosePrice) -> SignalFit:
    decision_close_ms = btc_decision.close_ms
    btc_decision_price, alt_decision_price = btc_decision.price, alt_decision.price
    if (not isinstance(decision_close_ms, int) or decision_close_ms % HOUR_MS
            or alt_decision.close_ms != decision_close_ms
            or not all(math.isfinite(value) and value > 0
                       for value in (btc_decision_price, alt_decision_price))):
        raise IncompleteAuthority("invalid decision close authority")
    prior_btc = _prior_returns(prior_btc_closes, decision_close_ms)
    prior_alt = _prior_returns(prior_alt_closes, decision_close_ms)
    btc_d = math.log(btc_decision_price / prior_btc_closes[-1].price)
    alt_d = math.log(alt_decision_price / prior_alt_closes[-1].price)
    if len(prior_btc) != 720 or len(prior_alt) != 720:
        raise IncompleteAuthority("signal requires exactly 720 prior paired returns")
    values = tuple(prior_btc) + tuple(prior_alt) + (btc_d, alt_d)
    if not all(math.isfinite(v) for v in values):
        raise IncompleteAuthority("non-finite return")
    denominator = math.fsum(v * v for v in prior_btc)
    if denominator <= 0:
        raise IncompleteAuthority("non-positive beta denominator")
    beta = math.fsum(a * b for a, b in zip(prior_alt, prior_btc)) / denominator
    residuals = [a - beta * b for a, b in zip(prior_alt, prior_btc)]
    x = prior_btc[:-1]
    y = residuals[1:]
    xx = math.fsum(v * v for v in x)
    if xx <= 0:
        raise IncompleteAuthority("non-positive lag denominator")
    gamma = math.fsum(a * b for a, b in zip(y, x)) / xx
    errors = [a - gamma * b for a, b in zip(y, x)]
    sse = math.fsum(v * v for v in errors)
    if sse < 0:
        raise IncompleteAuthority("negative SSE")
    se = math.sqrt((sse / 718) / xx)
    if not math.isfinite(se) or se <= 0:
        raise IncompleteAuthority("invalid gamma standard error")
    t = gamma / se
    scale = statistics.median(abs(v) for v in prior_btc[-168:])
    if not math.isfinite(scale) or scale <= 0:
        raise IncompleteAuthority("invalid impulse scale")
    threshold = max(0.0075, 3 * scale)
    residual_d = alt_d - beta * btc_d
    forecast = gamma * btc_d
    side = 1 if btc_d >= threshold else -1 if btc_d <= -threshold else None
    qualifies = bool(gamma >= 0.10 and t >= 2.0 and (
        side == 1 and forecast >= 0.0028 and residual_d <= 0.25 * forecast
        or side == -1 and forecast <= -0.0028 and residual_d >= 0.25 * forecast
    ))
    window_body = [(btc.close_ms, btc.price, alt.price)
                   for btc, alt in zip(prior_btc_closes, prior_alt_closes)]
    window_identity = _sha(_canon(window_body))
    return SignalFit(tuple(prior_btc_closes), tuple(prior_alt_closes),
                     btc_decision, alt_decision,
                     decision_close_ms, btc_decision_price, alt_decision_price,
                     prior_btc_closes[0].close_ms, prior_btc_closes[-1].close_ms,
                     window_identity,
                     beta, gamma, se, t, residual_d, forecast, threshold, side, qualifies)


def select_candidates(rows: Iterable[tuple[str, SignalFit]], side: int, *, decision_close_ms: int,
                      occupied: Iterable[str] = ()) -> tuple[str, ...]:
    occupied_set = set(occupied)
    materialized = list(rows)
    for _symbol, fit in materialized:
        recomputed = fit_signal(fit.prior_btc_closes, fit.prior_alt_closes,
                                btc_decision=fit.btc_decision,
                                alt_decision=fit.alt_decision)
        if (fit.decision_close_ms != decision_close_ms
                or fit.window_start_ms != decision_close_ms-721*HOUR_MS
                or fit.window_end_ms != decision_close_ms-HOUR_MS
                or len(fit.window_identity_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in fit.window_identity_sha256)
                or fit != recomputed):
            raise IncompleteAuthority("candidate signal provenance is stale or mismatched")
    eligible = [(symbol, fit) for symbol, fit in materialized
                if fit.qualifies and fit.side == side and symbol not in occupied_set]
    eligible.sort(key=lambda item: (-abs(item[1].forecast - item[1].residual_d), item[0]))
    return tuple(symbol for symbol, _ in eligible[:3])


def filter_authority_available(decision_close_ms: int) -> bool:
    """The frozen filter authority starts at 2024-12-14T00:00:00Z."""
    return decision_close_ms >= 1734134400000


def liquidity_admission(*, decision_quote_volume: float,
                        preceding_quote_volumes: Sequence[float],
                        raw_entry_notional: Decimal,
                        causal_quote_volume_24h: float) -> bool:
    if (len(preceding_quote_volumes) != 24 or raw_entry_notional <= 0
            or not math.isfinite(decision_quote_volume) or decision_quote_volume <= 0
            or not math.isfinite(causal_quote_volume_24h) or causal_quote_volume_24h <= 0
            or not all(math.isfinite(v) and v > 0 for v in preceding_quote_volumes)):
        raise IncompleteAuthority("invalid causal liquidity window")
    return (decision_quote_volume >= statistics.median(preceding_quote_volumes)
            and Decimal(str(causal_quote_volume_24h)) >= Decimal(100) * raw_entry_notional)


def cost_admission(*, entry: Decimal, stop: Decimal, forecast: float) -> bool:
    if entry <= 0 or stop <= 0 or not math.isfinite(forecast):
        raise C259Error("invalid cost admission input")
    base_round_trip = Decimal("0.0014")
    return (abs(entry - stop) >= Decimal(5) * base_round_trip * entry
            and abs(forecast) >= 2 * float(base_round_trip))


def atr24_wilder(bars: Sequence[Bar], decision_close_ms: int) -> float:
    index = exact_index(bars)
    through = sorted((bar for bar in bars if bar.open_ms + HOUR_MS <= decision_close_ms),
                     key=lambda b: b.open_ms)
    if len(through) < 25 or through[-1].open_ms + HOUR_MS != decision_close_ms:
        raise IncompleteAuthority("ATR requires 25 contiguous bars through decision")
    if any(b.open_ms != through[0].open_ms + i * HOUR_MS for i, b in enumerate(through)):
        raise IncompleteAuthority("ATR gap")
    ranges = [max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
              for prev, cur in zip(through, through[1:])]
    atr = math.fsum(ranges[:24]) / 24
    for value in ranges[24:]:
        atr = (23 * atr + value) / 24
    if not math.isfinite(atr) or atr <= 0:
        raise IncompleteAuthority("invalid ATR")
    return atr


@dataclass(frozen=True)
class Filters:
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def protection_prices(entry: Decimal, atr: Decimal, side: int, tick: Decimal) -> tuple[Decimal, Decimal]:
    if entry <= 0 or atr <= 0 or tick <= 0 or side not in (-1, 1):
        raise C259Error("invalid protection input")
    raw_stop = entry - Decimal(2) * atr if side == 1 else entry + Decimal(2) * atr
    mode = ROUND_CEILING if side == 1 else ROUND_FLOOR
    stop = (raw_stop / tick).to_integral_value(rounding=mode) * tick
    distance = abs(entry - stop)
    raw_target = entry + Decimal(2) * distance if side == 1 else entry - Decimal(2) * distance
    target_mode = ROUND_FLOOR if side == 1 else ROUND_CEILING
    target = (raw_target / tick).to_integral_value(rounding=target_mode) * tick
    side_correct = stop < entry < target if side == 1 else target < entry < stop
    if stop <= 0 or target <= 0 or distance <= 0 or not side_correct \
            or abs(target - entry) <= 0:
        raise C259Error("protection collapsed after tick rounding")
    return stop, target


def impact_drag(quantity: Decimal, reference: Decimal, quote_volume_24h: float) -> Decimal:
    if quantity <= 0 or reference <= 0 or not math.isfinite(quote_volume_24h) or quote_volume_24h <= 0:
        raise IncompleteAuthority("invalid causal impact authority")
    participation = float(quantity * reference) / quote_volume_24h
    if participation > 0.01:
        raise IncompleteEpisode("impact participation exceeds one percent")
    bps = min(10.0, 10.0 * math.sqrt(participation / 0.01))
    return quantity * reference * Decimal(str(bps)) / Decimal(10_000)


@dataclass(frozen=True)
class VolumePoint:
    open_ms: int
    quote_volume: float


def _volume_rows_total(rows: Sequence[VolumePoint], impact_asof_ms: int) -> float:
    expected = tuple(range(impact_asof_ms-24*HOUR_MS, impact_asof_ms, HOUR_MS))
    if (not isinstance(impact_asof_ms, int) or impact_asof_ms % HOUR_MS
            or len(rows) != 24
            or tuple(row.open_ms for row in rows) != expected
            or not all(math.isfinite(row.quote_volume) and row.quote_volume > 0
                       for row in rows)):
        raise IncompleteAuthority("volume rows are not exact contiguous causal authority")
    total = math.fsum(row.quote_volume for row in rows)
    if not math.isfinite(total) or total <= 0:
        raise IncompleteAuthority("causal quote volume is invalid")
    return total


@dataclass(frozen=True)
class SizedPosition:
    side: int
    entry: Decimal
    stop: Decimal
    filters: Filters
    side_book_equity: Decimal
    entry_impact_asof_ms: int
    entry_volume_rows: tuple[VolumePoint, ...]
    quote_volume_24h: float
    quantity: Decimal
    initial_risk_cash: Decimal
    requested_risk_cash: Decimal
    raw_notional: Decimal


def size_position(*, side: int, entry: Decimal, stop: Decimal, filters: Filters,
                  side_book_equity: Decimal, entry_impact_asof_ms: int,
                  entry_volume_rows: Sequence[VolumePoint]) -> SizedPosition | None:
    if side not in (-1, 1) or entry <= 0 or stop <= 0 or side_book_equity <= 0 \
            or filters.step <= 0 or filters.tick <= 0:
        raise C259Error("invalid sizing authority")
    volume_rows = tuple(entry_volume_rows)
    quote_volume_24h = _volume_rows_total(volume_rows, entry_impact_asof_ms)
    requested = side_book_equity * Decimal("0.01")
    fee = Decimal(str(FEE_BPS / 10_000))
    stress_slip = Decimal(str(STRESS_SLIPPAGE_BPS / 10_000))
    entry_execution = entry * (Decimal(1) + Decimal(side) * stress_slip)
    stop_execution = stop * (Decimal(1) - Decimal(side) * stress_slip)
    fee_drag = abs(entry_execution) * fee + abs(stop_execution) * fee
    slippage_drag = (entry + stop) * stress_slip
    base_loss = abs(entry - stop) + fee_drag + slippage_drag
    guess = min(requested / base_loss, side_book_equity * Decimal("0.5") / entry)
    quantity = _floor(guess, filters.step)
    while quantity >= filters.min_qty:
        notional = quantity * entry
        try:
            impacts = impact_drag(quantity, entry, quote_volume_24h) + impact_drag(
                quantity, stop, quote_volume_24h)
        except IncompleteEpisode:
            quantity -= filters.step
            continue
        risk = quantity * base_loss + impacts
        if (quantity <= filters.max_qty and notional >= filters.min_notional
                and notional <= side_book_equity * Decimal("0.5") and risk <= requested):
            return SizedPosition(side, entry, stop, filters, side_book_equity,
                                 entry_impact_asof_ms, volume_rows, quote_volume_24h,
                                 quantity, risk, requested, notional)
        quantity -= filters.step
    return None


@dataclass(frozen=True)
class Fill:
    timestamp_ms: int
    phase: str
    symbol: str
    side: int
    quantity: float
    reference: float
    movement_cash: float
    fee_drag: float
    slippage_drag: float
    impact_drag: float
    slippage_bps: float
    impact_asof_ms: int
    volume_rows: tuple[VolumePoint, ...]


def make_fill(*, timestamp_ms: int, phase: str, symbol: str, side: int, quantity: float,
              reference: float, movement_from: float | None, volume_bars: Sequence[Bar],
              slippage_bps: float = BASE_SLIPPAGE_BPS) -> Fill:
    if side not in (-1, 1) or quantity <= 0 or reference <= 0:
        raise C259Error("invalid fill")
    execution = reference * (1 + side * slippage_bps / 10_000)
    fee = abs(quantity * execution) * FEE_BPS / 10_000
    slip = abs(quantity) * abs(execution - reference)
    impact_asof_ms = (timestamp_ms-HOUR_MS if phase in {"STOP_RANGE", "TARGET_RANGE"}
                      else timestamp_ms)
    quote_volume_24h = causal_quote_volume_24h(volume_bars, impact_asof_ms)
    impact = float(impact_drag(Decimal(str(quantity)), Decimal(str(reference)), quote_volume_24h))
    movement = 0.0 if movement_from is None else -side * quantity * (reference - movement_from)
    required = tuple(sorted((bar for bar in volume_bars
                             if impact_asof_ms-24*HOUR_MS <= bar.open_ms < impact_asof_ms),
                            key=lambda bar: bar.open_ms))
    rows = tuple(VolumePoint(bar.open_ms, bar.quote_volume) for bar in required)
    return Fill(timestamp_ms, phase, symbol, side, quantity, reference, movement, fee, slip,
                impact, slippage_bps, impact_asof_ms, rows)


def funding_cash(position_side: int, quantity: float, mark: float, rate: float) -> float:
    if (position_side not in (-1, 1) or quantity <= 0 or mark <= 0
            or not all(math.isfinite(v) for v in (quantity, mark, rate))):
        raise IncompleteAuthority("invalid funding authority")
    return -position_side * abs(quantity) * mark * rate


@dataclass(frozen=True)
class MarkClose:
    close_ms: int
    price: float


def causal_mark_before(marks: Sequence[MarkClose], event_ms: int) -> MarkClose:
    if not isinstance(event_ms, int) or event_ms % HOUR_MS:
        raise IncompleteAuthority("event is off canonical grid")
    seen: set[int] = set()
    eligible: list[MarkClose] = []
    for mark in marks:
        if (not isinstance(mark.close_ms, int) or mark.close_ms % HOUR_MS
                or not math.isfinite(mark.price) or mark.price <= 0
                or mark.close_ms in seen):
            raise IncompleteAuthority("invalid or duplicate causal mark")
        seen.add(mark.close_ms)
        if mark.close_ms < event_ms:
            eligible.append(mark)
    if not eligible:
        raise IncompleteAuthority("missing causal mark strictly before event")
    result = max(eligible, key=lambda mark: mark.close_ms)
    if result.close_ms != event_ms - HOUR_MS:
        raise IncompleteAuthority("causal prior mark is missing/compressed")
    return result


@dataclass(frozen=True)
class FundingEvent:
    timestamp_ms: int
    rate: float


def validate_funding_events(events: Sequence[FundingEvent]) -> tuple[FundingEvent, ...]:
    seen: set[int] = set()
    ordered: list[FundingEvent] = []
    for event in sorted(events, key=lambda item: item.timestamp_ms):
        if (not isinstance(event.timestamp_ms, int) or event.timestamp_ms < 0
                or event.timestamp_ms >= 1793836800000 or event.timestamp_ms in seen
                or not math.isfinite(event.rate)):
            raise IncompleteAuthority("invalid or duplicate funding event")
        seen.add(event.timestamp_ms)
        ordered.append(event)
    return tuple(ordered)


@dataclass(frozen=True)
class AppliedFunding:
    event: FundingEvent
    mark: PositionMark


def causal_quote_volume_24h(bars: Sequence[Bar], impact_asof_ms: int) -> float:
    if not isinstance(impact_asof_ms, int) or impact_asof_ms % HOUR_MS:
        raise IncompleteAuthority("impact as-of is off canonical grid")
    index = exact_index(bars)
    required = tuple(impact_asof_ms - (24 - offset) * HOUR_MS for offset in range(24))
    if any(timestamp not in index for timestamp in required):
        raise IncompleteAuthority("impact volume window missing/compressed")
    return math.fsum(index[timestamp].quote_volume for timestamp in required)


def exit_reference(*, side: int, stop: float, target: float, bar: Bar,
                   scheduled_due: bool) -> tuple[str, float, int] | None:
    bar.validate()
    close_ms = bar.open_ms + HOUR_MS
    if side == 1 and bar.open <= stop or side == -1 and bar.open >= stop:
        return "STOP_GAP", bar.open, bar.open_ms
    if side == 1 and bar.open >= target or side == -1 and bar.open <= target:
        return "TARGET_GAP", target, bar.open_ms
    if scheduled_due:
        return "SCHEDULED", bar.open, bar.open_ms
    stop_touch = bar.low <= stop if side == 1 else bar.high >= stop
    target_touch = bar.high >= target if side == 1 else bar.low <= target
    if stop_touch:
        return "STOP_RANGE", stop, close_ms
    if target_touch:
        return "TARGET_RANGE", target, close_ms
    return None


EVENT_PHASE = {"INTRABAR_PRIOR_BOUNDARY": 0, "FUNDING": 1, "GAP_OR_SCHEDULED": 2,
               "PROTECTION_FAILURE": 3, "ENTRY": 4, "MARK": 5}


def ordered_events(events: Iterable[tuple[int, str, str, object]]) -> list[tuple[int, str, str, object]]:
    return sorted(events, key=lambda row: (row[0], EVENT_PHASE[row[1]], row[2]))


@dataclass(frozen=True)
class ClosedTrade:
    attempt_id: str
    side: int
    symbol: str
    entry_ms: int
    close_ms: int
    regime: str
    fills: tuple[Fill, ...]
    funding_events: tuple[AppliedFunding, ...]
    sizing: SizedPosition
    decision_close_ms: int
    atr_bars: tuple[Bar, ...]
    lifecycle_bars: tuple[Bar, ...]
    entry_bar: Bar
    exit_bar: Bar
    stop: float
    target: float
    incomplete: bool = False

    @property
    def funding(self) -> float:
        quantity = self.fills[0].quantity
        return math.fsum(funding_cash(self.side, quantity, item.mark.price, item.event.rate)
                         for item in self.funding_events)

    @property
    def initial_risk_cash(self) -> float:
        return float(self.sizing.initial_risk_cash)

    @property
    def net_cash(self) -> float:
        return math.fsum([self.funding] + [
            fill.movement_cash - fill.fee_drag - fill.slippage_drag - fill.impact_drag
            for fill in self.fills
        ])

    @property
    def trade_R(self) -> float:
        if self.initial_risk_cash <= 0:
            raise C259Error("invalid initial risk")
        return self.net_cash / self.initial_risk_cash


def validate_closed_trade(trade: ClosedTrade) -> None:
    if (not trade.attempt_id or trade.side not in (-1, 1) or not trade.symbol
            or trade.entry_ms % HOUR_MS or trade.close_ms % HOUR_MS
            or trade.close_ms <= trade.entry_ms or trade.initial_risk_cash <= 0
            or not math.isfinite(trade.initial_risk_cash)
            or trade.regime not in {"BTC_ABOVE_OR_ON_EMA200", "BTC_BELOW_EMA200"}):
        raise IncompleteEpisode("invalid closed-trade identity or authority")
    trade.entry_bar.validate(); trade.exit_bar.validate()
    if (trade.decision_close_ms != trade.entry_ms-HOUR_MS
            or trade.entry_ms != trade.decision_close_ms+HOUR_MS):
        raise IncompleteEpisode("entry is not the exact next open after ATR decision")
    causal_atr = atr24_wilder(trade.atr_bars, trade.decision_close_ms)
    derived_stop, derived_target = protection_prices(
        trade.sizing.entry, Decimal(str(causal_atr)), trade.side, trade.sizing.filters.tick)
    if (trade.sizing.stop != derived_stop
            or Decimal(str(trade.stop)) != derived_stop
            or Decimal(str(trade.target)) != derived_target):
        raise IncompleteEpisode("stop/target are not derived from exact ATR/tick authority")
    sizing_volume = _volume_rows_total(trade.sizing.entry_volume_rows,
                                       trade.sizing.entry_impact_asof_ms)
    if trade.sizing.quote_volume_24h != sizing_volume:
        raise IncompleteEpisode("sizing impact volume is not the exact 24-row fsum")
    recomputed_sizing = size_position(
        side=trade.sizing.side, entry=trade.sizing.entry, stop=trade.sizing.stop,
        filters=trade.sizing.filters, side_book_equity=trade.sizing.side_book_equity,
        entry_impact_asof_ms=trade.sizing.entry_impact_asof_ms,
        entry_volume_rows=trade.sizing.entry_volume_rows)
    if recomputed_sizing is None or recomputed_sizing != trade.sizing:
        raise IncompleteEpisode("initial risk is not bound to exact conservative sizing")
    if trade.incomplete:
        entries = [fill for fill in trade.fills if fill.phase == "ENTRY"]
        exits = [fill for fill in trade.fills if fill.phase == "PROTECTION_FAILURE_CLEANUP"]
    else:
        entries = [fill for fill in trade.fills if fill.phase == "ENTRY"]
        exits = [fill for fill in trade.fills if fill.phase in {
            "STOP_GAP", "TARGET_GAP", "SCHEDULED", "STOP_RANGE", "TARGET_RANGE"}]
    if len(trade.fills) != 2 or len(entries) != 1 or len(exits) != 1:
        raise IncompleteEpisode("closed trade is not one exact flat lifecycle")
    entry, exit_fill = entries[0], exits[0]
    for fill in trade.fills:
        if (fill.symbol != trade.symbol or fill.quantity <= 0 or fill.reference <= 0
                or not all(math.isfinite(value) for value in (
                    fill.quantity, fill.reference, fill.movement_cash, fill.fee_drag,
                    fill.slippage_drag, fill.impact_drag))
                or min(fill.fee_drag, fill.slippage_drag, fill.impact_drag) < 0):
            raise IncompleteEpisode("invalid fill cashflow")
        if (fill.slippage_bps not in (BASE_SLIPPAGE_BPS, STRESS_SLIPPAGE_BPS)
                or fill.impact_asof_ms != (fill.timestamp_ms-HOUR_MS
                                           if fill.phase in {"STOP_RANGE", "TARGET_RANGE"}
                                           else fill.timestamp_ms)
                or len(fill.volume_rows) != 24
                or tuple(row.open_ms for row in fill.volume_rows) != tuple(
                    range(fill.impact_asof_ms-24*HOUR_MS, fill.impact_asof_ms, HOUR_MS))
                or not all(math.isfinite(row.quote_volume) and row.quote_volume > 0
                           for row in fill.volume_rows)):
            raise IncompleteEpisode("fill volume/slippage authority is not exact")
        volume = math.fsum(row.quote_volume for row in fill.volume_rows)
        expected_impact = float(impact_drag(Decimal(str(fill.quantity)),
                                            Decimal(str(fill.reference)), volume))
        execution = fill.reference * (1 + fill.side * fill.slippage_bps / 10_000)
        expected_fee = abs(fill.quantity * execution) * FEE_BPS / 10_000
        expected_slip = abs(fill.quantity) * abs(execution - fill.reference)
        if (not math.isclose(fill.impact_drag, expected_impact, abs_tol=1e-12)
                or not math.isclose(fill.fee_drag, expected_fee, abs_tol=1e-12)
                or not math.isclose(fill.slippage_drag, expected_slip, abs_tol=1e-12)):
            raise IncompleteEpisode("fill costs do not recompute from frozen authority")
    if (entry.side != trade.side or exit_fill.side != -trade.side
            or entry.quantity != exit_fill.quantity
            or Decimal(str(entry.quantity)) != trade.sizing.quantity
            or trade.sizing.side != trade.side
            or trade.sizing.entry != Decimal(str(entry.reference))
            or trade.sizing.stop != Decimal(str(trade.stop))
            or entry.timestamp_ms != trade.entry_ms or exit_fill.timestamp_ms != trade.close_ms):
        raise IncompleteEpisode("closed trade quantity is not conserved to flat")
    if (trade.sizing.entry_impact_asof_ms != entry.impact_asof_ms
            or trade.sizing.entry_volume_rows != entry.volume_rows
            or trade.sizing.quote_volume_24h != math.fsum(
                row.quote_volume for row in entry.volume_rows)):
        raise IncompleteEpisode("sizing impact authority differs from exact entry fill rows")
    lifecycle = tuple(trade.lifecycle_bars)
    if (not lifecycle or lifecycle[0].open_ms != trade.entry_ms
            or tuple(bar.open_ms for bar in lifecycle)
            != tuple(range(trade.entry_ms,
                           trade.entry_ms+len(lifecycle)*HOUR_MS, HOUR_MS))):
        raise IncompleteAuthority("lifecycle bar tape is missing/compressed")
    for bar in lifecycle:
        bar.validate()
    if (trade.entry_bar.open_ms != trade.entry_ms
            or trade.entry_bar != lifecycle[0]
            or not math.isclose(trade.entry_bar.open, entry.reference, abs_tol=1e-12)
            or not (trade.stop < entry.reference < trade.target if trade.side == 1
                    else trade.target < entry.reference < trade.stop)):
        raise IncompleteEpisode("entry/protection canonical bar authority mismatch")
    if not trade.incomplete:
        expected = None
        source_bar = None
        for bar in lifecycle:
            candidate = exit_reference(
                side=trade.side, stop=float(derived_stop), target=float(derived_target), bar=bar,
                scheduled_due=bar.open_ms >= trade.entry_ms+12*HOUR_MS)
            if candidate is not None:
                expected, source_bar = candidate, bar
                break
        if expected is None or source_bar is None:
            raise IncompleteEpisode("lifecycle has no mandatory frozen exit")
        required_tape = tuple(range(trade.entry_ms, source_bar.open_ms+HOUR_MS, HOUR_MS))
        if tuple(bar.open_ms for bar in lifecycle) != required_tape:
            raise IncompleteAuthority("lifecycle bar tape is missing, extra, or post-exit")
        if trade.exit_bar != source_bar:
            raise IncompleteEpisode("exit bar is not the first derived lifecycle authority")
        if expected != (exit_fill.phase, exit_fill.reference, exit_fill.timestamp_ms):
            raise IncompleteEpisode("exit fill is not bound to canonical bar/protection")
    elif (trade.exit_bar.open_ms != trade.close_ms
          or trade.close_ms != trade.entry_ms+HOUR_MS
          or trade.exit_bar != lifecycle[-1]
          or tuple(bar.open_ms for bar in lifecycle)
          != tuple(range(trade.entry_ms, trade.close_ms+HOUR_MS, HOUR_MS))
          or not math.isclose(trade.exit_bar.open, exit_fill.reference, abs_tol=1e-12)):
        raise IncompleteEpisode("cleanup fill is not bound to canonical exit bar")
    expected_movement = trade.side * entry.quantity * (exit_fill.reference-entry.reference)
    if (entry.movement_cash != 0
            or not math.isclose(exit_fill.movement_cash, expected_movement, abs_tol=1e-12)):
        raise IncompleteEpisode("fill movement is not derived from frozen references")
    seen_funding: set[int] = set()
    validate_funding_events(tuple(item.event for item in trade.funding_events))
    for item in trade.funding_events:
        event, mark = item.event, item.mark
        validate_position_mark(mark)
        exit_includes_same_boundary = exit_fill.phase not in {"STOP_RANGE", "TARGET_RANGE"}
        exposed = (trade.entry_ms < event.timestamp_ms < trade.close_ms
                   or exit_includes_same_boundary and event.timestamp_ms == trade.close_ms)
        if (event.timestamp_ms in seen_funding or not exposed
                or event.timestamp_ms % HOUR_MS
                or mark.timestamp_ms != event.timestamp_ms-HOUR_MS
                or mark.symbol != trade.symbol
                or mark.price <= 0 or not all(math.isfinite(value) for value in (
                    event.rate, mark.price))):
            raise IncompleteEpisode("funding event identity/cash is invalid")
        seen_funding.add(event.timestamp_ms)


@dataclass
class SideBook:
    capital: Decimal
    positions: dict[str, SizedPosition] = field(default_factory=dict)
    settled_cash: Decimal | None = None

    def __post_init__(self) -> None:
        if self.settled_cash is None:
            self.settled_cash = self.capital

    def can_admit(self, symbol: str, sized: SizedPosition) -> bool:
        return (symbol not in self.positions and len(self.positions) < 3
                and sum((p.initial_risk_cash for p in self.positions.values()), Decimal(0))
                + sized.initial_risk_cash <= self.capital * Decimal("0.03")
                and sum((p.raw_notional for p in self.positions.values()), Decimal(0))
                + sized.raw_notional <= self.capital * Decimal("1.5"))

    def admit(self, symbol: str, sized: SizedPosition) -> None:
        if not self.can_admit(symbol, sized):
            raise C259Error("side-book cap")
        self.positions[symbol] = sized


def protection_failure(*, attempt_id: str, side: int, symbol: str, entry_ms: int,
                       cleanup_ms: int, fills: Sequence[Fill],
                       funding_events: Sequence[AppliedFunding],
                       sizing: SizedPosition, decision_close_ms: int,
                       atr_bars: Sequence[Bar], lifecycle_bars: Sequence[Bar],
                       entry_bar: Bar, exit_bar: Bar,
                       stop: float, target: float, regime: str) -> ClosedTrade:
    entries = [fill for fill in fills if fill.phase == "ENTRY"]
    cleanups = [fill for fill in fills if fill.phase == "PROTECTION_FAILURE_CLEANUP"]
    if (side not in (-1, 1) or cleanup_ms <= entry_ms or len(fills) != 2
            or len(entries) != 1 or len(cleanups) != 1):
        raise IncompleteEpisode("protection cleanup must follow accepted entry")
    entry, cleanup = entries[0], cleanups[0]
    if (entry.symbol != symbol or cleanup.symbol != symbol or entry.side != side
            or cleanup.side != -side or entry.quantity != cleanup.quantity
            or entry.timestamp_ms != entry_ms or cleanup.timestamp_ms != cleanup_ms):
        raise IncompleteEpisode("protection failure lifecycle is not exactly flat")
    trade = ClosedTrade(attempt_id, side, symbol, entry_ms, cleanup_ms, regime,
                        tuple(fills), tuple(funding_events), sizing, decision_close_ms,
                        tuple(atr_bars), tuple(lifecycle_bars), entry_bar, exit_bar,
                        stop, target, True)
    validate_closed_trade(trade)
    return trade


def marked_equity_drawdown_R(equity_points: Sequence[tuple[int, int, float]],
                             starting_capital: float) -> float:
    """Drawdown over exact (timestamp, frozen-phase-order, total equity) points."""
    if starting_capital <= 0 or not equity_points:
        raise C259Error("undefined marked-equity drawdown")
    ordered = sorted(equity_points, key=lambda row: (row[0], row[1]))
    if list(equity_points) != ordered:
        raise IncompleteAuthority("marked-equity sequence is not phase ordered")
    peak, maximum = starting_capital, 0.0
    previous: tuple[int, int] | None = None
    for timestamp, phase_order, equity in ordered:
        if (not isinstance(timestamp, int) or timestamp < 0 or phase_order not in range(6)
                or not math.isfinite(equity)
                or (previous is not None and (timestamp, phase_order) == previous)):
            raise IncompleteAuthority("invalid marked-equity sequence")
        previous = (timestamp, phase_order)
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum / (0.01 * starting_capital)


def closed_realized_drawdown_R(trades: Sequence[ClosedTrade]) -> float:
    if not trades:
        raise C259Error("undefined closed-realized drawdown")
    equity_R = peak_R = maximum_R = 0.0
    identities: set[str] = set()
    for trade in sorted(trades, key=lambda item: (item.close_ms, item.symbol, item.attempt_id)):
        if trade.attempt_id in identities:
            raise IncompleteAuthority("duplicate closed attempt identity")
        identities.add(trade.attempt_id)
        equity_R = math.fsum((equity_R, trade.trade_R))
        peak_R = max(peak_R, equity_R)
        maximum_R = max(maximum_R, peak_R - equity_R)
    return maximum_R


def gate_drawdown_R(*, marked_drawdown_R: float, realized_drawdown_R: float) -> float:
    if not all(math.isfinite(v) and v >= 0 for v in (marked_drawdown_R, realized_drawdown_R)):
        raise C259Error("invalid drawdown input")
    return max(marked_drawdown_R, realized_drawdown_R)


@dataclass
class EMA200:
    closes: list[float] = field(default_factory=list)
    value: float | None = None
    last_open_ms: int | None = None

    def update(self, bar: Bar) -> str | None:
        bar.validate()
        if self.last_open_ms is not None and bar.open_ms != self.last_open_ms + HOUR_MS:
            self.closes.clear(); self.value = None
        self.last_open_ms = bar.open_ms
        if self.value is None:
            self.closes.append(bar.close)
            if len(self.closes) < 200:
                return None
            self.value = math.fsum(self.closes[-200:]) / 200
        else:
            self.value = (2 / 201) * bar.close + (199 / 201) * self.value
        return "BTC_ABOVE_OR_ON_EMA200" if bar.close >= self.value else "BTC_BELOW_EMA200"


def stationary_bootstrap(values: Sequence[float], *, seed: int, centered: bool,
                         resamples: int = 20_000) -> np.ndarray:
    if not values or resamples <= 0 or not all(math.isfinite(v) for v in values):
        raise C259Error("invalid bootstrap input")
    source = np.asarray(values, dtype=np.float64)
    observed = math.fsum(map(float, source)) / len(source)
    if centered:
        source = source - observed
    draws = np.random.Generator(np.random.PCG64(seed)).random(
        size=(resamples, len(source), 2), dtype=np.float64)
    output = np.empty(resamples)
    for b in range(resamples):
        index = math.floor(draws[b, 0, 1] * len(source))
        sample = [float(source[index])]
        for j in range(1, len(source)):
            restart, u_index = draws[b, j]
            index = (math.floor(u_index * len(source)) if restart < 1 / 21
                     else (index + 1) % len(source))
            sample.append(float(source[index]))
        output[b] = math.fsum(sample) / len(sample)
    return output


def bootstrap_stats(values: Sequence[float], *, side: int, resamples: int = 20_000) -> dict[str, float]:
    if side not in (-1, 1):
        raise C259Error("bootstrap side must be long or short")
    null_seed = 259202611071 if side == 1 else 259202611073
    ci_seed = 259202611072 if side == 1 else 259202611074
    mean = math.fsum(values) / len(values)
    null = stationary_bootstrap(values, seed=null_seed, centered=True, resamples=resamples)
    ci = stationary_bootstrap(values, seed=ci_seed, centered=False, resamples=resamples)
    return {"mean": mean, "p_value": (1 + int(np.count_nonzero(null >= mean))) / (resamples + 1),
            "ci_low": float(np.quantile(ci, 0.025, method="linear")),
            "ci_high": float(np.quantile(ci, 0.975, method="linear"))}


def metrics(trades: Sequence[ClosedTrade]) -> dict[str, object]:
    if not trades:
        raise C259Error("undefined empty metrics")
    for trade in trades:
        validate_closed_trade(trade)
    rs = [trade.trade_R for trade in trades]
    cash = [trade.net_cash for trade in trades]
    wins, losses = math.fsum(v for v in cash if v > 0), math.fsum(v for v in cash if v < 0)
    pf = math.nan if losses == 0 else wins / abs(losses)
    regimes: dict[str, list[float]] = {}
    symbols: dict[str, float] = {}
    for trade in trades:
        regimes.setdefault(trade.regime, []).append(trade.trade_R)
        symbols[trade.symbol] = symbols.get(trade.symbol, 0) + trade.trade_R
    denominator = math.fsum(abs(v) for v in symbols.values())
    concentration = math.inf if denominator == 0 else max(abs(v) for v in symbols.values()) / denominator
    return {"closed": len(trades), "mean_R": math.fsum(rs) / len(rs), "net_R": math.fsum(rs),
            "profit_factor": pf, "symbols": len(symbols), "incomplete": any(t.incomplete for t in trades),
            "regime": {k: {"count": len(v), "mean_R": math.fsum(v) / len(v)} for k, v in regimes.items()},
            "max_symbol_concentration": concentration}


def split_name(timestamp_ms: int) -> str:
    boundaries = (
        (1743465600000, 1761955200000, "wf1"),
        (1761955200000, 1775001600000, "wf2"),
        (1775001600000, 1790553600000, "wf3"),
        (1790553600000, 1793836800000, "sealed_holdout"),
    )
    for start, end, name in boundaries:
        if start <= timestamp_ms < end:
            return name
    if 1728864000000 <= timestamp_ms < 1743465600000:
        return "development"
    raise C259Error("timestamp outside frozen splits")


@dataclass(frozen=True)
class PositionMark:
    timestamp_ms: int
    symbol: str
    price: float
    source_digest: str
    identity_sha256: str


def make_position_mark(*, timestamp_ms: int, symbol: str, price: float,
                       source_digest: str) -> PositionMark:
    body = {"timestamp_ms": timestamp_ms, "symbol": symbol, "price": price,
            "source_digest": source_digest}
    return PositionMark(timestamp_ms, symbol, price, source_digest, _sha(_canon(body)))


def validate_position_mark(mark: PositionMark) -> None:
    if (not isinstance(mark.timestamp_ms, int) or mark.timestamp_ms % HOUR_MS
            or not mark.symbol or not math.isfinite(mark.price) or mark.price <= 0
            or len(mark.source_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in mark.source_digest)
            or mark != make_position_mark(timestamp_ms=mark.timestamp_ms, symbol=mark.symbol,
                                           price=mark.price,
                                           source_digest=mark.source_digest)):
        raise IncompleteAuthority("invalid canonical position-mark identity")


def derive_equity_points(trades: Sequence[ClosedTrade], marks: Sequence[PositionMark], *,
                         start_ms: int, end_ms: int,
                         capital: float = PRIMARY_CAPITAL) -> tuple[tuple[int, int, float], ...]:
    for trade in trades:
        validate_closed_trade(trade)
    required = {(timestamp, trade.symbol)
                for trade in trades
                for timestamp in range(trade.entry_ms, trade.close_ms, HOUR_MS)}
    supplied: dict[tuple[int, str], PositionMark] = {}
    for mark in marks:
        validate_position_mark(mark)
        key = (mark.timestamp_ms, mark.symbol)
        if (key in supplied or key not in required or not math.isfinite(mark.price)
                or mark.price <= 0 or mark.timestamp_ms % HOUR_MS):
            raise IncompleteAuthority("missing, extra, duplicate, or invalid lifecycle mark")
        supplied[key] = mark
    if set(supplied) != required:
        raise IncompleteAuthority("missing, extra, duplicate, or invalid lifecycle mark")
    deltas: dict[tuple[int, int], list[float]] = {}
    for trade in trades:
        for fill in trade.fills:
            if fill.phase == "ENTRY":
                value, phase = -(fill.fee_drag+fill.slippage_drag+fill.impact_drag), 4
            else:
                value = fill.movement_cash-fill.fee_drag-fill.slippage_drag-fill.impact_drag
                phase = (0 if fill.phase in {"STOP_RANGE", "TARGET_RANGE"}
                         else 3 if fill.phase == "PROTECTION_FAILURE_CLEANUP" else 2)
            deltas.setdefault((fill.timestamp_ms, phase), []).append(value)
        for event in trade.funding_events:
            cash = funding_cash(trade.side, trade.fills[0].quantity,
                                event.mark.price, event.event.rate)
            deltas.setdefault((event.event.timestamp_ms, 1), []).append(cash)
    cash = capital
    points: list[tuple[int, int, float]] = [(start_ms, 0, capital)]
    timestamps = sorted({timestamp for timestamp, _phase in deltas} | {timestamp for timestamp, _ in required})
    for timestamp in timestamps:
        for phase in range(5):
            values = deltas.get((timestamp, phase))
            if values:
                cash = math.fsum((cash, *values))
                points.append((timestamp, phase, cash))
        if any(key[0] == timestamp for key in required):
            unrealized = math.fsum(
                trade.side * trade.fills[0].quantity *
                (supplied[(timestamp, trade.symbol)].price - trade.fills[0].reference)
                for trade in trades if trade.entry_ms <= timestamp < trade.close_ms)
            points.append((timestamp, 5, cash+unrealized))
    if not points or points[-1][0] > end_ms:
        raise IncompleteEpisode("lifecycle equity escaped fold")
    terminal = capital + math.fsum(trade.net_cash for trade in trades)
    if not math.isclose(cash, terminal, abs_tol=1e-9):
        raise IncompleteEpisode("derived terminal cash does not reconcile")
    return tuple(points)


@dataclass(frozen=True)
class FoldLifecycle:
    name: str
    base: tuple[ClosedTrade, ...]
    stress: tuple[ClosedTrade, ...]
    marks: tuple[PositionMark, ...]


FOLD_RANGES = {
    "development": (1728864000000, 1743465600000),
    "wf1": (1743465600000, 1761955200000),
    "wf2": (1761955200000, 1775001600000),
    "wf3": (1775001600000, 1790553600000),
}


def _validate_lifecycle(fold: FoldLifecycle) -> None:
    if fold.name not in FOLD_RANGES or not fold.base or not fold.stress:
        raise C259Error("invalid or empty frozen fold lifecycle")
    start, end = FOLD_RANGES[fold.name]
    side = fold.base[0].side
    if side not in (-1, 1) or any(trade.side != side for trade in fold.base + fold.stress):
        raise C259Error("fold must contain one isolated frozen side")
    identities: set[str] = set()
    for trade in fold.base + fold.stress:
        validate_closed_trade(trade)
        if not (start <= trade.entry_ms < trade.close_ms <= end) or trade.incomplete:
            raise IncompleteEpisode("trade crosses fold boundary or is incomplete")
    for trade in fold.base:
        if trade.attempt_id in identities:
            raise IncompleteAuthority("duplicate fold attempt identity")
        identities.add(trade.attempt_id)
        if fold.name == "development" and trade.entry_ms < 1734134400000:
            raise IncompleteAuthority("pre-filter development trade is forbidden")
    lifecycle_marks = {(mark.timestamp_ms, mark.symbol): mark for mark in fold.marks}
    for trade in fold.base + fold.stress:
        for applied in trade.funding_events:
            if lifecycle_marks.get((applied.mark.timestamp_ms, applied.mark.symbol)) != applied.mark:
                raise IncompleteAuthority("funding mark conflicts with lifecycle mark authority")
    stress_same_selection(fold.base, fold.stress)
    base_points = derive_equity_points(fold.base, fold.marks, start_ms=start, end_ms=end)
    stress_points = derive_equity_points(fold.stress, fold.marks, start_ms=start, end_ms=end)
    marked_equity_drawdown_R(base_points, PRIMARY_CAPITAL)
    marked_equity_drawdown_R(stress_points, PRIMARY_CAPITAL)


def _month_keys(start_ms: int, end_ms: int) -> tuple[tuple[int, int], ...]:
    current = datetime.fromtimestamp(start_ms / 1000, timezone.utc)
    end = datetime.fromtimestamp((end_ms - 1) / 1000, timezone.utc)
    result: list[tuple[int, int]] = []
    while (current.year, current.month) <= (end.year, end.month):
        result.append((current.year, current.month))
        current = datetime(current.year + (current.month == 12),
                           1 if current.month == 12 else current.month + 1, 1,
                           tzinfo=timezone.utc)
    return tuple(result)


def _month_of(timestamp_ms: int) -> tuple[int, int]:
    value = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
    return value.year, value.month


def development_gate(fold: FoldLifecycle) -> dict[str, object]:
    """Derive the development veto from immutable lifecycle evidence only."""
    _validate_lifecycle(fold)
    base, stress = metrics(fold.base), metrics(fold.stress)
    start, end = FOLD_RANGES[fold.name]
    months = _month_keys(start, end)
    counts = {month: 0 for month in months}
    for trade in fold.base:
        counts[_month_of(trade.close_ms)] += 1
    marked = marked_equity_drawdown_R(
        derive_equity_points(fold.base, fold.marks, start_ms=start, end_ms=end), PRIMARY_CAPITAL)
    realized = closed_realized_drawdown_R(fold.base)
    gross_mean = math.fsum(
        (math.fsum(fill.movement_cash for fill in trade.fills) + trade.funding)
        / trade.initial_risk_cash for trade in fold.base) / len(fold.base)
    checks = {
        "gross": gross_mean > 0, "mean": float(base["mean_R"]) > 0,
        "pf": float(base["profit_factor"]) > 1.05, "sample": int(base["closed"]) >= 120,
        "symbols": int(base["symbols"]) >= 8,
        "monthly_frequency": float(np.median(tuple(counts.values()))) >= 8,
        "stress_mean": float(stress["mean_R"]) > 0,
        "drawdown": gate_drawdown_R(marked_drawdown_R=marked,
                                    realized_drawdown_R=realized) <= 60,
        "complete": not bool(base["incomplete"]) and not bool(stress["incomplete"]),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return {"pass": not failures, "failures": failures, "checks": checks,
            "base": base, "stress": stress}


def forward_gate(folds: Sequence[FoldLifecycle], *, bootstrap_resamples: int = 20_000) -> dict[str, object]:
    """Derive every forward gate from exact frozen lifecycle evidence."""
    if tuple(fold.name for fold in folds) != ("wf1", "wf2", "wf3"):
        raise C259Error("exact ordered wf1/wf2/wf3 lifecycles are required")
    for fold in folds:
        _validate_lifecycle(fold)
    trades = tuple(trade for fold in folds for trade in fold.base)
    stress_trades = tuple(trade for fold in folds for trade in fold.stress)
    side = trades[0].side
    base, stress = metrics(trades), metrics(stress_trades)
    fold_reports = tuple(metrics(fold.base) for fold in folds)
    months = {month: [] for fold in folds for month in _month_keys(*FOLD_RANGES[fold.name])}
    for trade in trades:
        months[_month_of(trade.close_ms)].append(trade.trade_R)
    month_net = tuple(math.fsum(values) for values in months.values())
    month_counts = tuple(len(values) for values in months.values())
    regimes = {"BTC_ABOVE_OR_ON_EMA200": [], "BTC_BELOW_EMA200": []}
    for trade in trades:
        if trade.regime not in regimes:
            raise C259Error("unfrozen regime label")
        regimes[trade.regime].append(trade.trade_R)
    marked = max(marked_equity_drawdown_R(derive_equity_points(
        fold.base, fold.marks, start_ms=FOLD_RANGES[fold.name][0],
        end_ms=FOLD_RANGES[fold.name][1]), PRIMARY_CAPITAL) for fold in folds)
    realized = max(closed_realized_drawdown_R(fold.base) for fold in folds)
    stress_marked = max(marked_equity_drawdown_R(derive_equity_points(
        fold.stress, fold.marks, start_ms=FOLD_RANGES[fold.name][0],
        end_ms=FOLD_RANGES[fold.name][1]), PRIMARY_CAPITAL) for fold in folds)
    stress_realized = max(closed_realized_drawdown_R(fold.stress) for fold in folds)
    gate_dd = gate_drawdown_R(marked_drawdown_R=marked, realized_drawdown_R=realized)
    stress_dd = gate_drawdown_R(marked_drawdown_R=stress_marked,
                                realized_drawdown_R=stress_realized)
    tier = drawdown_tier(float(base["net_R"]), gate_dd,
                         float(stress["net_R"]), stress_dd)
    bootstrap = bootstrap_stats([trade.trade_R for trade in sorted(
        trades, key=lambda item: (item.entry_ms, item.symbol))], side=side,
        resamples=bootstrap_resamples)
    checks = {
        "mean": float(base["mean_R"]) > 0.03,
        "pf": float(base["profit_factor"]) > 1.10,
        "sample": int(base["closed"]) >= 300, "symbols": int(base["symbols"]) >= 12,
        "monthly_frequency": float(np.median(month_counts)) >= 12,
        "folds": sum(float(report["net_R"]) > 0 for report in fold_reports) >= 2,
        "worst_fold": min(float(report["mean_R"]) for report in fold_reports) > -0.05,
        "months": sum(value > 0 for value in month_net) / len(month_net) > 0.55,
        "regime_sample": all(len(values) >= 30 for values in regimes.values()),
        "regime_mean": all(values and math.fsum(values) / len(values) > -0.03
                           for values in regimes.values()),
        "stress_mean": float(stress["mean_R"]) > 0,
        "stress_pf": float(stress["profit_factor"]) > 1.02,
        "multiplicity": bootstrap["p_value"] <= MULTIPLICITY_ALPHA,
        "concentration": float(base["max_symbol_concentration"]) <= 0.25,
        "drawdown": bool(tier["pass"]),
        "complete": not bool(base["incomplete"]) and not bool(stress["incomplete"]),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return {"pass": not failures, "failures": failures, "checks": checks,
            "base": base, "stress": stress, "bootstrap": bootstrap,
            "drawdown": tier, "month_net_R": month_net}


def stress_same_selection(base: Sequence[ClosedTrade], stress: Sequence[ClosedTrade]) -> None:
    def identity(trade: ClosedTrade) -> tuple[object, ...]:
        fills = tuple((fill.timestamp_ms, fill.phase, fill.symbol, fill.side, fill.quantity,
                       fill.reference, fill.movement_cash) for fill in trade.fills)
        funding = tuple(asdict(event).items() for event in trade.funding_events)
        return (trade.attempt_id, trade.side, trade.symbol, trade.entry_ms, trade.close_ms,
                trade.regime, fills, funding, trade.sizing, trade.decision_close_ms,
                trade.atr_bars, trade.lifecycle_bars, trade.entry_bar, trade.exit_bar,
                trade.stop, trade.target, trade.initial_risk_cash, trade.incomplete)
    left = [identity(trade) for trade in base]
    right = [identity(trade) for trade in stress]
    if left != right:
        raise C259Error("stress replay changed admitted trade set")
    for base_trade, stress_trade in zip(base, stress):
        validate_closed_trade(base_trade); validate_closed_trade(stress_trade)
        for base_fill, stress_fill in zip(base_trade.fills, stress_trade.fills):
            if (base_fill.slippage_bps != BASE_SLIPPAGE_BPS
                    or stress_fill.slippage_bps != STRESS_SLIPPAGE_BPS
                    or base_fill.impact_drag != stress_fill.impact_drag
                    or base_fill.volume_rows != stress_fill.volume_rows
                    or base_fill.fee_drag == stress_fill.fee_drag
                    or stress_fill.slippage_drag <= base_fill.slippage_drag):
                raise C259Error("stress costs are not exact frozen 10bp replay")


def drawdown_tier(net_R: float, gate_drawdown_R: float, stress_net_R: float,
                  stress_drawdown_R: float) -> dict[str, object]:
    if gate_drawdown_R <= 15:
        return {"tier": "standard", "pass": True, "paper_multiplier": 1.0}
    if gate_drawdown_R <= 30:
        return {"tier": "elevated", "pass": net_R / gate_drawdown_R >= 1.5,
                "paper_multiplier": 1.0}
    passed = (gate_drawdown_R <= 60 and net_R / gate_drawdown_R >= 2
              and stress_drawdown_R > 0 and stress_net_R / stress_drawdown_R >= 1)
    return {"tier": "high" if gate_drawdown_R <= 60 else "reject", "pass": passed,
            "paper_multiplier": min(0.5, 15 / gate_drawdown_R)}


def immutable_result_identity(*, pins: AuthorityPins, source_digest: str,
                              result: Mapping[str, object]) -> dict[str, object]:
    if len(source_digest) != 64:
        raise C259Error("invalid source digest")
    body = {"format": "zb-c259-stagea-result/1", "candidate_id": 259,
            "contract_head": CONTRACT_HEAD, "contract_sha256": CONTRACT_SHA256,
            "authority_plan_sha256": PLAN_SHA256, "authority_pins": asdict(pins),
            "source_digest": source_digest, "default_off": True, "paper_testnet_only": True,
            "sealed_holdout_opened": False, "result": dict(result)}
    return {**body, "output_identity_sha256": _sha(_canon(body))}
