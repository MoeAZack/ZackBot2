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
    if cutoff != "2026-11-05T00:00:00Z" and cutoff_ms != 1793836800000:
        raise IncompleteAuthority("manifest cutoff mismatch")
    if pit.get("format") != "zb-pit-universe/2" or pit.get("manifest_digest") != _declared_digest(manifest):
        raise IncompleteAuthority("PIT-v9 manifest binding mismatch")
    if (filters.get("authority"), filters.get("version")) != (
        "STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY", 4
    ):
        raise IncompleteAuthority("filter-v4 identity mismatch")
    if (filters.get("pit_universe_sha256") != hashes["pit_v9"]
            or filters.get("pit_universe_digest") != _declared_digest(pit)):
        raise IncompleteAuthority("filter-v4 PIT binding mismatch")
    gaps = coverage.get("guaranteed_period_gaps")
    result = coverage.get("required_result", coverage.get("result"))
    if gaps != 0 or coverage.get("gaps") not in ([], None) or result != "ZERO_GUARANTEED_PERIOD_GAPS":
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
    found = sorted((bar for bar in bars if bar.open_ms > decision_close_ms), key=lambda b: b.open_ms)
    if not found or found[0].open_ms != decision_close_ms + HOUR_MS:
        raise IncompleteAuthority("missing exact next canonical open")
    return found[0]


@dataclass(frozen=True)
class SignalFit:
    beta0: float
    gamma: float
    se_gamma: float
    t_gamma: float
    residual_d: float
    forecast: float
    impulse_threshold: float
    side: int | None
    qualifies: bool


def fit_signal(prior_btc: Sequence[float], prior_alt: Sequence[float], *,
               btc_d: float, alt_d: float) -> SignalFit:
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
    return SignalFit(beta, gamma, se, t, residual_d, forecast, threshold, side, qualifies)


def select_candidates(rows: Iterable[tuple[str, SignalFit]], side: int,
                      occupied: Iterable[str] = ()) -> tuple[str, ...]:
    occupied_set = set(occupied)
    eligible = [(symbol, fit) for symbol, fit in rows
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
    if stop <= 0 or target <= 0 or distance <= 0:
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
class SizedPosition:
    quantity: Decimal
    initial_risk_cash: Decimal
    requested_risk_cash: Decimal
    raw_notional: Decimal


def size_position(*, side: int, entry: Decimal, stop: Decimal, filters: Filters,
                  side_book_equity: Decimal, quote_volume_24h: float) -> SizedPosition | None:
    if side not in (-1, 1) or entry <= 0 or stop <= 0 or side_book_equity <= 0 \
            or filters.step <= 0 or filters.tick <= 0:
        raise C259Error("invalid sizing authority")
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
            return SizedPosition(quantity, risk, requested, notional)
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


def make_fill(*, timestamp_ms: int, phase: str, symbol: str, side: int, quantity: float,
              reference: float, movement_from: float | None, quote_volume_24h: float,
              slippage_bps: float = BASE_SLIPPAGE_BPS) -> Fill:
    if side not in (-1, 1) or quantity <= 0 or reference <= 0:
        raise C259Error("invalid fill")
    execution = reference * (1 + side * slippage_bps / 10_000)
    fee = abs(quantity * execution) * FEE_BPS / 10_000
    slip = abs(quantity) * abs(execution - reference)
    impact = float(impact_drag(Decimal(str(quantity)), Decimal(str(reference)), quote_volume_24h))
    movement = 0.0 if movement_from is None else -side * quantity * (reference - movement_from)
    return Fill(timestamp_ms, phase, symbol, side, quantity, reference, movement, fee, slip, impact)


def funding_cash(position_side: int, quantity: float, mark: float, rate: float) -> float:
    if (position_side not in (-1, 1) or quantity <= 0 or mark <= 0
            or not all(math.isfinite(v) for v in (quantity, mark, rate))):
        raise IncompleteAuthority("invalid funding authority")
    return -position_side * abs(quantity) * mark * rate


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
    funding: float
    initial_risk_cash: float
    incomplete: bool = False

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
                       cleanup_ms: int, fills: Sequence[Fill], funding: float,
                       initial_risk_cash: float, regime: str) -> ClosedTrade:
    if cleanup_ms <= entry_ms or not fills:
        raise IncompleteEpisode("protection cleanup must follow accepted entry")
    return ClosedTrade(attempt_id, side, symbol, entry_ms, cleanup_ms, regime,
                       tuple(fills), funding, initial_risk_cash, True)


def marked_equity_drawdown_R(equity_points: Sequence[tuple[int, int, float]],
                             starting_capital: float) -> float:
    """Drawdown over exact (timestamp, frozen-phase-order, total equity) points."""
    if starting_capital <= 0 or not equity_points:
        raise C259Error("undefined marked-equity drawdown")
    peak, maximum = starting_capital, 0.0
    previous: tuple[int, int] | None = None
    for timestamp, phase_order, equity in sorted(equity_points, key=lambda row: (row[0], row[1])):
        if not math.isfinite(equity) or (previous is not None and (timestamp, phase_order) == previous):
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


def forward_gate(evidence: Mapping[str, object]) -> tuple[bool, tuple[str, ...]]:
    """Apply the frozen aggregate forward gates to already computed exact evidence."""
    failures: list[str] = []
    checks = (
        (float(evidence.get("mean_R", -math.inf)) > 0.03, "mean"),
        (float(evidence.get("profit_factor", -math.inf)) > 1.10, "pf"),
        (int(evidence.get("closed", 0)) >= 300, "sample"),
        (int(evidence.get("symbols", 0)) >= 12, "symbols"),
        (float(evidence.get("median_monthly_closes", 0)) >= 12, "monthly_frequency"),
        (int(evidence.get("positive_folds", 0)) >= 2, "folds"),
        (float(evidence.get("worst_fold_mean_R", -math.inf)) > -0.05, "worst_fold"),
        (float(evidence.get("positive_month_fraction", 0)) > 0.55, "months"),
        (int(evidence.get("min_regime_closes", 0)) >= 30, "regime_sample"),
        (float(evidence.get("worst_regime_mean_R", -math.inf)) > -0.03, "regime_mean"),
        (float(evidence.get("stress_mean_R", -math.inf)) > 0, "stress_mean"),
        (float(evidence.get("stress_profit_factor", -math.inf)) > 1.02, "stress_pf"),
        (float(evidence.get("p_value", math.inf)) <= MULTIPLICITY_ALPHA, "multiplicity"),
        (float(evidence.get("max_symbol_concentration", math.inf)) <= 0.25, "concentration"),
        (bool(evidence.get("drawdown_pass", False)), "drawdown"),
        (not bool(evidence.get("incomplete", True)), "incomplete"),
    )
    failures.extend(name for passed, name in checks if not passed)
    return not failures, tuple(failures)


def stress_same_selection(base: Sequence[ClosedTrade], stress: Sequence[ClosedTrade]) -> None:
    left = [(t.attempt_id, t.side, t.symbol, t.entry_ms, t.close_ms) for t in base]
    right = [(t.attempt_id, t.side, t.symbol, t.entry_ms, t.close_ms) for t in stress]
    if left != right:
        raise C259Error("stress replay changed admitted trade set")


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
