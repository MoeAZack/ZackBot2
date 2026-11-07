"""C257 causal evaluator core. No filesystem or network data loader lives here."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Protocol, Sequence

from tools.research import funding_shock_reversal_xsec_c257_stagea as S


class EvaluatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class FundingPoint:
    symbol: str
    nominal_ms: int
    rate: Decimal


@dataclass(frozen=True)
class MarkClose:
    symbol: str
    close_ms: int
    price: Decimal


@dataclass(frozen=True)
class LegPlan:
    symbol: str
    position_side: int
    quantity: Decimal
    entry_ms: int
    entry_reference: Decimal
    stop_reference: Decimal


@dataclass(frozen=True)
class DerivedFill:
    timestamp_ms: int
    symbol: str
    transaction_side: int
    quantity: Decimal
    reference: Decimal
    phase: str
    source_open_ms: int
    range_stop: bool = False


@dataclass(frozen=True)
class FillCash:
    fill: DerivedFill
    execution: Decimal
    movement: Decimal
    fee: Decimal
    slippage: Decimal
    impact_ratio: Decimal
    impact_failure: str | None


@dataclass(frozen=True)
class FundingCash:
    point: FundingPoint
    mark: MarkClose
    cash: Decimal


@dataclass(frozen=True)
class ClosedAttempt:
    attempt_id: str
    entry_ms: int
    close_ms: int
    initial_risk_cash: Decimal
    fills: tuple[FillCash, ...]
    funding: tuple[FundingCash, ...]
    base_net_cash: Decimal
    base_R: Decimal
    stress_net_cash: Decimal
    stress_R: Decimal
    atomic_failure: bool
    symbols: tuple[str, ...]
    regime: str = "UNSPECIFIED"


@dataclass(frozen=True)
class EvaluationInput:
    attempts: tuple[ClosedAttempt, ...]
    marks: tuple[MarkClose, ...]
    fold: str


class SealedTypedLoader(Protocol):
    def load(self, *, capability: object) -> EvaluationInput: ...


def _bar_index(bars: Sequence[S.HourBar]) -> dict[int, S.HourBar]:
    out: dict[int, S.HourBar] = {}
    for bar in bars:
        bar.validate()
        if bar.open_ms in out:
            raise S.IncompleteAuthority("duplicate canonical lifecycle bar")
        out[bar.open_ms] = bar
    return out


def _first_event(leg: LegPlan, bars: Sequence[S.HourBar], due_ms: int) -> DerivedFill:
    index = _bar_index(bars)
    for open_ms in range(leg.entry_ms, due_ms + S.HOUR_MS, S.HOUR_MS):
        bar = index.get(open_ms)
        if bar is None:
            raise S.IncompleteAuthority("lifecycle bar tape is not contiguous")
        stop = float(leg.stop_reference)
        gap = bar.open <= stop if leg.position_side == 1 else bar.open >= stop
        touch = bar.low <= stop if leg.position_side == 1 else bar.high >= stop
        if gap:
            raw = min(stop, bar.open) if leg.position_side == 1 else max(stop, bar.open)
            return DerivedFill(open_ms, leg.symbol, -leg.position_side, leg.quantity,
                               Decimal(str(raw)), "stop", open_ms, False)
        if touch:
            return DerivedFill(open_ms + S.HOUR_MS, leg.symbol, -leg.position_side,
                               leg.quantity, leg.stop_reference, "stop", open_ms, True)
        if open_ms == due_ms:
            return DerivedFill(due_ms, leg.symbol, -leg.position_side, leg.quantity,
                               Decimal(str(bar.open)), "exit", open_ms, False)
    raise EvaluatorError("mandatory lifecycle event was not derived")


def derive_lifecycle(legs: Sequence[LegPlan], bars: Mapping[str, Sequence[S.HourBar]],
                     *, atomic_failure: bool = False) -> tuple[DerivedFill, ...]:
    if len(legs) != (1 if atomic_failure else 2) or set(bars) != {leg.symbol for leg in legs}:
        raise EvaluatorError("declared legs and lifecycle tapes differ")
    if len({leg.symbol for leg in legs}) != len(legs):
        raise EvaluatorError("duplicate lifecycle symbol")
    entry_ms = legs[0].entry_ms
    if any(leg.entry_ms != entry_ms or leg.quantity <= 0 for leg in legs):
        raise EvaluatorError("entries are not one atomic cohort")
    due = entry_ms + (S.HOUR_MS if atomic_failure else 8*S.HOUR_MS)
    # Require the complete exposure tape plus the possible next-open cleanup.
    required = set(range(entry_ms-S.HOUR_MS, due+2*S.HOUR_MS, S.HOUR_MS))
    for leg in legs:
        if not required <= set(_bar_index(bars[leg.symbol])):
            raise S.IncompleteAuthority("complete entry-through-cleanup tape is missing")
    events = {leg.symbol: _first_event(leg, bars[leg.symbol], due) for leg in legs}
    if atomic_failure:
        event = events[legs[0].symbol]
        if event.phase == "stop":
            return (event,)
        raw = Decimal(str(_bar_index(bars[event.symbol])[due].open))
        return (DerivedFill(due, event.symbol, event.transaction_side, event.quantity,
                            raw, "atomic_cleanup", due),)
    stops = [event for event in events.values() if event.phase == "stop"]
    if not stops:
        return tuple(events[key] for key in sorted(events))
    first_source = min(event.source_open_ms for event in stops)
    same_bar = [event for event in stops if event.source_open_ms == first_source]
    if len(same_bar) == 2:  # contract: both stops in one bar book independently
        return tuple(sorted(same_bar, key=lambda item: (item.timestamp_ms, item.symbol)))
    first = min(same_bar, key=lambda item: (item.timestamp_ms, item.symbol))
    mate = next(leg for leg in legs if leg.symbol != first.symbol)
    cleanup_ms = first_source + S.HOUR_MS
    mate_bar = _bar_index(bars[mate.symbol])[cleanup_ms]
    gap = mate_bar.open <= float(mate.stop_reference) if mate.position_side == 1 else mate_bar.open >= float(mate.stop_reference)
    raw = (min(float(mate.stop_reference), mate_bar.open) if mate.position_side == 1
           else max(float(mate.stop_reference), mate_bar.open)) if gap else mate_bar.open
    phase = "stop" if gap else "mate_cleanup"
    cleanup = DerivedFill(cleanup_ms, mate.symbol, -mate.position_side, mate.quantity,
                          Decimal(str(raw)), phase, cleanup_ms, False)
    return tuple(sorted((first, cleanup), key=lambda item: (item.timestamp_ms, item.symbol)))


def _latest_prior_volume(bars: Sequence[S.HourBar], timestamp_ms: int) -> Decimal:
    eligible = [bar for bar in bars if bar.close_ms < timestamp_ms]
    if not eligible:
        raise S.IncompleteAuthority("strict-prior impact authority is missing")
    latest = max(bar.close_ms for bar in eligible)
    ordered = sorted(eligible, key=lambda bar: bar.close_ms)[-24:]
    if (len(ordered) != 24
            or [bar.close_ms for bar in ordered] != list(range(latest-23*S.HOUR_MS,
                                                               latest+S.HOUR_MS, S.HOUR_MS))
            or any(not math.isfinite(bar.quote_volume) or bar.quote_volume <= 0 for bar in ordered)):
        raise S.IncompleteAuthority("exact causal prior-24h quote-volume authority is incomplete")
    return sum((Decimal(str(bar.quote_volume)) for bar in ordered), Decimal(0))


def account_fill(fill: DerivedFill, leg: LegPlan, bars: Sequence[S.HourBar], *, stress: bool) -> FillCash:
    try:
        volume = _latest_prior_volume(bars, fill.timestamp_ms)
        ratio = abs(fill.quantity*fill.reference)/volume
        incomplete = "INCOMPLETE_IMPACT_LIMIT" if ratio > Decimal("0.001") else None
    except S.IncompleteAuthority:
        if fill.phase == "entry":
            raise
        ratio, incomplete = Decimal("NaN"), "INCOMPLETE_IMPACT_AUTHORITY"
    base_bps = Decimal(10 if stress else 2)
    multiplier = max(Decimal(1), ratio/Decimal("0.001")) if ratio.is_finite() else Decimal(1)
    slip = base_bps*multiplier/Decimal(10_000)
    execution = fill.reference*(Decimal(1)+Decimal(fill.transaction_side)*slip)
    fee = abs(fill.quantity*execution)*Decimal("0.0005")
    slippage = abs(fill.quantity*(execution-fill.reference))
    movement = (fill.reference-leg.entry_reference)*fill.quantity*Decimal(leg.position_side)
    return FillCash(fill, execution, movement, fee, slippage, ratio, incomplete)


def derive_funding(legs: Sequence[LegPlan], finals: Sequence[DerivedFill],
                   points: Sequence[FundingPoint], marks: Sequence[MarkClose]) -> tuple[FundingCash, ...]:
    if len({(point.symbol, point.nominal_ms) for point in points}) != len(points):
        raise S.IncompleteAuthority("duplicate funding event")
    mark_index = {(mark.symbol, mark.close_ms): mark for mark in marks}
    if len(mark_index) != len(marks):
        raise S.IncompleteAuthority("duplicate causal mark")
    final_by_symbol = {fill.symbol: fill for fill in finals}
    out: list[FundingCash] = []
    for leg in legs:
        final = final_by_symbol[leg.symbol]
        for point in sorted((p for p in points if p.symbol == leg.symbol), key=lambda p: p.nominal_ms):
            inclusive = final.range_stop
            exposed = leg.entry_ms <= point.nominal_ms <= final.timestamp_ms if inclusive else leg.entry_ms <= point.nominal_ms < final.timestamp_ms
            if not exposed:
                continue
            mark = mark_index.get((leg.symbol, point.nominal_ms-S.HOUR_MS))
            if mark is None or not mark.price.is_finite() or mark.price <= 0:
                raise S.IncompleteAuthority("immediate strict-prior funding mark is missing")
            cash = -Decimal(leg.position_side)*leg.quantity*mark.price*point.rate
            out.append(FundingCash(point, mark, cash))
    return tuple(out)


def close_attempt(*, attempt_id: str, legs: Sequence[LegPlan], bars: Mapping[str, Sequence[S.HourBar]],
                  points: Sequence[FundingPoint], marks: Sequence[MarkClose],
                  sizing: S.ExactPairSizing | None = None,
                  atomic_failure: bool = False) -> ClosedAttempt:
    if atomic_failure:
        leg = legs[0]
        initial_risk_cash = abs(leg.quantity*(leg.entry_reference-leg.stop_reference))
        initial_risk_cash += S._fill_drag(leg.quantity, leg.entry_reference, leg.position_side)
        initial_risk_cash += S._fill_drag(leg.quantity, leg.stop_reference, -leg.position_side)
    else:
        if sizing is None or len(legs) != 2:
            raise EvaluatorError("normal attempt requires exact SizedPair authority")
        by_side = {leg.position_side: leg for leg in legs}
        if (set(by_side) != {-1, 1} or by_side[1].quantity != sizing.long_qty
                or by_side[-1].quantity != sizing.short_qty
                or by_side[1].stop_reference != sizing.long_stop
                or by_side[-1].stop_reference != sizing.short_stop):
            raise EvaluatorError("accepted quantities/stops differ from exact sizing")
        initial_risk_cash = sizing.modeled_loss
    if not initial_risk_cash.is_finite() or initial_risk_cash <= 0:
        raise EvaluatorError("derived initial risk must be finite and positive")
    finals = derive_lifecycle(legs, bars, atomic_failure=atomic_failure)
    entries = tuple(DerivedFill(leg.entry_ms, leg.symbol, leg.position_side, leg.quantity,
                                leg.entry_reference, "entry", leg.entry_ms) for leg in legs)
    funding = derive_funding(legs, finals, points, marks)
    leg_map = {leg.symbol: leg for leg in legs}
    base = tuple(account_fill(fill, leg_map[fill.symbol], bars[fill.symbol], stress=False)
                 for fill in (*entries, *finals))
    stressed = tuple(account_fill(fill, leg_map[fill.symbol], bars[fill.symbol], stress=True)
                      for fill in (*entries, *finals))
    base_funding = sum((row.cash for row in funding), Decimal(0))
    stress_funding = sum((row.cash*Decimal("1.5") if row.cash < 0 else row.cash/Decimal("1.5")
                          for row in funding), Decimal(0))
    def net(rows: Sequence[FillCash], funding_cash: Decimal) -> Decimal:
        return sum((row.movement-row.fee-row.slippage for row in rows), Decimal(0))+funding_cash
    base_net, stress_net = net(base, base_funding), net(stressed, stress_funding)
    if atomic_failure:
        base_net = min(base_net, -sum((row.fee+row.slippage for row in base), Decimal(0)))
        stress_net = min(stress_net, -sum((row.fee+row.slippage for row in stressed), Decimal(0)))
    return ClosedAttempt(attempt_id, legs[0].entry_ms, max(fill.timestamp_ms for fill in finals),
                         initial_risk_cash, base, funding, base_net, base_net/initial_risk_cash,
                         stress_net, stress_net/initial_risk_cash, atomic_failure,
                         tuple(sorted(leg_map)))


def profit_factor(values: Sequence[Decimal]) -> Decimal:
    gains = sum((value for value in values if value > 0), Decimal(0))
    losses = -sum((value for value in values if value < 0), Decimal(0))
    if losses <= 0:
        raise EvaluatorError("profit factor is undefined without losses")
    return gains/losses


def realized_drawdown(values: Sequence[Decimal]) -> Decimal:
    equity = peak = Decimal(0)
    worst = Decimal(0)
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak-equity)
    return worst


def marked_cashflow_drawdown(rows: Sequence[ClosedAttempt], marks: Sequence[MarkClose]) -> Decimal:
    mark_index = {(mark.symbol, mark.close_ms): mark for mark in marks}
    if len(mark_index) != len(marks):
        raise S.IncompleteAuthority("duplicate chronological portfolio mark")
    events: list[tuple[int, int, Decimal]] = []
    for row in rows:
        entries = {cash.fill.symbol: cash.fill for cash in row.fills if cash.fill.phase == "entry"}
        finals = {cash.fill.symbol: cash.fill for cash in row.fills if cash.fill.phase != "entry"}
        if set(entries) != set(row.symbols) or set(finals) != set(row.symbols):
            raise EvaluatorError("marked drawdown lifecycle is not fully flat")
        realized_actual = Decimal(0)
        for cash in row.fills:
            delta = cash.movement-cash.fee-cash.slippage
            realized_actual += delta
            events.append((cash.fill.timestamp_ms, 1 if cash.fill.phase == "entry" else 3,
                           delta/row.initial_risk_cash))
        for funding in row.funding:
            realized_actual += funding.cash
            events.append((funding.point.nominal_ms, 2, funding.cash/row.initial_risk_cash))
        adjustment = row.base_net_cash-realized_actual
        events.append((row.close_ms, 4, adjustment/row.initial_risk_cash))
        for symbol, entry in entries.items():
            final = finals[symbol]
            position_side = entry.transaction_side
            for timestamp in range(row.entry_ms, final.timestamp_ms+S.HOUR_MS, S.HOUR_MS):
                mark = mark_index.get((symbol, timestamp))
                if mark is None:
                    raise S.IncompleteAuthority("missing chronological marked exposure")
                unrealized = Decimal(0) if timestamp >= final.timestamp_ms else (
                    Decimal(position_side)*entry.quantity*(mark.price-entry.reference)/row.initial_risk_cash)
                events.append((timestamp, 0, unrealized))
    cash = peak = worst = Decimal(0)
    marks_at_time: dict[int, Decimal] = {}
    for timestamp, phase, value in sorted(events, key=lambda item: (item[0], item[1])):
        if phase == 0:
            marks_at_time[timestamp] = marks_at_time.get(timestamp, Decimal(0))+value
            equity = cash+marks_at_time[timestamp]
        else:
            cash += value
            equity = cash+marks_at_time.get(timestamp, Decimal(0))
        peak=max(peak,equity); worst=max(worst,peak-equity)
    return worst


def evaluate(input_: EvaluationInput) -> Mapping[str, object]:
    if input_.fold == "holdout":
        raise S.EvaluationLocked("sealed holdout is unavailable to Stage A")
    rows = tuple(sorted(input_.attempts, key=lambda row: (row.entry_ms, row.symbols)))
    if not rows:
        raise EvaluatorError("empty fold statistics are undefined")
    values = [row.base_R for row in rows]
    stress = [row.stress_R for row in rows]
    pf, stress_pf = profit_factor(values), profit_factor(stress)
    marked_dd = marked_cashflow_drawdown(rows, input_.marks)
    indices = S.stationary_bootstrap_indices(len(rows))
    observed = sum(values, Decimal(0))/Decimal(len(values))
    centered = [value-observed for value in values]
    exceed = sum(1 for sample in indices
                 if sum((centered[i] for i in sample), Decimal(0))/Decimal(len(sample)) >= observed)
    p_value = Decimal(1+exceed)/Decimal(10_001)
    packet = {
        "candidate_id": 257, "default_off": True, "paper_testnet_only": True,
        "fold": input_.fold, "attempts": len(rows), "mean_R": str(observed),
        "profit_factor": str(pf), "stress_mean_R": str(sum(stress, Decimal(0))/Decimal(len(stress))),
        "stress_profit_factor": str(stress_pf), "realized_drawdown_R": str(realized_drawdown(values)),
        "marked_cashflow_drawdown_R": str(marked_dd),
        "worse_of_drawdown_R": str(max(marked_dd, realized_drawdown(values))),
        "bootstrap_p_value": str(p_value), "family_alpha": str(Decimal("0.05")/Decimal(256)),
        "folds": {input_.fold: {"attempts": len(rows), "mean_R": str(observed)}},
        "regimes": {regime: {"attempts": sum(row.regime == regime for row in rows),
                              "net_R": str(sum((row.base_R for row in rows if row.regime == regime), Decimal(0)))}
                    for regime in sorted({row.regime for row in rows})},
        "symbols": {symbol: sum(symbol in row.symbols for row in rows)
                    for symbol in sorted({symbol for row in rows for symbol in row.symbols})},
    }
    gross_mean = sum((sum((cash.movement for cash in row.fills), Decimal(0))/row.initial_risk_cash
                      for row in rows), Decimal(0))/Decimal(len(rows))
    packet["gross_mean_R"] = str(gross_mean)
    if input_.fold == "development":
        checks = {
            "gross_expectancy_gt_0": gross_mean > 0,
            "mean_net_R_gt_0": observed > 0,
            "profit_factor_gt_1_05": pf > Decimal("1.05"),
            "attempts_gte_200": len(rows) >= 200,
            "symbols_gte_12": len(packet["symbols"]) >= 12,
            "stress_mean_gt_0": sum(stress, Decimal(0))/Decimal(len(stress)) > 0,
            "worse_of_drawdown_lte_60": max(marked_dd, realized_drawdown(values)) <= 60,
        }
        packet["gate_checks"] = checks
        packet["verdict"] = "DEVELOPMENT_SURVIVOR" if all(checks.values()) else "REJECT_DEVELOPMENT"
    else:
        packet["gate_checks"] = {"mean_R_gt_minus_0_05": observed > Decimal("-0.05")}
        packet["verdict"] = "FORWARD_FOLD_EVIDENCE"
    packet["result_digest"] = hashlib.sha256(json.dumps(packet, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return packet
