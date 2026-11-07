"""C257 causal evaluator core. No filesystem or network data loader lives here."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from dataclasses import dataclass, field, fields, is_dataclass, replace
from decimal import Decimal
from typing import Mapping, Protocol, Sequence

from tools.research import funding_shock_reversal_xsec_c257_stagea as S
from tools.research import c257_reporting as R


class EvaluatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class FundingPoint:
    symbol: str
    nominal_ms: int
    available_ms: int
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
    funding_interval_hours: int = 8
    settlement_anchor_ms: int = 0
    funding_authority_start_ms: int = 0
    funding_authority_end_ms: int = 0


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
    impact: Decimal
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
    initial_gross_cash: Decimal
    fills: tuple[FillCash, ...]
    stress_fills: tuple[FillCash, ...]
    funding: tuple[FundingCash, ...]
    base_net_cash: Decimal
    base_R: Decimal
    stress_net_cash: Decimal
    stress_R: Decimal
    atomic_failure: bool
    symbols: tuple[str, ...]
    regime: str = "UNCLASSIFIED"
    _capability: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class EvaluationInput:
    attempts: tuple[ClosedAttempt, ...]
    marks: tuple[MarkClose, ...]
    fold: str
    _capability: object = field(default=None, repr=False, compare=False)
    disclosure: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RawSymbolEvidence:
    current: S.FundingRow
    prior_31: tuple[S.FundingRow, ...]
    bars: tuple[S.HourBar, ...]
    atr_bars: tuple[S.HourBar, ...]
    filters: S.ExactFilters
    funding_tape: tuple[FundingPoint, ...]
    funding_marks: tuple[MarkClose, ...]
    funding_authority_start_ms: int
    funding_authority_end_ms: int


@dataclass(frozen=True)
class RawCohortEvidence:
    cohort_id: str
    nominal_ms: int
    symbols: Mapping[str, RawSymbolEvidence]
    ineligible_reason: str | None = None
    symbol_vetoes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SealedEvidence:
    fold: str
    initial_equity: Decimal
    cohorts: tuple[RawCohortEvidence, ...]
    portfolio_marks: tuple[MarkClose, ...]
    benchmark_bars: tuple[S.HourBar, ...] = ()
    coverage: Mapping[str, object] = field(default_factory=dict)


class SealedTypedLoader(Protocol):
    def load_development(self, *, capability: object) -> SealedEvidence: ...
    def load_forward(self, fold: str, *, capability: object) -> SealedEvidence: ...


FOLD_NAMES = ("development", "wf1", "wf2", "wf3")
_DERIVED_CAPABILITY = object()


def _canonical_evidence(value: object) -> object:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise S.IncompleteAuthority("sealed evidence contains non-finite Decimal")
        return str(value)
    if is_dataclass(value):
        return {item.name: _canonical_evidence(getattr(value, item.name))
                for item in fields(value) if not item.name.startswith("_")}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise S.IncompleteAuthority("sealed evidence mapping keys must be strings")
        return {key: _canonical_evidence(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical_evidence(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise S.IncompleteAuthority("sealed evidence contains non-finite float")
        return value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise S.IncompleteAuthority("sealed evidence contains an unsupported typed value")


def sealed_evidence_digest(value: SealedEvidence) -> str:
    if not isinstance(value, SealedEvidence):
        raise EvaluatorError("only typed sealed evidence can be source-bound")
    raw = json.dumps(_canonical_evidence(value), sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _exact_entry_bar(bars: Sequence[S.HourBar], nominal_ms: int) -> S.HourBar:
    target = ((nominal_ms+S.FUNDING_DELAY_MS+S.HOUR_MS-1)//S.HOUR_MS)*S.HOUR_MS
    index = _bar_index(bars)
    if target != nominal_ms+S.HOUR_MS or nominal_ms not in index or target not in index:
        raise S.IncompleteAuthority("T+300 entry lacks exact next canonical open/continuity")
    return index[target]


def _atr24(bars: Sequence[S.HourBar], entry_ms: int) -> Decimal:
    completed = sorted((bar for bar in bars if bar.close_ms <= entry_ms), key=lambda bar: bar.open_ms)
    if len(completed) < 25:
        raise S.IncompleteAuthority("ATR24 requires previous close plus 24 causal true ranges")
    for bar in completed:
        bar.validate()
    if [bar.open_ms for bar in completed] != list(range(completed[0].open_ms,
                                                        entry_ms, S.HOUR_MS)):
        raise S.IncompleteAuthority("ATR24 bars are not exact contiguous causal rows")
    values: list[Decimal] = []
    for previous, bar in zip(completed, completed[1:]):
        values.append(max(Decimal(str(bar.high-bar.low)),
                          abs(Decimal(str(bar.high-previous.close))),
                          abs(Decimal(str(bar.low-previous.close)))))
    atr = sum(values[:24], Decimal(0))/Decimal(24)
    for value in values[24:]:
        atr = (atr*Decimal(23)+value)/Decimal(24)
    if not atr.is_finite() or atr <= 0:
        raise S.IncompleteAuthority("ATR24 is non-positive")
    return atr


def _book_equity_at(initial_equity: Decimal, rows: Sequence[ClosedAttempt],
                    marks: Sequence[MarkClose], timestamp_ms: int) -> Decimal:
    """Settled cash plus causally marked open inventory at one admission open."""
    if not initial_equity.is_finite() or initial_equity <= 0:
        raise EvaluatorError("frozen initial equity is invalid")
    mark_index = {(mark.symbol, mark.close_ms): mark for mark in marks}
    if len(mark_index) != len(marks):
        raise S.IncompleteAuthority("duplicate portfolio mark authority")
    equity = initial_equity
    for row in rows:
        _validate_derived_attempt(row)
        entries = {cash.fill.symbol: cash for cash in row.fills
                   if cash.fill.phase == "entry"}
        finals = {cash.fill.symbol: cash for cash in row.fills
                  if cash.fill.phase != "entry"}
        for cash in row.fills:
            if cash.fill.timestamp_ms <= timestamp_ms:
                equity += cash.movement-cash.fee-cash.slippage-cash.impact
        for funding in row.funding:
            if funding.point.nominal_ms <= timestamp_ms:
                equity += funding.cash
        for symbol, entry_cash in entries.items():
            final_cash = finals[symbol]
            if entry_cash.fill.timestamp_ms <= timestamp_ms < final_cash.fill.timestamp_ms:
                mark = mark_index.get((symbol, timestamp_ms))
                if mark is None or not mark.price.is_finite() or mark.price <= 0:
                    raise S.IncompleteAuthority("current admission equity lacks a causal open-position mark")
                equity += (Decimal(entry_cash.fill.transaction_side)*entry_cash.fill.quantity
                           *(mark.price-entry_cash.fill.reference))
    if not equity.is_finite() or equity <= 0:
        raise EvaluatorError("settled and marked book equity is non-positive")
    return equity


def derive_evaluation(sealed: SealedEvidence, *, _diagnostic: str | None = None,
                      _primary_admissions: frozenset[str] | None = None) -> EvaluationInput:
    """Consume sealed raw rows and derive all economic attempts from scratch."""
    if not isinstance(sealed, SealedEvidence) or sealed.fold not in FOLD_NAMES:
        raise EvaluatorError("loader must return sealed raw evidence for one frozen fold")
    if not sealed.initial_equity.is_finite() or sealed.initial_equity <= 0:
        raise EvaluatorError("sealed book initial equity must be finite and positive")
    if (len({row.cohort_id for row in sealed.cohorts}) != len(sealed.cohorts)
            or len({row.nominal_ms for row in sealed.cohorts}) != len(sealed.cohorts)):
        raise S.IncompleteAuthority("duplicate scheduled cohort identity or nominal timestamp")
    split = S.frozen_splits()[sealed.fold]
    attempts: list[ClosedAttempt] = []
    decisions: list[dict[str, object]] = []
    active_until: dict[str, int] = {}
    active_pairs: list[tuple[int, Decimal, Decimal]] = []
    regime_start = sealed.coverage.get("effective_start_ms",split[0])
    regime_labels = R.regime_labels(tuple(bar for bar in sealed.benchmark_bars if bar.open_ms>=regime_start))
    if _diagnostic is not None:
        import numpy as np
        diagnostic_rng=np.random.Generator(np.random.PCG64(R.BASELINE_SEED))
    for cohort in sorted(sealed.cohorts, key=lambda row: (row.nominal_ms, row.cohort_id)):
        if not split[0] <= cohort.nominal_ms < split[1] or not cohort.cohort_id:
            raise S.IncompleteAuthority("cohort crosses frozen fold boundary")
        decision = {"cohort_id":cohort.cohort_id,"nominal_ms":cohort.nominal_ms,
                    "eligible_symbols":len(cohort.symbols),"admitted_attempts":[],
                    "skipped_pairs":[],"symbol_vetoes":dict(cohort.symbol_vetoes),
                    "reason":cohort.ineligible_reason}
        decisions.append(decision)
        if cohort.ineligible_reason is not None:
            continue
        if any(symbol != evidence.current.symbol for symbol, evidence in cohort.symbols.items()):
            raise S.IncompleteAuthority("cohort symbol key differs from funding authority")
        shocks = []
        for evidence in cohort.symbols.values():
            if evidence.current.nominal_ms != cohort.nominal_ms:
                raise S.IncompleteAuthority("mixed cohort nominal funding time")
            if not (split[0] <= evidence.funding_authority_start_ms
                    <= evidence.current.nominal_ms
                    < evidence.funding_authority_end_ms <= split[1]):
                raise S.IncompleteAuthority("full funding authority bounds escape the frozen fold")
            shocks.append(S.robust_funding_surprise(evidence.current, evidence.prior_31,
                decision_ms=cohort.nominal_ms+S.FUNDING_DELAY_MS))
        pairs = (S.fixed_index_pairs(shocks) if _diagnostic is None
                 else R.diagnostic_pairs(shocks,_diagnostic,diagnostic_rng))
        for pair in pairs:
            if (_primary_admissions is not None
                    and f"{cohort.cohort_id}:{pair.index}" not in _primary_admissions):
                decision["skipped_pairs"].append({"index":pair.index,"reason":"PRIMARY_NOT_ADMITTED"})
                continue
            if active_until.get(pair.long_symbol, -1) > cohort.nominal_ms+S.HOUR_MS or active_until.get(pair.short_symbol, -1) > cohort.nominal_ms+S.HOUR_MS:
                decision["skipped_pairs"].append({"index":pair.index,"reason":"ALREADY_OCCUPIED"})
                continue
            long_raw, short_raw = cohort.symbols[pair.long_symbol], cohort.symbols[pair.short_symbol]
            try:
                long_entry, short_entry = (_exact_entry_bar(long_raw.bars, cohort.nominal_ms),
                                           _exact_entry_bar(short_raw.bars, cohort.nominal_ms))
                long_atr, short_atr = _atr24(long_raw.atr_bars, long_entry.open_ms), _atr24(short_raw.atr_bars, short_entry.open_ms)
            except S.IncompleteAuthority as exc:
                decision["skipped_pairs"].append({"index":pair.index,"reason":"CAUSAL_AUTHORITY_VETO","detail":str(exc)})
                continue
            if long_entry.open_ms != short_entry.open_ms:
                raise S.IncompleteAuthority("pair legs do not share exact atomic entry open")
            exact_long = S.ExactLeg(pair.long_symbol, 1, Decimal(str(long_entry.open)), long_atr, long_raw.filters)
            exact_short = S.ExactLeg(pair.short_symbol, -1, Decimal(str(short_entry.open)), short_atr, short_raw.filters)
            equity = _book_equity_at(sealed.initial_equity, attempts,
                                     sealed.portfolio_marks, long_entry.open_ms)
            try:
                sizing = S.size_exact_pair(equity=equity, long=exact_long, short=exact_short)
            except S.IncompleteAuthority as exc:
                decision["skipped_pairs"].append({"index":pair.index,"reason":"EXCHANGE_INFEASIBLE","detail":str(exc)})
                continue  # frozen index is skipped, never re-paired
            plans = (
                LegPlan(pair.long_symbol,1,sizing.long_qty,long_entry.open_ms,exact_long.entry,
                        sizing.long_stop,long_raw.current.interval_hours,cohort.nominal_ms,
                        long_raw.funding_authority_start_ms,long_raw.funding_authority_end_ms),
                LegPlan(pair.short_symbol,-1,sizing.short_qty,short_entry.open_ms,exact_short.entry,
                        sizing.short_stop,short_raw.current.interval_hours,cohort.nominal_ms,
                        short_raw.funding_authority_start_ms,short_raw.funding_authority_end_ms),)
            try:
                for leg, raw in ((plans[0],long_raw),(plans[1],short_raw)):
                    volume = _latest_prior_volume(raw.bars, leg.entry_ms)
                    if abs(leg.quantity*leg.entry_reference)/volume > Decimal("0.001"):
                        raise S.IncompleteAuthority("entry exceeds frozen impact cap")
            except S.IncompleteAuthority as exc:
                decision["skipped_pairs"].append({"index":pair.index,"reason":"ENTRY_IMPACT_VETO","detail":str(exc)})
                continue
            active_pairs = [item for item in active_pairs if item[0] > long_entry.open_ms]
            if (len(active_pairs) >= 3
                    or sum((item[1] for item in active_pairs),Decimal(0))+sizing.modeled_loss > equity*Decimal("0.03")
                    or sum((item[2] for item in active_pairs),Decimal(0))+sizing.gross > equity*Decimal("1.5")):
                decision["skipped_pairs"].append({"index":pair.index,"reason":"PORTFOLIO_LIMIT"})
                continue
            attempt = close_attempt(attempt_id=f"{cohort.cohort_id}:{pair.index}", legs=plans,
                bars={pair.long_symbol:long_raw.bars,pair.short_symbol:short_raw.bars},
                points=long_raw.funding_tape+short_raw.funding_tape,
                marks=long_raw.funding_marks+short_raw.funding_marks, sizing=sizing)
            attempt = replace(attempt,regime=regime_labels.get(cohort.nominal_ms,"UNCLASSIFIED"))
            if attempt.close_ms >= split[1]:
                raise S.IncompleteAuthority("attempt crosses frozen flat fold boundary")
            attempts.append(attempt)
            decision["admitted_attempts"].append(attempt.attempt_id)
            active_pairs.append((attempt.close_ms,sizing.modeled_loss,sizing.gross))
            for symbol in attempt.symbols:
                active_until[symbol] = attempt.close_ms
    required_marks = {(fill.fill.symbol,timestamp) for row in attempts for fill in row.fills
                      if fill.fill.phase != "entry"
                      for timestamp in range(row.entry_ms,fill.fill.timestamp_ms,S.HOUR_MS)}
    selected_marks = tuple(mark for mark in sealed.portfolio_marks
                           if (mark.symbol,mark.close_ms) in required_marks)
    return EvaluationInput(tuple(attempts), selected_marks, sealed.fold,
                           _DERIVED_CAPABILITY, {"cohort_decisions":decisions,
                           "benchmark_bars":sealed.benchmark_bars,"coverage":sealed.coverage,
                           "initial_equity":sealed.initial_equity})


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
        stop = leg.stop_reference
        gap = bar.open <= stop if leg.position_side == 1 else bar.open >= stop
        touch = bar.low <= stop if leg.position_side == 1 else bar.high >= stop
        if gap:
            raw = min(stop, bar.open) if leg.position_side == 1 else max(stop, bar.open)
            return DerivedFill(open_ms, leg.symbol, -leg.position_side, leg.quantity,
                               Decimal(str(raw)), "stop", open_ms, False)
        if open_ms == due_ms:
            return DerivedFill(due_ms, leg.symbol, -leg.position_side, leg.quantity,
                               Decimal(str(bar.open)), "exit", open_ms, False)
        if touch:
            return DerivedFill(open_ms + S.HOUR_MS, leg.symbol, -leg.position_side,
                               leg.quantity, leg.stop_reference, "stop", open_ms, True)
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
    gap = mate_bar.open <= mate.stop_reference if mate.position_side == 1 else mate_bar.open >= mate.stop_reference
    raw = (min(mate.stop_reference, mate_bar.open) if mate.position_side == 1
           else max(mate.stop_reference, mate_bar.open)) if gap else mate_bar.open
    phase = "stop" if gap else "mate_cleanup"
    cleanup = DerivedFill(cleanup_ms, mate.symbol, -mate.position_side, mate.quantity,
                          Decimal(str(raw)), phase, cleanup_ms, False)
    return tuple(sorted((first, cleanup), key=lambda item: (item.timestamp_ms, item.symbol)))


def _latest_prior_volume(bars: Sequence[S.HourBar], asof_open_ms: int) -> Decimal:
    """Frozen single latest completed 1h quote-volume bar, never a 24h sum."""
    rows = [bar for bar in bars if bar.close_ms == asof_open_ms]
    if len(rows) != 1 or not math.isfinite(rows[0].quote_volume) or rows[0].quote_volume <= 0:
        raise S.IncompleteAuthority("exact causal prior-1h quote-volume authority is incomplete")
    return Decimal(str(rows[0].quote_volume))


def account_fill(fill: DerivedFill, leg: LegPlan, bars: Sequence[S.HourBar], *, stress: bool) -> FillCash:
    try:
        volume = _latest_prior_volume(bars, fill.source_open_ms)
        ratio = abs(fill.quantity*fill.reference)/volume
        incomplete = "INCOMPLETE_IMPACT_LIMIT" if ratio > Decimal("0.001") else None
    except S.IncompleteAuthority:
        if fill.phase == "entry":
            raise
        ratio, incomplete = Decimal("NaN"), "INCOMPLETE_IMPACT_AUTHORITY"
    base_bps = Decimal(10 if stress else 2)
    multiplier = max(Decimal(1), ratio/Decimal("0.001")) if ratio.is_finite() else Decimal(1)
    ordinary_rate = base_bps/Decimal(10_000)
    impact_rate = base_bps*(multiplier-Decimal(1))/Decimal(10_000)
    execution = fill.reference*(Decimal(1)+Decimal(fill.transaction_side)*(ordinary_rate+impact_rate))
    fee = abs(fill.quantity*execution)*Decimal("0.0005")
    slippage = abs(fill.quantity*fill.reference*ordinary_rate)
    impact = abs(fill.quantity*fill.reference*impact_rate)
    movement = (fill.reference-leg.entry_reference)*fill.quantity*Decimal(leg.position_side)
    return FillCash(fill, execution, movement, fee, slippage, impact, ratio, incomplete)


def derive_funding(legs: Sequence[LegPlan], finals: Sequence[DerivedFill],
                   points: Sequence[FundingPoint], marks: Sequence[MarkClose]) -> tuple[FundingCash, ...]:
    if len({(point.symbol, point.nominal_ms) for point in points}) != len(points):
        raise S.IncompleteAuthority("duplicate funding event")
    if {point.symbol for point in points} - {leg.symbol for leg in legs}:
        raise S.IncompleteAuthority("extra funding symbol outside accepted lifecycle")
    mark_index = {(mark.symbol, mark.close_ms): mark for mark in marks}
    if len(mark_index) != len(marks):
        raise S.IncompleteAuthority("duplicate causal mark")
    final_by_symbol = {fill.symbol: fill for fill in finals}
    out: list[FundingCash] = []
    for leg in legs:
        final = final_by_symbol[leg.symbol]
        if type(leg.funding_interval_hours) is not int or leg.funding_interval_hours not in S.ALLOWED_INTERVAL_HOURS:
            raise S.IncompleteAuthority("funding cadence is not canonical")
        step = leg.funding_interval_hours*S.HOUR_MS
        if (type(leg.funding_authority_start_ms) is not int
                or type(leg.funding_authority_end_ms) is not int
                or not 0 <= leg.funding_authority_start_ms < leg.funding_authority_end_ms):
            raise S.IncompleteAuthority("full funding authority bounds are missing")
        first = leg.settlement_anchor_ms
        while first-step >= leg.funding_authority_start_ms:
            first -= step
        while first < leg.funding_authority_start_ms:
            first += step
        expected_times = tuple(range(first, leg.funding_authority_end_ms, step))
        leg_points = tuple(sorted((p for p in points if p.symbol == leg.symbol), key=lambda p: p.nominal_ms))
        if tuple(point.nominal_ms for point in leg_points) != expected_times:
            raise S.IncompleteAuthority("funding authority tape omits/adds a canonical settlement")
        if any(point.available_ms != point.nominal_ms+S.FUNDING_DELAY_MS
               for point in leg_points):
            raise S.IncompleteAuthority("funding authority availability is not exact T+300")
        for point in leg_points:
            if not point.rate.is_finite():
                raise S.IncompleteAuthority("non-finite funding authority rate")
            inclusive = final.range_stop
            exposed = leg.entry_ms <= point.nominal_ms <= final.timestamp_ms if inclusive else leg.entry_ms <= point.nominal_ms < final.timestamp_ms
            if not exposed:
                continue
            mark = mark_index.get((leg.symbol, point.nominal_ms-S.HOUR_MS))
            if mark is None or not mark.price.is_finite() or mark.price <= 0:
                raise S.IncompleteAuthority("immediate strict-prior funding mark is missing")
            cash = -Decimal(leg.position_side)*leg.quantity*mark.price*point.rate
            out.append(FundingCash(point, mark, cash))
    expected_mark_keys = {(row.point.symbol, row.point.nominal_ms-S.HOUR_MS) for row in out}
    if set(mark_index) != expected_mark_keys:
        raise S.IncompleteAuthority("funding mark authority omits/adds a canonical strict-prior mark")
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
        initial_gross_cash = abs(leg.quantity*leg.entry_reference)
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
        initial_gross_cash = sizing.gross
    if not initial_risk_cash.is_finite() or initial_risk_cash <= 0:
        raise EvaluatorError("derived initial risk must be finite and positive")
    finals = derive_lifecycle(legs, bars, atomic_failure=atomic_failure)
    entries = tuple(DerivedFill(leg.entry_ms, leg.symbol, leg.position_side, leg.quantity,
                                leg.entry_reference, "entry", leg.entry_ms) for leg in legs)
    final_map = {fill.symbol: fill for fill in finals}
    expected_mark_keys = {(point.symbol, point.nominal_ms-S.HOUR_MS)
        for leg in legs for point in points if point.symbol == leg.symbol
        and leg.entry_ms <= point.nominal_ms
        and (point.nominal_ms <= final_map[leg.symbol].timestamp_ms
             if final_map[leg.symbol].range_stop else point.nominal_ms < final_map[leg.symbol].timestamp_ms)}
    funding = derive_funding(legs, finals, points, tuple(mark for mark in marks
        if (mark.symbol,mark.close_ms) in expected_mark_keys))
    leg_map = {leg.symbol: leg for leg in legs}
    base = tuple(account_fill(fill, leg_map[fill.symbol], bars[fill.symbol], stress=False)
                 for fill in (*entries, *finals))
    stressed = tuple(account_fill(fill, leg_map[fill.symbol], bars[fill.symbol], stress=True)
                      for fill in (*entries, *finals))
    base_funding = sum((row.cash for row in funding), Decimal(0))
    stress_funding = sum((row.cash*Decimal("1.5") if row.cash < 0 else row.cash/Decimal("1.5")
                          for row in funding), Decimal(0))
    def net(rows: Sequence[FillCash], funding_cash: Decimal) -> Decimal:
        return sum((row.movement-row.fee-row.slippage-row.impact for row in rows), Decimal(0))+funding_cash
    base_net, stress_net = net(base, base_funding), net(stressed, stress_funding)
    if atomic_failure:
        base_net = min(base_net, -sum((row.fee+row.slippage+row.impact for row in base), Decimal(0)))
        stress_net = min(stress_net, -sum((row.fee+row.slippage+row.impact for row in stressed), Decimal(0)))
    return ClosedAttempt(attempt_id, legs[0].entry_ms, max(fill.timestamp_ms for fill in finals),
                         initial_risk_cash, initial_gross_cash, base, stressed, funding,
                         base_net, base_net/initial_risk_cash,
                         stress_net, stress_net/initial_risk_cash, atomic_failure,
                         tuple(sorted(leg_map)), _capability=_DERIVED_CAPABILITY)


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


def _validate_derived_attempt(row: ClosedAttempt) -> None:
    if row._capability is not _DERIVED_CAPABILITY:
        raise EvaluatorError("caller-constructed closed attempt is forbidden")
    if (not row.attempt_id or not row.initial_risk_cash.is_finite()
            or row.initial_risk_cash <= 0 or not row.initial_gross_cash.is_finite()
            or row.initial_gross_cash <= 0 or row.entry_ms >= row.close_ms):
        raise EvaluatorError("derived attempt identity/risk/lifecycle is invalid")
    for fills in (row.fills, row.stress_fills):
        entries = {cash.fill.symbol: cash.fill for cash in fills if cash.fill.phase == "entry"}
        finals = {cash.fill.symbol: cash.fill for cash in fills if cash.fill.phase != "entry"}
        if set(entries) != set(row.symbols) or set(finals) != set(row.symbols):
            raise EvaluatorError("derived attempt is not one complete flat lifecycle")
        if max(fill.timestamp_ms for fill in finals.values()) != row.close_ms:
            raise EvaluatorError("derived close timestamp differs from lifecycle fills")
        if any(not value.is_finite() for cash in fills for value in (
                cash.execution, cash.movement, cash.fee, cash.slippage,
                cash.impact)):
            raise EvaluatorError("derived attempt contains non-finite execution economics")
    funding_cash = sum((item.cash for item in row.funding), Decimal(0))
    base_net = sum((cash.movement-cash.fee-cash.slippage-cash.impact
                    for cash in row.fills), Decimal(0))+funding_cash
    stress_funding = sum((item.cash*Decimal("1.5") if item.cash < 0
                          else item.cash/Decimal("1.5") for item in row.funding), Decimal(0))
    stress_net = sum((cash.movement-cash.fee-cash.slippage-cash.impact
                      for cash in row.stress_fills), Decimal(0))+stress_funding
    if row.atomic_failure:
        base_net = min(base_net, -sum((cash.fee+cash.slippage+cash.impact
                                      for cash in row.fills), Decimal(0)))
        stress_net = min(stress_net, -sum((cash.fee+cash.slippage+cash.impact
                                          for cash in row.stress_fills), Decimal(0)))
    if (row.base_net_cash != base_net or row.base_R != base_net/row.initial_risk_cash
            or row.stress_net_cash != stress_net
            or row.stress_R != stress_net/row.initial_risk_cash):
        raise EvaluatorError("derived attempt economics were altered after causal derivation")


def marked_cashflow_drawdown(rows: Sequence[ClosedAttempt], marks: Sequence[MarkClose], *, stress: bool = False) -> Decimal:
    mark_index = {(mark.symbol, mark.close_ms): mark for mark in marks}
    if len(mark_index) != len(marks):
        raise S.IncompleteAuthority("duplicate chronological portfolio mark")
    events: list[tuple[int, int, str, str, Decimal, DerivedFill | None]] = []
    required_marks: set[tuple[str, int]] = set()
    for row in rows:
        _validate_derived_attempt(row)
        entries = {cash.fill.symbol: cash.fill for cash in row.fills
                   if cash.fill.phase == "entry"}
        finals = {cash.fill.symbol: cash.fill for cash in row.fills
                  if cash.fill.phase != "entry"}
        if set(entries) != set(row.symbols) or set(finals) != set(row.symbols):
            raise EvaluatorError("marked drawdown lifecycle is not fully flat")
        for cash in (row.stress_fills if stress else row.fills):
            delta = cash.movement-cash.fee-cash.slippage-cash.impact
            events.append((cash.fill.timestamp_ms,
                           0 if cash.fill.phase == "entry" else 2,
                           row.attempt_id, cash.fill.symbol,
                           delta/row.initial_risk_cash, cash.fill))
        for funding in row.funding:
            events.append((funding.point.nominal_ms, 1, row.attempt_id,
                           funding.point.symbol,
                           (funding.cash*Decimal("1.5") if funding.cash < 0 else funding.cash/Decimal("1.5"))
                           /row.initial_risk_cash if stress else funding.cash/row.initial_risk_cash, None))
        for symbol, entry in entries.items():
            final = finals[symbol]
            for timestamp in range(row.entry_ms, final.timestamp_ms, S.HOUR_MS):
                required_marks.add((symbol, timestamp))
    if set(mark_index) != required_marks:
        raise S.IncompleteAuthority("chronological mark authority omits/adds an exposure mark")
    cash = peak = worst = Decimal(0)
    positions: dict[tuple[str, str], tuple[DerivedFill, Decimal]] = {}
    by_time: dict[int, list[tuple[int, str, str, Decimal, DerivedFill | None]]] = {}
    for timestamp, phase, attempt_id, symbol, value, fill in events:
        by_time.setdefault(timestamp, []).append((phase, attempt_id, symbol, value, fill))
    timeline = sorted(set(by_time) | {timestamp for _, timestamp in required_marks})
    for timestamp in timeline:
        for phase, attempt_id, symbol, value, fill in sorted(
                by_time.get(timestamp, ()), key=lambda item: (item[0], item[1], item[2])):
            cash += value
            key = (attempt_id, symbol)
            if phase == 0:
                if fill is None or key in positions:
                    raise EvaluatorError("duplicate/missing marked position entry")
                risk = next(row.initial_risk_cash for row in rows
                            if row.attempt_id == attempt_id)
                positions[key] = (fill, risk)
            elif phase == 2:
                if fill is None or positions.pop(key, None) is None:
                    raise EvaluatorError("marked position exit lacks one open entry")
        unrealized = Decimal(0)
        for (_, symbol), (entry, risk) in positions.items():
            mark = mark_index.get((symbol, timestamp))
            if mark is None or not mark.price.is_finite() or mark.price <= 0:
                raise S.IncompleteAuthority("missing chronological marked exposure")
            unrealized += (Decimal(entry.transaction_side)*entry.quantity
                           *(mark.price-entry.reference)/risk)
        equity = cash+unrealized
        peak=max(peak,equity); worst=max(worst,peak-equity)
    if positions:
        raise EvaluatorError("marked drawdown ended with open positions")
    return worst


def _evaluate_input(input_: EvaluationInput) -> Mapping[str, object]:
    if input_._capability is not _DERIVED_CAPABILITY or input_.fold not in (*FOLD_NAMES, "wf_combined"):
        raise EvaluatorError("statistics require causally derived frozen-fold evidence")
    if len({row.attempt_id for row in input_.attempts}) != len(input_.attempts):
        raise EvaluatorError("duplicate derived attempt identity")
    for row in input_.attempts:
        _validate_derived_attempt(row)
    rows = tuple(sorted(input_.attempts, key=lambda row: (row.entry_ms, row.symbols)))
    if not rows:
        packet={"candidate_id":257,"default_off":True,"paper_testnet_only":True,
            "fold":input_.fold,"attempts":0,"mean_R":None,"profit_factor":None,
            "stress_mean_R":None,"stress_profit_factor":None,"gross_mean_R":None,
            "realized_drawdown_R":"0","marked_cashflow_drawdown_R":"0","worse_of_drawdown_R":"0",
            "max_drawdown_R":"0","stress_max_drawdown_R":"0","bootstrap_p_value":None,
            "bootstrap_mean_R_95pct_interval":None,"family_alpha":str(Decimal("0.05")/256),
            "folds":{input_.fold:{"attempts":0,"mean_R":None}},"symbols":{},
            "gate_checks":{"nonempty_required_statistics":False},
            "verdict":"REJECT_DEVELOPMENT" if input_.fold=="development" else "REJECT_UNDEFINED"}
        packet.update(R.build_disclosures(input_,drawdown=Decimal(0),stress_drawdown=Decimal(0)))
        packet["result_digest"]=_packet_digest(packet)
        return packet
    values = [row.base_R for row in rows]
    stress = [row.stress_R for row in rows]
    try: pf=profit_factor(values)
    except EvaluatorError: pf=None
    try: stress_pf=profit_factor(stress)
    except EvaluatorError: stress_pf=None
    marked_dd = marked_cashflow_drawdown(rows, input_.marks)
    indices = S.stationary_bootstrap_indices(len(rows))
    observed = sum(values, Decimal(0))/Decimal(len(values))
    bootstrapped = [sum((values[i] for i in sample),Decimal(0))/Decimal(len(sample)) for sample in indices]
    centered = [value-observed for value in values]
    exceed = sum(sum((centered[i] for i in sample),Decimal(0))/Decimal(len(sample)) >= observed
                 for sample in indices)
    p_value = Decimal(1+exceed)/Decimal(10_001)
    close_order = tuple(sorted(rows, key=lambda row: (row.close_ms, row.attempt_id)))
    realized_dd = realized_drawdown([row.base_R for row in close_order])
    stress_realized_dd = realized_drawdown([row.stress_R for row in close_order])
    stress_marked_dd = marked_cashflow_drawdown(rows, input_.marks, stress=True)
    packet = {
        "candidate_id": 257, "default_off": True, "paper_testnet_only": True,
        "fold": input_.fold, "attempts": len(rows), "mean_R": str(observed),
        "profit_factor": str(pf) if pf is not None else None, "stress_mean_R": str(sum(stress, Decimal(0))/Decimal(len(stress))),
        "stress_profit_factor": str(stress_pf) if stress_pf is not None else None, "realized_drawdown_R": str(realized_dd),
        "marked_cashflow_drawdown_R": str(marked_dd),
        "worse_of_drawdown_R": str(max(marked_dd, realized_dd)),
        "max_drawdown_R":str(max(marked_dd, realized_dd)),
        "stress_realized_drawdown_R": str(stress_realized_dd),
        "stress_marked_cashflow_drawdown_R": str(stress_marked_dd),
        "stress_worse_of_drawdown_R": str(max(stress_realized_dd,stress_marked_dd)),
        "stress_max_drawdown_R":str(max(stress_realized_dd,stress_marked_dd)),
        "bootstrap_p_value": str(p_value), "family_alpha": str(Decimal("0.05")/Decimal(256)),
        "bootstrap_mean_R_95pct_interval":[str(R.percentile(bootstrapped,Decimal("0.025"))),
                                         str(R.percentile(bootstrapped,Decimal("0.975")))],
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
    packet.update(R.build_disclosures(input_,drawdown=max(marked_dd,realized_dd),
                                     stress_drawdown=max(stress_realized_dd,stress_marked_dd)))
    if pf is None: packet["undefined_statistics"].append("profit_factor")
    if stress_pf is None: packet["undefined_statistics"].append("stress_profit_factor")
    packet["report_statistics_defined"]=not packet["undefined_statistics"]
    if input_.fold == "development":
        checks = {
            "gross_expectancy_gt_0": gross_mean > 0,
            "mean_net_R_gt_0": observed > 0,
            "profit_factor_gt_1_05": pf is not None and pf > Decimal("1.05"),
            "attempts_gte_200": len(rows) >= 200,
            "symbols_gte_12": len(packet["symbols"]) >= 12,
            "stress_mean_gt_0": sum(stress, Decimal(0))/Decimal(len(stress)) > 0,
            "worse_of_drawdown_lte_60": max(marked_dd, realized_dd) <= 60,
            "complete_causal_authority":packet["impact_failures"]==0
                 and not packet["skipped_pair_reasons"].get("CAUSAL_AUTHORITY_VETO",0),
            "defined_required_statistics":packet["report_statistics_defined"],
        }
        packet["gate_checks"] = checks
        packet["verdict"] = "DEVELOPMENT_SURVIVOR" if all(checks.values()) else "REJECT_DEVELOPMENT"
    else:
        packet["gate_checks"] = {"mean_R_gt_minus_0_05": observed > Decimal("-0.05")}
        packet["verdict"] = "FORWARD_FOLD_EVIDENCE"
    packet["result_digest"] = hashlib.sha256(json.dumps(packet, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    return packet


def evaluate(sealed: SealedEvidence) -> Mapping[str, object]:
    """Only sealed raw rows may cross the public evaluator boundary."""
    input_=derive_evaluation(sealed)
    packet=dict(_evaluate_input(input_))
    packet["diagnostics"]=report_diagnostics(sealed,input_)
    packet["result_digest"]=_packet_digest(packet)
    return packet


def report_diagnostics(sealed: SealedEvidence, primary: EvaluationInput) -> Mapping[str, object]:
    packets={}
    for mode in ("random_opposite_tail","sign_flipped"):
        try:
            derived=derive_evaluation(sealed,_diagnostic=mode,
                _primary_admissions=frozenset(row.attempt_id for row in primary.attempts)
                if mode=="sign_flipped" else None)
            packets[mode]=R.diagnostic_summary(derived,mode)
        except (S.C257Error,EvaluatorError) as exc:
            packets[mode]={"case":mode,"status":"INCOMPLETE","reason":str(exc),
                           "promotable":False,"rescue_permitted":False}
    packets["equal_gross_eligible_universe"]={"status":"INCOMPLETE_SPECIFICATION",
        "missing":"Multi-symbol basket lifecycle, stop-mate cleanup, atomicity/attempt identity and inherited three-pair-cap mapping are unspecified, including odd eligible symbol counts.",
        "ruling":"Do not broaden the frozen two-leg primary engine; basket specification is a separate report-only follow-up.",
        "promotable":False,"rescue_permitted":False}
    return packets


def _packet_digest(packet: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps({key:value for key,value in packet.items() if key!="result_digest"},
        sort_keys=True,separators=(",", ":")).encode()).hexdigest()


def _symbol_contributions(rows: Sequence[ClosedAttempt]) -> Mapping[str, Decimal]:
    out: dict[str, Decimal] = {}
    for row in rows:
        funding = {symbol: sum((item.cash for item in row.funding if item.point.symbol == symbol), Decimal(0))
                   for symbol in row.symbols}
        for symbol in row.symbols:
            fills = [item for item in row.fills if item.fill.symbol == symbol]
            value = sum((item.movement-item.fee-item.slippage-item.impact for item in fills), Decimal(0))+funding[symbol]
            out[symbol] = out.get(symbol, Decimal(0))+value/row.initial_risk_cash
    return out


def evaluate_stage_a(development: SealedEvidence,
                     forwards: Sequence[SealedEvidence]) -> Mapping[str, object]:
    development_input = derive_evaluation(development)
    dev = dict(_evaluate_input(development_input))
    dev["diagnostics"]=report_diagnostics(development,development_input)
    dev["result_digest"]=_packet_digest(dev)
    if dev["verdict"] != "DEVELOPMENT_SURVIVOR":
        return {"candidate_id":257,"verdict":"REJECT_DEVELOPMENT","forward_opened":False,
                "development":dev,"default_off":True,"paper_testnet_only":True}
    if tuple(item.fold for item in forwards) != ("wf1","wf2","wf3"):
        raise EvaluatorError("forward evidence must be exact wf1/wf2/wf3 order")
    forward_inputs = tuple(derive_evaluation(item) for item in forwards)
    fold_packets = [dict(_evaluate_input(item)) for item in forward_inputs]
    for packet,sealed,input_ in zip(fold_packets,forwards,forward_inputs):
        packet["diagnostics"]=report_diagnostics(sealed,input_)
        packet["result_digest"]=_packet_digest(packet)
    if any(not item.attempts for item in forward_inputs):
        return {"candidate_id":257,"verdict":"REJECT_FORWARD","reason":"EMPTY_FORWARD_FOLD",
                "forward_opened":True,"development":dev,"folds":fold_packets,
                "default_off":True,"paper_testnet_only":True,"promotable":False}
    rows = tuple(row for item in forward_inputs for row in item.attempts)
    marks = tuple(mark for item in forward_inputs for mark in item.marks)
    combined = dict(_evaluate_input(EvaluationInput(
        rows, marks, "wf_combined", _DERIVED_CAPABILITY,
        {"cohort_decisions":[decision for item in forward_inputs for decision in item.disclosure["cohort_decisions"]],
         "benchmark_bars":tuple(bar for item in forward_inputs for bar in item.disclosure["benchmark_bars"]),
         "coverage":{"segments":tuple(item.disclosure["coverage"] for item in forward_inputs)},
         "fold_inputs":forward_inputs})))
    contributions = _symbol_contributions(rows)
    denominator = sum((abs(value) for value in contributions.values()), Decimal(0))
    if denominator <= 0:
        raise EvaluatorError("symbol concentration denominator is undefined")
    side_values = {1:Decimal(0),-1:Decimal(0)}
    for row in rows:
        entries = {item.fill.symbol:item for item in row.fills if item.fill.phase == "entry"}
        for item in row.fills:
            entry = entries[item.fill.symbol]
            side_values[entry.fill.transaction_side] += (item.movement-item.fee-item.slippage-item.impact)/row.initial_risk_cash
        for funding in row.funding:
            entry = entries[funding.point.symbol]
            side_values[entry.fill.transaction_side] += funding.cash/row.initial_risk_cash
    monthly: dict[str, Decimal] = {}
    for row in rows:
        key = datetime.fromtimestamp(row.entry_ms/1000,tz=timezone.utc).strftime("%Y-%m")
        monthly[key] = monthly.get(key,Decimal(0))+row.base_R
    expected_months=[]
    forward_start = S.frozen_splits()["wf1"][0]
    forward_end = S.frozen_splits()["wf3"][1]
    start_dt = datetime.fromtimestamp(forward_start/1000,tz=timezone.utc)
    end_dt = datetime.fromtimestamp(forward_end/1000,tz=timezone.utc)
    cursor=datetime(start_dt.year,start_dt.month,1,tzinfo=timezone.utc)
    while cursor < end_dt:
        expected_months.append(cursor.strftime("%Y-%m"))
        cursor=datetime(cursor.year+(cursor.month==12),1 if cursor.month==12 else cursor.month+1,1,tzinfo=timezone.utc)
    positive_months=sum(monthly.get(key,Decimal(0))>0 for key in expected_months)
    mean=Decimal(combined["mean_R"]); stress_mean=Decimal(combined["stress_mean_R"])
    pf=Decimal(combined["profit_factor"]) if combined["profit_factor"] is not None else None
    stress_pf=Decimal(combined["stress_profit_factor"]) if combined["stress_profit_factor"] is not None else None
    dd=Decimal(combined["worse_of_drawdown_R"]); total=sum((r.base_R for r in rows),Decimal(0))
    stress_total=sum((r.stress_R for r in rows),Decimal(0))
    stress_dd=Decimal(combined["stress_worse_of_drawdown_R"])
    tier_pass = (dd <= 15 or (dd <= 30 and dd > 0 and total/dd >= Decimal("1.5"))
                 or (dd <= 60 and dd > 0 and stress_dd > 0 and total/dd >= 2 and stress_total/stress_dd >= 1))
    checks={
        "mean_R_gt_0_03":mean>Decimal("0.03"),"profit_factor_gt_1_10":pf is not None and pf>Decimal("1.10"),
        "attempts_gte_600":len(rows)>=600,"symbols_gte_12":len(contributions)>=12,
        "two_of_three_positive":sum(sum((r.base_R for r in item.attempts),Decimal(0))>0 for item in forward_inputs)>=2,
        "worst_fold_mean_gt_minus_0_05":min(sum((r.base_R for r in item.attempts),Decimal(0))/Decimal(len(item.attempts)) for item in forward_inputs)>Decimal("-0.05"),
        "both_sides_nonnegative":all(value>=0 for value in side_values.values()),
        "positive_months_gte_55pct":Decimal(positive_months)/Decimal(len(expected_months))>=Decimal("0.55"),
        "stress_mean_gt_0":stress_mean>0,"stress_pf_gt_1_02":stress_pf is not None and stress_pf>Decimal("1.02"),
        "multiplicity":Decimal(combined["bootstrap_p_value"])<=Decimal("0.05")/Decimal(256),
        "symbol_concentration_lte_25pct":max(abs(value)/denominator for value in contributions.values())<=Decimal("0.25"),
        "drawdown_tier":tier_pass,
        "complete_causal_authority":combined["impact_failures"]==0
            and not combined["skipped_pair_reasons"].get("CAUSAL_AUTHORITY_VETO",0),
        "defined_required_statistics":combined["report_statistics_defined"],
    }
    return {"candidate_id":257,"verdict":"PASS_STAGE_A" if all(checks.values()) else "REJECT_FORWARD",
            "forward_opened":True,"checks":checks,"development":dev,"folds":fold_packets,
            "combined":combined,"default_off":True,"paper_testnet_only":True}
