"""C255 Stage-A causal primitives.

This module is intentionally not an evaluator.  It validates the frozen C255
contract and provides deterministic, synthetic-testable mechanics.  Real data
access remains locked until a later protected commit supplies an explicit
evaluation authority and independently reviewed runner binding.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "research_evidence/prereg/cointegrated_residual_reversion_c255_stagea.v1.json"
CONTRACT_SHA256 = "c6018851ee4e6ca76e69e91307eaa3f89fe3e72a8fb210694ade59f450387a36"
HOUR_MS = 3_600_000
TRAINING_HOURS = 180 * 24
HALF_HOURS = 90 * 24
MIN_COMPLETE_FRACTION = 0.995
FEE_BPS = 5.0
ADVERSE_SLIPPAGE_BPS = 2.0
STRESS_ROUND_TRIP_FRACTION = 0.003


class C255Error(RuntimeError):
    pass


class EvaluationLocked(C255Error):
    pass


class IncompleteAuthority(C255Error):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_contract() -> dict:
    """Load and byte-bind the reviewed contract and its non-outcome authorities."""
    if _sha256(CONTRACT_PATH) != CONTRACT_SHA256:
        raise IncompleteAuthority("C255 contract bytes do not match reviewed head 0e398490 protected-base refresh")
    doc = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (doc.get("candidate_id"), doc.get("default_off"), doc.get("status")) != (
        255, True, "FROZEN_NOT_EVALUATED_RESEARCH_ONLY"
    ):
        raise IncompleteAuthority("C255 is not the frozen default-OFF research contract")
    authority = doc["data_authority"]
    for path_key, sha_key in (
        ("universe_path", "universe_sha256"),
        ("archive_manifest_path", "archive_manifest_sha256"),
        ("exchange_filter_authority_path", "exchange_filter_authority_sha256"),
    ):
        path = ROOT / authority[path_key]
        if not path.is_file() or _sha256(path) != authority[sha_key]:
            raise IncompleteAuthority(f"C255 authority mismatch: {path_key}")
    universe = json.loads((ROOT / authority["universe_path"]).read_text(encoding="utf-8"))
    if universe.get("digest") != authority["universe_digest"]:
        raise IncompleteAuthority("C255 PIT universe digest mismatch")
    return doc


def assert_real_evaluation_authorized(*, gate: Mapping[str, object] | None = None) -> None:
    """Always fail on this prestage head; accepting a caller boolean is forbidden."""
    load_frozen_contract()
    raise EvaluationLocked(
        "C255_REAL_EVALUATION_LOCKED: protected authority/evaluation binding is absent; "
        "no market, outcome, forward-fold, or sealed-holdout data may be opened"
    )


def latest_membership_cell(cells: Sequence[Mapping[str, object]], selection_ms: int) -> Mapping[str, object]:
    eligible = [cell for cell in cells if isinstance(cell.get("monday_ms"), int) and cell["monday_ms"] <= selection_ms]
    if not eligible:
        raise IncompleteAuthority("no PIT membership cell at or before selection_ms")
    latest = max(eligible, key=lambda cell: int(cell["monday_ms"]))
    if sum(int(cell["monday_ms"]) == int(latest["monday_ms"]) for cell in cells) != 1:
        raise IncompleteAuthority("duplicate latest monday_ms membership cell")
    return latest


@dataclass(frozen=True)
class Bar:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float = 0.0

    def validate(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(v) and v > 0 for v in values):
            raise IncompleteAuthority("non-positive or non-finite OHLC")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise IncompleteAuthority("inconsistent OHLC")


def exact_bar_intersection(
    x_bars: Sequence[Bar], y_bars: Sequence[Bar], *, start_ms: int, end_ms: int
) -> tuple[tuple[Bar, Bar], ...]:
    """Inner join exact timestamps only; duplicates and off-grid bars fail closed."""
    def index(bars: Sequence[Bar]) -> dict[int, Bar]:
        out: dict[int, Bar] = {}
        for bar in bars:
            bar.validate()
            if bar.open_ms % HOUR_MS:
                raise IncompleteAuthority("non-hourly bar timestamp")
            if bar.open_ms in out:
                raise IncompleteAuthority("duplicate hourly bar timestamp")
            if start_ms <= bar.open_ms < end_ms:
                out[bar.open_ms] = bar
        return out

    x, y = index(x_bars), index(y_bars)
    return tuple((x[t], y[t]) for t in sorted(x.keys() & y.keys()))


@dataclass(frozen=True)
class TrainingSample:
    selection_ms: int
    aligned: tuple[tuple[Bar, Bar], ...]

    @property
    def x_close(self) -> tuple[float, ...]:
        return tuple(x.close for x, _ in self.aligned)

    @property
    def y_close(self) -> tuple[float, ...]:
        return tuple(y.close for _, y in self.aligned)

    def halves(self) -> tuple[tuple[tuple[Bar, Bar], ...], tuple[tuple[Bar, Bar], ...]]:
        boundary = self.selection_ms - HALF_HOURS * HOUR_MS
        return (
            tuple(row for row in self.aligned if row[0].open_ms < boundary),
            tuple(row for row in self.aligned if row[0].open_ms >= boundary),
        )


def build_training_sample(x_bars: Sequence[Bar], y_bars: Sequence[Bar], *, selection_ms: int) -> TrainingSample:
    if not isinstance(selection_ms, int) or selection_ms < 0 or selection_ms % HOUR_MS:
        raise IncompleteAuthority("selection_ms must be a non-negative UTC hourly timestamp")
    selection = datetime.fromtimestamp(selection_ms / 1000, tz=timezone.utc)
    if not (selection.day == 1 and selection.hour == 0 and selection.minute == 0
            and selection.second == 0 and selection.microsecond == 0):
        raise IncompleteAuthority("selection_ms must be exactly UTC month-start 00:00")
    start_ms = selection_ms - TRAINING_HOURS * HOUR_MS
    aligned = exact_bar_intersection(x_bars, y_bars, start_ms=start_ms, end_ms=selection_ms)
    minimum = math.ceil(TRAINING_HOURS * MIN_COMPLETE_FRACTION)
    if len(aligned) < minimum:
        raise IncompleteAuthority(
            f"pair training completeness {len(aligned)}/{TRAINING_HOURS} is below {MIN_COMPLETE_FRACTION}"
        )
    return TrainingSample(selection_ms, aligned)


def deterministic_pairs(symbols: Iterable[str]) -> tuple[tuple[str, str], ...]:
    ordered = sorted(set(symbols))
    return tuple((ordered[i], ordered[j]) for i in range(len(ordered)) for j in range(i + 1, len(ordered)))


@dataclass(frozen=True)
class ADFSettings:
    regression: str = "n"
    maxlag: int = 24
    autolag: str = "BIC"

    def kwargs(self) -> dict[str, object]:
        if (self.regression, self.maxlag, self.autolag) != ("n", 24, "BIC"):
            raise C255Error("C255 ADF settings are frozen")
        return {"regression": self.regression, "maxlag": self.maxlag, "autolag": self.autolag}


ADFFunction = Callable[..., Sequence[object]]


def adf_pvalue(residual: Sequence[float], adf: ADFFunction, settings: ADFSettings = ADFSettings()) -> float:
    result = adf(np.asarray(residual, dtype=float), **settings.kwargs())
    if len(result) < 2:
        raise C255Error("ADF result has no p-value")
    p = float(result[1])
    if not math.isfinite(p) or not 0 <= p <= 1:
        raise C255Error("ADF returned an invalid p-value")
    return p


@dataclass(frozen=True)
class OLSFit:
    intercept: float
    beta: float
    residual: np.ndarray = field(repr=False, compare=False)


def fit_log_ols(x_close: Sequence[float], y_close: Sequence[float]) -> OLSFit:
    x, y = np.asarray(x_close, dtype=float), np.asarray(y_close, dtype=float)
    if x.shape != y.shape or x.ndim != 1 or x.size < 3:
        raise C255Error("OLS inputs must be equal one-dimensional samples")
    if np.any(~np.isfinite(x)) or np.any(~np.isfinite(y)) or np.any(x <= 0) or np.any(y <= 0):
        raise C255Error("OLS prices must be finite and positive")
    lx, ly = np.log(x), np.log(y)
    design = np.column_stack((np.ones(lx.size), lx))
    coefficients, _, rank, _ = np.linalg.lstsq(design, ly, rcond=None)
    if rank != 2:
        raise C255Error("singular pair regression")
    intercept, beta = map(float, coefficients)
    residual = ly - (intercept + beta * lx)
    if not all(map(math.isfinite, (intercept, beta))) or np.any(~np.isfinite(residual)):
        raise C255Error("non-finite pair regression")
    return OLSFit(intercept, beta, residual)


def residual_half_life(residual: Sequence[float]) -> tuple[float, float]:
    values = np.asarray(residual, dtype=float)
    if values.ndim != 1 or values.size < 3 or np.any(~np.isfinite(values)):
        raise C255Error("invalid half-life sample")
    lag, current = values[:-1], values[1:]
    design = np.column_stack((np.ones(lag.size), lag))
    coefficients, _, rank, _ = np.linalg.lstsq(design, current, rcond=None)
    if rank != 2:
        raise C255Error("singular half-life regression")
    phi = float(coefficients[1])
    if not 0 < phi < 1:
        raise C255Error("residual phi is not mean reverting")
    half_life = -math.log(2) / math.log(phi)
    if not math.isfinite(half_life):
        raise C255Error("non-finite half-life")
    return phi, half_life


def beta_drift(beta_full: float, beta_first: float, beta_second: float) -> float:
    values = (beta_full, beta_first, beta_second)
    if not all(math.isfinite(v) for v in values):
        raise C255Error("non-finite beta")
    return abs(beta_first - beta_second) / max(abs(beta_full), 1e-12)


def holm_adjust(pvalues: Mapping[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    if not pvalues:
        return {}
    ranked = sorted(pvalues.items(), key=lambda item: (item[1], item[0]))
    m, running, adjusted = len(ranked), 0.0, {}
    for rank, (key, raw) in enumerate(ranked):
        if not math.isfinite(raw) or not 0 <= raw <= 1:
            raise C255Error("invalid Holm p-value")
        running = max(running, min(1.0, (m - rank) * raw))
        adjusted[key] = running
    return adjusted


@dataclass(frozen=True)
class PairModel:
    x: str
    y: str
    intercept: float
    beta: float
    residual_mean: float
    residual_std: float
    raw_adf_p: float
    corrected_adf_p: float
    half_life_hours: float
    drift: float


def select_monthly_pairs(
    samples: Mapping[tuple[str, str], TrainingSample],
    adf: ADFFunction,
) -> tuple[PairModel, ...]:
    """Apply the frozen training-only screen and greedy shared-symbol cap."""
    # Holm's denominator is every data/beta-eligible pair.  Stability is a
    # later screen and must never shrink the multiple-testing family.
    provisional: dict[tuple[str, str], dict[str, float | bool]] = {}
    for pair, sample in sorted(samples.items()):
        if pair[0] >= pair[1]:
            raise C255Error("pair orientation must be lexicographic x/y")
        x, y = sample.x_close, sample.y_close
        first_rows, second_rows = sample.halves()
        minimum_half = math.ceil(HALF_HOURS * MIN_COMPLETE_FRACTION)
        if len(first_rows) < minimum_half or len(second_rows) < minimum_half:
            continue
        full = fit_log_ols(x, y)
        first = fit_log_ols([row[0].close for row in first_rows], [row[1].close for row in first_rows])
        second = fit_log_ols([row[0].close for row in second_rows], [row[1].close for row in second_rows])
        # Every data-eligible pair enters the monthly Holm family.  All three
        # frozen ADF calls therefore happen before beta or stability rejection.
        raw = adf_pvalue(full.residual, adf)
        first_p = adf_pvalue(first.residual, adf)
        second_p = adf_pvalue(second.residual, adf)
        drift = beta_drift(full.beta, first.beta, second.beta)
        std = float(np.std(full.residual, ddof=1))
        try:
            _, half_life = residual_half_life(full.residual)
        except C255Error:
            half_life = math.inf
        provisional[pair] = {
            "intercept": full.intercept, "beta": full.beta,
            "mean": float(np.mean(full.residual)), "std": std,
            "raw": raw, "half_life": half_life, "drift": drift,
            "stable": bool(0.2 < full.beta < 5.0 and first.beta > 0 and second.beta > 0
                           and drift <= 0.25 and 6 <= half_life <= 72
                           and first_p <= 0.05 and second_p <= 0.05 and std > 0),
        }
    corrected = holm_adjust({pair: float(row["raw"]) for pair, row in provisional.items()})
    eligible = [
        PairModel(pair[0], pair[1], float(row["intercept"]), float(row["beta"]),
                  float(row["mean"]), float(row["std"]), float(row["raw"]),
                  corrected[pair], float(row["half_life"]), float(row["drift"]))
        for pair, row in provisional.items() if bool(row["stable"]) and corrected[pair] <= 0.05
    ]
    eligible.sort(key=lambda model: (model.corrected_adf_p, model.half_life_hours, model.y, model.x))
    selected, used = [], set()
    for model in eligible:
        if model.x in used or model.y in used:
            continue
        selected.append(model)
        used.update((model.x, model.y))
        if len(selected) == 6:
            break
    return tuple(selected)


def z_score(model: PairModel, x_close: float, y_close: float) -> float:
    if min(x_close, y_close, model.residual_std) <= 0:
        raise C255Error("invalid z-score inputs")
    residual = math.log(y_close) - model.beta * math.log(x_close) - model.intercept
    value = (residual - model.residual_mean) / model.residual_std
    if not math.isfinite(value):
        raise C255Error("non-finite z-score")
    return value


def entry_signal(z: float, *, displacement_log_units: float) -> str | None:
    if not math.isfinite(z) or not math.isfinite(displacement_log_units):
        raise C255Error("invalid entry signal")
    if abs(z) >= 4.5 or abs(displacement_log_units) < 0.007:
        return None
    if z <= -2.5:
        return "long_spread"
    if z >= 2.5:
        return "short_spread"
    return None


def next_open_fill(bars: Sequence[Bar], decision_open_ms: int) -> Bar:
    expected = decision_open_ms + HOUR_MS
    matches = [bar for bar in bars if bar.open_ms == expected]
    if len(matches) != 1:
        raise IncompleteAuthority("missing or duplicate exact next 1h open")
    matches[0].validate()
    return matches[0]


def catastrophic_stop_fill(*, side: str, entry_price: float, frozen_atr24: float,
                           bar: Bar, adverse_slippage_bps: float = ADVERSE_SLIPPAGE_BPS) -> float | None:
    """Resolve a leg stop pessimistically; return None when it was not touched."""
    bar.validate()
    if adverse_slippage_bps != ADVERSE_SLIPPAGE_BPS:
        raise C255Error("C255 adverse slippage is frozen at 2 bps")
    if side not in {"long", "short"} or entry_price <= 0 or frozen_atr24 <= 0:
        raise C255Error("invalid catastrophic-stop inputs")
    if side == "long":
        stop = entry_price - 3 * frozen_atr24
        if stop <= 0:
            raise C255Error("non-positive long stop")
        if bar.low > stop:
            return None
        reference = min(stop, bar.open)  # gap down may only worsen the fill
        return reference * (1 - adverse_slippage_bps / 10_000)
    stop = entry_price + 3 * frozen_atr24
    if bar.high < stop:
        return None
    reference = max(stop, bar.open)  # gap up may only worsen the fill
    return reference * (1 + adverse_slippage_bps / 10_000)


def atr24_wilder(bars: Sequence[Bar]) -> float:
    if len(bars) < 25:
        raise IncompleteAuthority("ATR24 requires 24 true ranges plus previous close")
    for bar in bars:
        bar.validate()
    if any(bar.open_ms % HOUR_MS for bar in bars):
        raise IncompleteAuthority("ATR24 bars must use exact UTC hourly timestamps")
    if any(right.open_ms - left.open_ms != HOUR_MS for left, right in zip(bars, bars[1:])):
        raise IncompleteAuthority("ATR24 bars must be unique, sorted, and exactly hourly contiguous")
    tr = [
        max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close))
        for previous, bar in zip(bars, bars[1:])
    ]
    atr = sum(tr[:24]) / 24
    for value in tr[24:]:
        atr = ((23 * atr) + value) / 24
    if not math.isfinite(atr) or atr <= 0:
        raise C255Error("invalid ATR24")
    return atr


@dataclass(frozen=True)
class Fill:
    symbol: str
    notional_dollars: float
    phase: str
    price_move_pnl: float
    funding_pnl: float


def _validate_fills(fills: Sequence[Fill]) -> None:
    for fill in fills:
        if fill.phase not in {"entry", "exit", "flatten"}:
            raise C255Error("fill phase must be entry, exit, or flatten")
        if (not fill.symbol or not math.isfinite(fill.notional_dollars) or fill.notional_dollars <= 0
                or not math.isfinite(fill.price_move_pnl) or not math.isfinite(fill.funding_pnl)):
            raise C255Error("every realized fill requires finite notional, price P&L, and funding P&L")


def actual_fill_cost(fills: Sequence[Fill], *, fee_bps: float = FEE_BPS,
                     slippage_bps: float = ADVERSE_SLIPPAGE_BPS) -> float:
    if fee_bps != FEE_BPS or slippage_bps != ADVERSE_SLIPPAGE_BPS:
        raise C255Error("C255 fill costs are frozen at 5 fee bps + 2 adverse slippage bps")
    if len(fills) == 0:
        raise C255Error("actual-fill notionals must be finite and positive")
    _validate_fills(fills)
    return sum(abs(fill.notional_dollars) * (fee_bps + slippage_bps) / 10_000 for fill in fills)


@dataclass(frozen=True)
class ClosedPairAttempt:
    pair_id: str
    net_pnl: float
    costs: float
    funding_pnl: float
    atomicity_failure: bool
    closed_attempts: int = 1


def account_atomic_attempt(pair_id: str, fills: Sequence[Fill], *,
                           expected_symbols: tuple[str, str]) -> ClosedPairAttempt:
    """Charge every realized fill even when one leg or its flatten failed atomically."""
    if (not pair_id or len(expected_symbols) != 2 or expected_symbols[0] == expected_symbols[1]
            or not all(expected_symbols)):
        raise C255Error("atomic pair requires two distinct expected legs")
    if not fills:
        raise C255Error("an attempted pair must retain its realized fills")
    _validate_fills(fills)
    expected = set(expected_symbols)
    if any(fill.symbol not in expected for fill in fills):
        raise C255Error("fill symbol is outside the attempted pair")
    entries = [fill for fill in fills if fill.phase == "entry"]
    closes = [fill for fill in fills if fill.phase in {"exit", "flatten"}]
    entry_symbols = [fill.symbol for fill in entries]
    if not entries or len(entry_symbols) != len(set(entry_symbols)):
        raise C255Error("each realized leg must have exactly one entry fill")
    close_symbols = [fill.symbol for fill in closes]
    if sorted(close_symbols) != sorted(entry_symbols):
        raise IncompleteAuthority("every realized entry leg requires exactly one exit or bounded flatten fill")
    both_legs_placed = set(entry_symbols) == expected and len(entries) == 2
    costs = actual_fill_cost(fills)
    funding = sum(fill.funding_pnl for fill in fills)
    net = sum(fill.price_move_pnl + fill.funding_pnl for fill in fills) - costs
    return ClosedPairAttempt(pair_id, net, costs, funding, not both_legs_placed)


def modeled_loss_fraction(*, beta: float, z_entry: float, residual_std: float,
                          y_atr_fraction: float, x_atr_fraction: float,
                          y_other_leg_flatten_fraction: float,
                          x_other_leg_flatten_fraction: float,
                          stress_round_trip_fraction: float = STRESS_ROUND_TRIP_FRACTION) -> float:
    if stress_round_trip_fraction != STRESS_ROUND_TRIP_FRACTION:
        raise C255Error("C255 stressed round-trip cost is frozen at 30 bps")
    if (beta <= 0 or residual_std <= 0 or abs(z_entry) >= 4.5
            or min(y_atr_fraction, x_atr_fraction,
                   y_other_leg_flatten_fraction, x_other_leg_flatten_fraction) < 0):
        raise C255Error("invalid C255 risk inputs")
    wy, wx = 1 / (1 + abs(beta)), abs(beta) / (1 + abs(beta))
    delta_r = (4.5 - abs(z_entry)) * residual_std
    residual_loss = max(wy * math.expm1(delta_r), wx * math.expm1(delta_r / abs(beta)))
    y_stop_loss = wy * 3 * y_atr_fraction + wx * x_other_leg_flatten_fraction
    x_stop_loss = wx * 3 * x_atr_fraction + wy * y_other_leg_flatten_fraction
    atr_loss = max(y_stop_loss, x_stop_loss)
    result = max(residual_loss, atr_loss) + stress_round_trip_fraction
    if not math.isfinite(result) or result <= 0:
        raise C255Error("non-positive or non-finite modeled loss")
    return result


@dataclass
class PortfolioState:
    equity: float
    open_pair_gross: float = 0.0
    open_risk_r: float = 0.0
    open_pairs: int = 0
    symbols: set[str] = field(default_factory=set)

    def admit(self, *, x: str, y: str, proposed_gross: float, proposed_risk_r: float) -> bool:
        if not all(math.isfinite(v) and v > 0 for v in (self.equity, proposed_gross, proposed_risk_r)):
            return False
        if x == y or x in self.symbols or y in self.symbols or self.open_pairs >= 4:
            return False
        pair_cap = 0.5 * self.equity
        aggregate_cap = 1.5 * self.equity
        margin_cap = 0.5 * self.equity * 3
        return (
            proposed_gross <= pair_cap
            and self.open_pair_gross + proposed_gross <= min(aggregate_cap, margin_cap)
            and self.open_risk_r + proposed_risk_r <= 4.0
        )

    def commit(self, *, x: str, y: str, gross: float, risk_r: float) -> None:
        if not self.admit(x=x, y=y, proposed_gross=gross, proposed_risk_r=risk_r):
            raise C255Error("portfolio caps refuse pair")
        self.open_pair_gross += gross
        self.open_risk_r += risk_r
        self.open_pairs += 1
        self.symbols.update((x, y))
