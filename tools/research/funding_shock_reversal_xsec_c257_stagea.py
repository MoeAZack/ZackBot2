"""C257 Stage-A deterministic primitives with real evaluation locked.

This module deliberately has no archive, market-data, identity, or outcome
reader.  It binds the frozen preregistration and exposes only pure mechanics
that can be exercised with synthetic inputs.  A later, independently reviewed
protected head must supply any real evaluation authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SUCCESSOR_PATH = ROOT / "research_evidence/prereg/funding_shock_reversal_xsec_c257_successor.v1.json"
PREP_PATH = ROOT / "contracts/strategy_causal_pack_01/c257_successor_adapter_prep.v1.json"
CONTRACT_SHA256 = "88294e16a44c4e2219284abeb26f4085123b9fd9de0c5120d0a19d5b84a94b54"
CONTRACT_BASE = "a02c33f3cc343ee2af09bffcf35adb5a26fc0fd8"
CANDIDATE_ID = 257
HOUR_MS = 3_600_000
FUNDING_DELAY_MS = 300_000
ALLOWED_INTERVAL_HOURS = frozenset({1, 2, 4, 8})
FEE_BPS = 5.0
BASE_SLIPPAGE_BPS = 2.0
STRESS_SLIPPAGE_BPS = 10.0
IMPACT_CAP = 0.001
FAMILY_ALPHA = 0.05 / 256
FROZEN_RISK_FRACTION = 0.01
PRE_FILTER_AUTHORITY_INELIGIBLE = "PRE_FILTER_AUTHORITY_INELIGIBLE"


class C257Error(RuntimeError):
    pass


C256Error = C257Error  # compatibility name for inherited pure helper signatures


class IncompleteAuthority(C256Error):
    pass


class EvaluationLocked(C256Error):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_contract() -> dict:
    """Bind the exact successor/prep and resolve inherited C256 bytes from Git."""
    try:
        from tools.research import c257_successor_guard as guard
        successor = guard._strict_json(SUCCESSOR_PATH.read_bytes(), "C257 successor")
        prep = guard._strict_json(PREP_PATH.read_bytes(), "C257 prep")
        guard.validate_scaffold(successor, prep, repo=ROOT)
        return guard._resolve_c256(successor, ROOT)
    except Exception as exc:
        if isinstance(exc, IncompleteAuthority):
            raise
        raise IncompleteAuthority("C257 exact C256 economic inheritance is unavailable") from exc


def assert_real_evaluation_authorized(*, gate: Mapping[str, object] | None = None) -> None:
    """No caller-supplied flag can unlock this prestage head."""
    load_frozen_contract()
    raise EvaluationLocked(
        "C257_REAL_EVALUATION_LOCKED: no protected authority-v6 evaluation capability is bound; "
        "market, outcome, identity, forward-fold and sealed-holdout access are forbidden"
    )


@dataclass(frozen=True)
class FundingRow:
    symbol: str
    nominal_ms: int
    available_ms: int
    rate: float
    interval_hours: int

    def validate(self) -> None:
        if not self.symbol or not isinstance(self.nominal_ms, int) or not isinstance(self.available_ms, int):
            raise IncompleteAuthority("invalid funding row identity or timestamp")
        if self.nominal_ms < 0 or self.nominal_ms % HOUR_MS or self.available_ms < self.nominal_ms:
            raise IncompleteAuthority("funding row is off-grid or available before settlement")
        if not math.isfinite(self.rate):
            raise IncompleteAuthority("non-finite funding rate")
        if type(self.interval_hours) is not int or self.interval_hours not in ALLOWED_INTERVAL_HOURS:
            raise IncompleteAuthority("funding interval authority is not one of 1/2/4/8 hours")


@dataclass(frozen=True)
class FundingShock:
    symbol: str
    nominal_ms: int
    r8: float
    median: float
    mad: float
    z: float


def _interval_hours(previous: FundingRow, current: FundingRow) -> int:
    delta = current.nominal_ms - previous.nominal_ms
    if delta <= 0 or delta % HOUR_MS:
        raise IncompleteAuthority("funding cadence is duplicate, reversed, or off-grid")
    hours = delta // HOUR_MS
    if hours not in ALLOWED_INTERVAL_HOURS:
        raise IncompleteAuthority("funding cadence is not one of 1/2/4/8 hours")
    if current.interval_hours != hours:
        raise IncompleteAuthority("funding gap does not match verified interval_hours authority")
    return int(hours)


def robust_funding_surprise(
    current: FundingRow,
    prior_rows: Sequence[FundingRow],
    *,
    decision_ms: int,
) -> FundingShock:
    """Compute the frozen current r8 surprise from exactly 30 strictly prior rows."""
    current.validate()
    if decision_ms != current.nominal_ms + FUNDING_DELAY_MS or current.available_ms > decision_ms:
        raise IncompleteAuthority("current funding is not available at the frozen T+300 decision")
    rows = sorted(prior_rows, key=lambda row: row.nominal_ms)
    # Thirty normalized observations require thirty-one settlement rows: the
    # extra oldest row establishes the first observation's actual cadence.
    if len(rows) != 31:
        raise IncompleteAuthority("C256 requires 31 rows to establish exactly 30 prior normalized settlements")
    if len({row.nominal_ms for row in rows}) != 31:
        raise IncompleteAuthority("duplicate prior funding settlement")
    if any(row.symbol != current.symbol for row in rows):
        raise IncompleteAuthority("mixed-symbol funding history")
    if any(row.nominal_ms >= current.nominal_ms or row.available_ms > decision_ms for row in rows):
        raise IncompleteAuthority("future or unavailable funding row in prior-30 history")
    for row in rows:
        row.validate()
    current.validate()
    cadences = {row.interval_hours for row in (*rows, current)}
    if len(cadences) != 1:
        raise IncompleteAuthority("changed funding cadence is ambiguous and vetoes the symbol")
    chain = [*rows, current]
    r8_history: list[float] = []
    for previous, row in zip(chain, chain[1:]):
        interval = _interval_hours(previous, row)
        normalized = row.rate * (8.0 / interval)
        if not math.isfinite(normalized):
            raise IncompleteAuthority("non-finite normalized funding")
        if row is not current:
            r8_history.append(normalized)
    if len(r8_history) != 30:
        raise AssertionError("internal prior-30 normalization error")
    current_r8 = current.rate * (8.0 / _interval_hours(rows[-1], current))
    center = statistics.median(r8_history)
    mad = statistics.median(abs(value - center) for value in r8_history)
    scale = 1.4826 * mad
    if not math.isfinite(scale) or scale <= 0:
        raise IncompleteAuthority("non-positive robust funding scale")
    z = (current_r8 - center) / scale
    if not math.isfinite(z):
        raise IncompleteAuthority("non-finite funding surprise")
    return FundingShock(current.symbol, current.nominal_ms, current_r8, center, mad, z)


@dataclass(frozen=True)
class IndexedPair:
    index: int
    long_symbol: str
    short_symbol: str
    long_z: float
    short_z: float


@dataclass(frozen=True)
class PairDecision:
    pair: IndexedPair
    accepted: bool
    reason: str


def fixed_index_pairs(shocks: Iterable[FundingShock]) -> tuple[IndexedPair, ...]:
    values = tuple(shocks)
    if len({item.symbol for item in values}) != len(values):
        raise IncompleteAuthority("duplicate symbol in settlement cohort")
    if len(values) < 12:
        return ()
    nominal = {item.nominal_ms for item in values}
    if len(nominal) != 1:
        raise IncompleteAuthority("mixed nominal timestamps in settlement cohort")
    longs = sorted((item for item in values if item.z <= -2.0), key=lambda item: (item.z, item.symbol))[:3]
    shorts = sorted((item for item in values if item.z >= 2.0), key=lambda item: (-item.z, item.symbol))[:3]
    return tuple(
        IndexedPair(i, long.symbol, short.symbol, long.z, short.z)
        for i, (long, short) in enumerate(zip(longs, shorts), start=1)
    )


def decide_indexed_pairs(
    pairs: Sequence[IndexedPair],
    *,
    occupied_symbols: Iterable[str] = (),
    infeasible_symbols: Iterable[str] = (),
) -> tuple[PairDecision, ...]:
    """Skip an infeasible index without ever re-pairing its members."""
    occupied, infeasible = set(occupied_symbols), set(infeasible_symbols)
    out: list[PairDecision] = []
    for pair in pairs:
        symbols = {pair.long_symbol, pair.short_symbol}
        if len(symbols) != 2:
            out.append(PairDecision(pair, False, "DUPLICATE_LEG"))
        elif symbols & occupied:
            out.append(PairDecision(pair, False, "ALREADY_OCCUPIED"))
        elif symbols & infeasible:
            out.append(PairDecision(pair, False, "EXCHANGE_OR_AUTHORITY_INFEASIBLE"))
        else:
            out.append(PairDecision(pair, True, "ACCEPTED"))
    return tuple(out)


@dataclass(frozen=True)
class HourBar:
    open_ms: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float

    @property
    def close_ms(self) -> int:
        return self.open_ms + HOUR_MS

    def validate(self) -> None:
        if self.open_ms < 0 or self.open_ms % HOUR_MS:
            raise IncompleteAuthority("off-grid 1h bar")
        values = (self.open, self.high, self.low, self.close, self.quote_volume)
        if not all(math.isfinite(v) for v in values) or min(self.open, self.high, self.low, self.close) <= 0:
            raise IncompleteAuthority("invalid bar values")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise IncompleteAuthority("inconsistent OHLC")


def first_open_at_or_after(bars: Sequence[HourBar], timestamp_ms: int) -> HourBar:
    eligible = []
    seen: set[int] = set()
    for bar in bars:
        bar.validate()
        if bar.open_ms in seen:
            raise IncompleteAuthority("duplicate 1h open")
        seen.add(bar.open_ms)
        if bar.open_ms >= timestamp_ms:
            eligible.append(bar)
    if not eligible:
        raise IncompleteAuthority("required next-open fill is missing")
    return min(eligible, key=lambda bar: bar.open_ms)


def entry_bar(bars: Sequence[HourBar], nominal_ms: int) -> HourBar:
    bar = first_open_at_or_after(bars, nominal_ms + FUNDING_DELAY_MS)
    if bar.open_ms <= nominal_ms:
        raise IncompleteAuthority("same-hour funding-event entry is forbidden")
    return bar


def fixed_exit_bar(bars: Sequence[HourBar], entry_ms: int) -> HourBar:
    return first_open_at_or_after(bars, entry_ms + 8 * HOUR_MS)


@dataclass(frozen=True)
class StopEvent:
    timestamp_ms: int
    reference_price: float
    kind: str
    funding_inclusive_at_exit: bool


def protective_stop_event(*, side: str, stop_price: float, bar: HourBar) -> StopEvent | None:
    bar.validate()
    if not math.isfinite(stop_price) or stop_price <= 0:
        raise C256Error("protective stop must be finite and positive")
    if side == "long":
        if bar.open <= stop_price:
            return StopEvent(bar.open_ms, min(stop_price, bar.open), "GAP_STOP", False)
        if bar.low <= stop_price:
            return StopEvent(bar.close_ms, stop_price, "RANGE_STOP", True)
    elif side == "short":
        if bar.open >= stop_price:
            return StopEvent(bar.open_ms, max(stop_price, bar.open), "GAP_STOP", False)
        if bar.high >= stop_price:
            return StopEvent(bar.close_ms, stop_price, "RANGE_STOP", True)
    else:
        raise C256Error("side must be long or short")
    return None


@dataclass(frozen=True)
class VolumeBar:
    close_ms: int
    quote_volume: float


def prior_volume(volumes: Sequence[VolumeBar], fill_ms: int) -> float:
    rows = [row for row in volumes if row.close_ms < fill_ms]
    if not rows:
        raise IncompleteAuthority("missing strictly-prior quote-volume authority")
    latest_ms = max(row.close_ms for row in rows)
    latest = [row for row in rows if row.close_ms == latest_ms]
    if len(latest) != 1:
        raise IncompleteAuthority("duplicate latest quote-volume authority")
    value = latest[0].quote_volume
    if not math.isfinite(value) or value <= 0:
        raise IncompleteAuthority("invalid strictly-prior quote volume")
    return value


def impact_terms(
    *, quantity: float, reference_price: float, fill_ms: int,
    volumes: Sequence[VolumeBar], required_exit: bool, stress: bool = False,
) -> tuple[float, float, str | None]:
    if not all(math.isfinite(value) and value > 0 for value in (quantity, reference_price)):
        raise C256Error("impact quantity and reference price must be finite and positive")
    notional = abs(quantity) * reference_price
    base_bps = STRESS_SLIPPAGE_BPS if stress else BASE_SLIPPAGE_BPS
    try:
        volume = prior_volume(volumes, fill_ms)
    except IncompleteAuthority:
        if not required_exit:
            raise
        return math.nan, base_bps, "INCOMPLETE_IMPACT_AUTHORITY"
    ratio = abs(notional) / volume
    if ratio > IMPACT_CAP and not required_exit:
        raise IncompleteAuthority("proposed entry exceeds frozen 0.10% impact cap")
    if ratio > IMPACT_CAP:
        return ratio, base_bps * max(1.0, ratio / IMPACT_CAP), "INCOMPLETE_IMPACT_LIMIT"
    return ratio, base_bps, None


@dataclass(frozen=True)
class Fill:
    timestamp_ms: int
    symbol: str
    side: str
    quantity: float
    reference_price: float
    execution_price: float
    fee_dollars: float
    slippage_dollars: float
    impact_ratio: float


def make_fill(
    *, timestamp_ms: int, symbol: str, side: str, quantity: float,
    reference_price: float, slippage_bps: float, impact_ratio: float,
) -> Fill:
    if side not in {"buy", "sell"}:
        raise C256Error("fill side must be buy or sell")
    values = (quantity, reference_price, slippage_bps)
    if not all(math.isfinite(value) for value in values) or quantity <= 0 or reference_price <= 0 or slippage_bps < 0:
        raise C256Error("invalid fill inputs")
    direction = 1.0 if side == "buy" else -1.0
    execution = reference_price * (1.0 + direction * slippage_bps / 10_000.0)
    slippage = abs(quantity) * abs(execution - reference_price)
    fee = abs(quantity * execution) * FEE_BPS / 10_000.0
    return Fill(timestamp_ms, symbol, side, quantity, reference_price, execution, fee, slippage, impact_ratio)


def raw_move_pnl(*, position_side: str, quantity: float, entry_reference: float, exit_reference: float) -> float:
    sign = 1.0 if position_side == "long" else -1.0 if position_side == "short" else None
    if sign is None:
        raise C256Error("position_side must be long or short")
    return sign * abs(quantity) * (exit_reference - entry_reference)


def signed_funding_pnl(*, position_side: str, quantity: float, mark_price: float, funding_rate: float) -> float:
    side_sign = 1.0 if position_side == "long" else -1.0 if position_side == "short" else None
    if side_sign is None or not all(math.isfinite(v) for v in (quantity, mark_price, funding_rate)):
        raise C256Error("invalid signed-funding inputs")
    return -side_sign * abs(quantity) * mark_price * funding_rate


def settlement_is_in_position(
    *, nominal_ms: int, entry_ms: int, exit_ms: int, range_stop_exit: bool = False,
) -> bool:
    return entry_ms <= nominal_ms <= exit_ms if range_stop_exit else entry_ms <= nominal_ms < exit_ms


def net_pnl(*, raw_movement: float, fills: Sequence[Fill], funding_pnl: float) -> float:
    values = (raw_movement, funding_pnl, *(fill.fee_dollars for fill in fills), *(fill.slippage_dollars for fill in fills))
    if not all(math.isfinite(value) for value in values):
        raise C256Error("non-finite ledger value")
    return raw_movement - sum(fill.fee_dollars + fill.slippage_dollars for fill in fills) + funding_pnl


def stressed_funding(value: float) -> float:
    if not math.isfinite(value):
        raise C256Error("non-finite funding")
    return value * 1.5 if value < 0 else value / 1.5 if value > 0 else 0.0


def modeled_pair_loss(
    *, long_quantity: float, long_entry: float, long_stop: float,
    short_quantity: float, short_entry: float, short_stop: float,
) -> float:
    values = (long_quantity, long_entry, long_stop, short_quantity, short_entry, short_stop)
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise C256Error("invalid modeled-risk input")
    if not long_stop < long_entry or not short_stop > short_entry:
        raise C256Error("protective stops must be adverse to their entry side")
    fills = (
        make_fill(timestamp_ms=0, symbol="LONG", side="buy", quantity=long_quantity,
                  reference_price=long_entry, slippage_bps=STRESS_SLIPPAGE_BPS, impact_ratio=0.0),
        make_fill(timestamp_ms=1, symbol="LONG", side="sell", quantity=long_quantity,
                  reference_price=long_stop, slippage_bps=STRESS_SLIPPAGE_BPS, impact_ratio=0.0),
        make_fill(timestamp_ms=0, symbol="SHORT", side="sell", quantity=short_quantity,
                  reference_price=short_entry, slippage_bps=STRESS_SLIPPAGE_BPS, impact_ratio=0.0),
        make_fill(timestamp_ms=1, symbol="SHORT", side="buy", quantity=short_quantity,
                  reference_price=short_stop, slippage_bps=STRESS_SLIPPAGE_BPS, impact_ratio=0.0),
    )
    raw_loss = (
        abs(long_quantity) * (long_entry - long_stop)
        + abs(short_quantity) * (short_stop - short_entry)
    )
    result = raw_loss + sum(fill.fee_dollars + fill.slippage_dollars for fill in fills)
    if not math.isfinite(result) or result <= 0:
        raise C256Error("invalid aggregate modeled pair loss")
    return result


def frozen_risk_dollars(equity: float) -> float:
    if not math.isfinite(equity) or equity <= 0:
        raise C256Error("invalid equity for frozen R authority")
    return equity * FROZEN_RISK_FRACTION


def pair_risk_r(loss_dollars: float, equity: float) -> float:
    if not all(math.isfinite(v) and v > 0 for v in (loss_dollars, equity)):
        raise C256Error("invalid risk inputs")
    return loss_dollars / frozen_risk_dollars(equity)


@dataclass(frozen=True)
class PairSizing:
    long_quantity: float
    short_quantity: float
    gross: float
    modeled_loss: float
    risk_r: float
    long_stop: float
    short_stop: float


def _floor_step(value: float, step: float) -> float:
    if not math.isfinite(value) or not math.isfinite(step) or value < 0 or step <= 0:
        raise C256Error("invalid exchange quantity step")
    return math.floor((value + 1e-12) / step) * step


def round_protective_stop(*, side: str, raw_stop: float, entry: float, tick_size: float) -> float:
    if not all(math.isfinite(value) and value > 0 for value in (raw_stop, entry, tick_size)):
        raise C256Error("stop, entry and tickSize must be finite and positive")
    if side == "long":
        if raw_stop >= entry:
            raise C256Error("long protective stop must be below entry")
        rounded = math.floor((raw_stop + 1e-12) / tick_size) * tick_size
        if not 0 < rounded < entry:
            raise IncompleteAuthority("long stop is invalid after conservative tick rounding")
        return rounded
    if side == "short":
        if raw_stop <= entry:
            raise C256Error("short protective stop must be above entry")
        rounded = math.ceil((raw_stop - 1e-12) / tick_size) * tick_size
        if not rounded > entry or not math.isfinite(rounded):
            raise IncompleteAuthority("short stop is invalid after conservative tick rounding")
        return rounded
    raise C256Error("stop side must be long or short")


def size_equal_gross_pair(
    *, equity: float, target_gross: float,
    long_entry: float, long_stop: float, long_tick: float, long_step: float, long_min_qty: float, long_max_qty: float, long_min_notional: float,
    short_entry: float, short_stop: float, short_tick: float, short_step: float, short_min_qty: float, short_max_qty: float, short_min_notional: float,
) -> PairSizing:
    """Floor both legs and shrink equal-gross sizing until aggregate loss <=1R."""
    scalars = (
        equity, target_gross, long_entry, long_stop, long_tick, long_step, long_min_qty, long_max_qty,
        long_min_notional, short_entry, short_stop, short_tick, short_step, short_min_qty,
        short_max_qty, short_min_notional,
    )
    if not all(math.isfinite(value) and value > 0 for value in scalars):
        raise C256Error("invalid pair-sizing input")
    long_stop = round_protective_stop(side="long", raw_stop=long_stop, entry=long_entry, tick_size=long_tick)
    short_stop = round_protective_stop(side="short", raw_stop=short_stop, entry=short_entry, tick_size=short_tick)
    gross = min(target_gross, 0.5 * equity)
    long_qty = _floor_step((gross / 2) / long_entry, long_step)
    short_qty = _floor_step((gross / 2) / short_entry, short_step)
    for _ in range(2):
        if long_qty <= 0 or short_qty <= 0:
            raise IncompleteAuthority("pair fails frozen exchange feasibility after floor rounding")
        loss = modeled_pair_loss(
            long_quantity=long_qty, long_entry=long_entry, long_stop=long_stop,
            short_quantity=short_qty, short_entry=short_entry, short_stop=short_stop,
        )
        risk = pair_risk_r(loss, equity)
        if risk <= 1.0 + 1e-12:
            break
        scale = 1.0 / risk
        long_qty = _floor_step(long_qty * scale, long_step)
        short_qty = _floor_step(short_qty * scale, short_step)
    else:  # pragma: no cover - defensive; one homogeneous rescale is sufficient
        raise IncompleteAuthority("pair risk cannot be reduced to 1R")
    if risk > 1.0 + 1e-12:
        raise IncompleteAuthority("rounded pair exceeds aggregate 1R risk")
    for label, qty, price, minimum, maximum, min_notional in (
        ("long", long_qty, long_entry, long_min_qty, long_max_qty, long_min_notional),
        ("short", short_qty, short_entry, short_min_qty, short_max_qty, short_min_notional),
    ):
        if qty < minimum or qty > maximum or qty * price < min_notional:
            raise IncompleteAuthority(f"{label} leg fails frozen exchange feasibility after floor rounding")
    actual_gross = long_qty * long_entry + short_qty * short_entry
    return PairSizing(long_qty, short_qty, actual_gross, loss, risk, long_stop, short_stop)


def admit_pair(
    *, proposed_modeled_loss: float, open_modeled_loss: float, open_pairs: int,
    proposed_gross: float, open_gross: float, equity: float,
) -> bool:
    if type(open_pairs) is not int or open_pairs < 0:
        raise C256Error("open_pairs must be an exact non-negative integer")
    values = (proposed_modeled_loss, open_modeled_loss, proposed_gross, open_gross, equity)
    if not all(math.isfinite(value) and value >= 0 for value in values) or equity <= 0 or proposed_modeled_loss <= 0:
        raise C256Error("invalid portfolio admission state")
    one_r_dollars = frozen_risk_dollars(equity)
    return (
        proposed_modeled_loss <= one_r_dollars + 1e-12
        and open_modeled_loss + proposed_modeled_loss <= 3.0 * one_r_dollars + 1e-12
        and open_pairs < 3
        and proposed_gross <= 0.5 * equity + 1e-12
        and open_gross + proposed_gross <= 1.5 * equity + 1e-12
    )


@dataclass(frozen=True)
class ExactFilters:
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal

    def validate(self) -> None:
        if any(not value.is_finite() or value <= 0 for value in (
                self.tick, self.step, self.min_qty, self.max_qty, self.min_notional)):
            raise IncompleteAuthority("non-positive exact exchange filter")
        if self.min_qty > self.max_qty:
            raise IncompleteAuthority("exact exchange quantity bounds are inverted")


@dataclass(frozen=True)
class ExactLeg:
    symbol: str
    side: int
    entry: Decimal
    atr24: Decimal
    filters: ExactFilters


@dataclass(frozen=True)
class ExactPairSizing:
    long_qty: Decimal
    short_qty: Decimal
    long_stop: Decimal
    short_stop: Decimal
    gross: Decimal
    modeled_loss: Decimal
    risk_r: Decimal


def _floor_decimal(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _fill_drag(qty: Decimal, reference: Decimal, side: int) -> Decimal:
    execution = reference * (Decimal(1) + Decimal(side) * Decimal("0.001"))
    return abs(qty * execution) * Decimal("0.0005") + abs(qty * (execution-reference))


def size_exact_pair(*, equity: Decimal, long: ExactLeg, short: ExactLeg) -> ExactPairSizing:
    """Frozen equal-gross, floor-only, exact-Decimal 1R sizing."""
    if not equity.is_finite() or equity <= 0 or long.side != 1 or short.side != -1:
        raise C256Error("invalid exact pair sizing authority")
    for leg in (long, short):
        leg.filters.validate()
        if (not leg.symbol or not leg.entry.is_finite() or leg.entry <= 0
                or not leg.atr24.is_finite() or leg.atr24 <= 0):
            raise IncompleteAuthority("invalid exact leg authority")
    long_stop = _floor_decimal(long.entry - Decimal(3)*long.atr24, long.filters.tick)
    short_stop = ((short.entry + Decimal(3)*short.atr24) / short.filters.tick
                  ).to_integral_value(rounding=ROUND_CEILING) * short.filters.tick
    if not (Decimal(0) < long_stop < long.entry and short.entry < short_stop):
        raise IncompleteAuthority("invalid conservatively rounded 3ATR stop")
    one_r, gross_cap = equity*Decimal("0.01"), equity*Decimal("0.5")
    per_long = long.entry-long_stop + _fill_drag(Decimal(1), long.entry, 1) + _fill_drag(Decimal(1), long_stop, -1)
    per_short = short_stop-short.entry + _fill_drag(Decimal(1), short.entry, -1) + _fill_drag(Decimal(1), short_stop, 1)
    half = min(gross_cap/Decimal(2), one_r/(per_long/long.entry+per_short/short.entry))
    lq = _floor_decimal(half/long.entry, long.filters.step)
    sq = _floor_decimal(half/short.entry, short.filters.step)
    while lq > 0 and sq > 0:
        loss = lq*per_long + sq*per_short
        gross = lq*long.entry + sq*short.entry
        feasible = all((qty >= leg.filters.min_qty and qty <= leg.filters.max_qty
                        and qty*leg.entry >= leg.filters.min_notional)
                       for qty, leg in ((lq, long), (sq, short)))
        if loss <= one_r and gross <= gross_cap and feasible:
            return ExactPairSizing(lq, sq, long_stop, short_stop, gross, loss, loss/one_r)
        # Never increase. Reduce the larger actual notional; lexicographically later on ties.
        ln, sn = lq*long.entry, sq*short.entry
        if ln > sn or (ln == sn and long.symbol > short.symbol):
            lq -= long.filters.step
        else:
            sq -= short.filters.step
    raise IncompleteAuthority("exact pair is exchange/risk infeasible")


@dataclass(frozen=True)
class FilterSnapshot:
    captured_ms: int
    server_time_ms: int
    digest: str


def freeze_filter_snapshot(snapshots: Sequence[FilterSnapshot], fold_start_ms: int) -> FilterSnapshot:
    eligible = [row for row in snapshots if row.captured_ms < fold_start_ms and row.server_time_ms < fold_start_ms]
    if not eligible:
        raise IncompleteAuthority("no exchange-filter snapshot strictly before fold")
    latest_key = max((row.captured_ms, row.server_time_ms) for row in eligible)
    latest = [row for row in eligible if (row.captured_ms, row.server_time_ms) == latest_key]
    if len(latest) != 1 or not latest[0].digest:
        raise IncompleteAuthority("ambiguous exchange-filter snapshot")
    return latest[0]


def _iso_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise IncompleteAuthority("contract split timestamp lacks timezone")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def frozen_splits() -> Mapping[str, tuple[int, int, bool]]:
    walk = load_frozen_contract()["walk_forward"]
    raw = {"development": walk["development_test_range"]}
    raw.update({f"wf{index}": value for index, value in enumerate(walk["forward_folds"], start=1)})
    raw["holdout"] = walk["sealed_holdout"]
    out: dict[str, tuple[int, int, bool]] = {}
    for name, interval in raw.items():
        start, end = interval.split("/", 1)
        out[name] = (_iso_ms(start), _iso_ms(end), name == "holdout")
    return out


def enforce_split(timestamp_ms: int, split_name: str) -> str:
    splits = frozen_splits()
    if split_name not in splits:
        raise IncompleteAuthority("unknown split; caller-defined ranges are forbidden")
    start_ms, end_ms, sealed = splits[split_name]
    if sealed:
        raise EvaluationLocked("sealed holdout is unavailable on C257 Stage-A")
    if not start_ms <= timestamp_ms < end_ms:
        raise IncompleteAuthority("timestamp crosses frozen split boundary")
    if split_name == "development":
        eligible_start_raw = load_frozen_contract()["walk_forward"]["development_filter_eligible_segment"].split("/", 1)[0]
        if timestamp_ms < _iso_ms(eligible_start_raw):
            return PRE_FILTER_AUTHORITY_INELIGIBLE
    return "ELIGIBLE"


REQUIRED_REPORT_KEYS = frozenset({
    "candidate_id", "contract_sha256", "base_commit", "status", "promotable",
    "scheduled_cohorts", "eligible_cohorts", "admitted_cohorts", "skipped_pairs",
    "zero_trade_cohorts", "closed_paired_attempts", "distinct_symbols", "net_r",
    "mean_net_r", "profit_factor", "max_drawdown_r", "stress_net_r",
    "stress_profit_factor", "folds", "months", "sides", "symbols", "regimes",
    "fees_dollars", "slippage_dollars", "funding_dollars", "impact_failures",
    "atomicity_failures", "correlation", "btc_beta", "same_candle_cluster",
    "bootstrap_p_value", "family_alpha", "capital_feasibility", "ledger_digest",
    "leg_count", "holding_time_hours", "exchange_rejection_rate", "gross_imbalance",
    "recovery_duration_hours", "return_drawdown_ratio", "stress_max_drawdown_r", "risk_label",
})

_FINITE_NUMERIC_REPORT_KEYS = frozenset({
    "scheduled_cohorts", "eligible_cohorts", "admitted_cohorts", "skipped_pairs",
    "zero_trade_cohorts", "closed_paired_attempts", "leg_count", "distinct_symbols",
    "net_r", "mean_net_r", "profit_factor", "max_drawdown_r", "stress_net_r",
    "stress_profit_factor", "fees_dollars", "slippage_dollars", "funding_dollars",
    "impact_failures", "atomicity_failures", "correlation", "btc_beta",
    "same_candle_cluster", "bootstrap_p_value", "family_alpha", "holding_time_hours",
    "exchange_rejection_rate", "gross_imbalance", "recovery_duration_hours",
    "return_drawdown_ratio", "stress_max_drawdown_r",
})


def validate_report_schema(report: Mapping[str, object]) -> None:
    missing = REQUIRED_REPORT_KEYS - report.keys()
    if missing:
        raise C256Error(f"C256 report missing fields: {sorted(missing)}")
    if (report["candidate_id"], report["contract_sha256"], report["base_commit"]) != (
        257, CONTRACT_SHA256, CONTRACT_BASE
    ):
        raise C256Error("C256 report identity mismatch")
    if report["family_alpha"] != FAMILY_ALPHA:
        raise C256Error("C256 multiplicity threshold mismatch")
    if report["promotable"] is not False or report["status"] != "EVALUATION_LOCKED":
        raise C256Error("prestage reports must remain non-promotable and locked")
    for key in _FINITE_NUMERIC_REPORT_KEYS:
        value = report[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise C256Error(f"C256 report statistic is undefined or non-finite: {key}")
    count_keys = (
        "scheduled_cohorts", "eligible_cohorts", "admitted_cohorts", "skipped_pairs",
        "zero_trade_cohorts", "closed_paired_attempts", "leg_count", "distinct_symbols",
        "impact_failures", "atomicity_failures", "same_candle_cluster",
    )
    if any(not isinstance(report[key], int) or isinstance(report[key], bool) or report[key] < 0 for key in count_keys):
        raise C256Error("C256 report counts must be non-negative integers")
    nonnegative_keys = (
        "profit_factor", "max_drawdown_r", "stress_profit_factor", "fees_dollars",
        "slippage_dollars", "holding_time_hours", "exchange_rejection_rate",
        "gross_imbalance", "recovery_duration_hours", "stress_max_drawdown_r",
    )
    if any(float(report[key]) < 0 for key in nonnegative_keys):
        raise C256Error("C256 non-negative report statistic is invalid")
    if not 0 <= float(report["bootstrap_p_value"]) <= 1:
        raise C256Error("C256 bootstrap p-value is invalid")
    if not 0 <= float(report["exchange_rejection_rate"]) <= 1:
        raise C256Error("C256 exchange rejection rate is invalid")
    if not -1 <= float(report["correlation"]) <= 1:
        raise C256Error("C256 correlation is invalid")
    for key in ("folds", "months", "sides", "symbols", "regimes", "capital_feasibility"):
        if not isinstance(report[key], Mapping):
            raise C256Error(f"C256 report field must be a mapping: {key}")
    if report["risk_label"] not in {"standard", "elevated", "high", "reject"}:
        raise C256Error("C256 report risk label is invalid")
    if not isinstance(report["ledger_digest"], str) or len(report["ledger_digest"]) != 64:
        raise C256Error("C256 report ledger digest is invalid")


def stationary_bootstrap_indices(length: int, *, resamples: int = 10_000, block_length: int = 21, seed: int = 25620261107) -> tuple[tuple[int, ...], ...]:
    """Deterministic index generator only; it never reads outcomes itself."""
    if length <= 0 or resamples != 10_000 or block_length != 21 or seed != 25620261107:
        raise C256Error("C256 stationary-bootstrap settings are frozen")
    import numpy as np
    rng = np.random.Generator(np.random.PCG64(seed))
    restart_probability = 1.0 / block_length
    out: list[tuple[int, ...]] = []
    for _ in range(resamples):
        index = int(rng.integers(length))
        sample: list[int] = []
        for _ in range(length):
            if sample and float(rng.random()) < restart_probability:
                index = int(rng.integers(length))
            sample.append(index)
            index = (index + 1) % length
        out.append(tuple(sample))
    return tuple(out)
