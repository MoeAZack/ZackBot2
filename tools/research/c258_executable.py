"""Fixed archive-to-book C258 replay. Imports never open economic data.

Real execution remains locked by the immutable preregistration. The separate
synthetic CLI route requires a byte-verified protected synthetic authority tree.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C
from tools.research.c258_archive_source import C258ArchiveSource, VerifiedSourceAbsence, frozen_window


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def claim_evaluation(pins: C.AuthorityPins, directory: Path | None, *, synthetic: bool) -> tuple[str, Path]:
    """An exclusive, durable claim precedes the first source economic read."""
    from tools.research.c258_production_authority import IMPLEMENTATION_PATHS
    code = {path: C._sha256(C.ROOT/path) for path in IMPLEMENTATION_PATHS}
    identity = C._sha256_bytes(C._canonical_bytes({"candidate": 258,
        "contract": C.CONTRACT_SHA256, "pins": asdict(pins), "code": code,
        "synthetic": synthetic}))
    if not synthetic:
        common = Path(C._git(C.ROOT, "rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = C.ROOT/common
        fixed = common.resolve()/"research-evaluation-claims"/"c258"
        if directory is not None and directory.resolve() != fixed:
            raise C.EvaluationLocked("production C258 claim directory is fixed across worktrees")
        directory = fixed
    if directory is None:
        raise C.EvaluationLocked("synthetic replay requires an isolated fixture claim directory")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/(f"synthetic-{identity}.json" if synthetic else "c258-stage-a-single-use.json")
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"identity": identity, "state": "CLAIMED_BEFORE_ECONOMIC_READ",
                       "code": code, "synthetic": synthetic}, handle, sort_keys=True)
            handle.flush()
            import os
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise C.EvaluationLocked("C258 single-use evaluation identity already claimed") from exc
    return identity, path


@dataclass
class FoldData:
    name: str
    start: int
    end: int
    bars: dict
    marks: dict
    funding: dict
    memberships: dict
    gaps: list


class ReplayIncomplete(C.IncompleteEpisode):
    def __init__(self, reason, *, capital, rows, unresolved):
        super().__init__(reason)
        self.rows = tuple(rows)
        self.partial = {"capital": capital, "admitted_lifecycle_rows": [asdict(row) for row in rows],
                        "unresolved_accepted_exposure": unresolved}


def _load_fold(source: C258ArchiveSource, name: str) -> FoldData:
    lo, hi = frozen_window(name)
    weeks = source._pit["weeks"]
    active = max((week for week in weeks if week["monday_ms"] <= lo),
                 key=lambda week: week["monday_ms"], default=None)
    if active is None:
        raise C.IncompleteAuthority("missing fold-start PIT cell")
    cells = [active]+[week for week in weeks if lo < week["monday_ms"] < hi]
    # This union is only a bounded I/O plan. Every decision below obtains its
    # eligible artifact order from the exact causal week and filter snapshot.
    symbols = {"BTCUSDT"}
    memberships = {}
    for week in cells:
        decision = max(lo, week["monday_ms"])
        if name == "development":
            decision = max(decision, 1734134400000)
        if decision < hi:
            members, filters = source.membership_and_filters(fold=name, decision_ms=decision)
            memberships[week["monday_ms"]] = (members, filters)
            symbols.update(members)
    bars, marks, funding, gaps = {}, {}, {}, []
    for symbol in sorted(symbols):
        for stream, target, getter in (("klines", bars, source.candles),
                                      ("markPriceKlines", marks, source.marks),
                                      ("fundingRate", funding, source.funding)):
            kwargs = dict(symbol=symbol, fold=name, start_ms=lo, end_ms=hi)
            if stream != "fundingRate":
                kwargs["allow_gaps"] = True
            try:
                target[symbol] = getter(**kwargs)
            except VerifiedSourceAbsence as exc:
                # Absence before an entry makes the symbol ineligible. If any
                # accepted leg needs this stream, replay raises INCOMPLETE.
                target[symbol] = ()
                gaps.append({"symbol": symbol, "stream": stream, "reason": str(exc)})
    return FoldData(name, lo, hi, {s: {b.open_ms: b for b in rows} for s, rows in bars.items()},
        {s: {m.close_ms: m for m in rows} for s, rows in marks.items()}, funding,
        memberships, gaps)


def _build_attempt(source, data, *, identifier, entry, regime, accepted, atomic_symbol=None):
    legs = {accepted.long.symbol: accepted.long, accepted.short.symbol: accepted.short}
    quantities = {accepted.long.symbol: accepted.sizing.long_qty,
                  accepted.short.symbol: accepted.sizing.short_qty}
    if atomic_symbol:
        legs = {atomic_symbol: legs[atomic_symbol]}
        quantities = {atomic_symbol: quantities[atomic_symbol]}
    due = entry+(C.HOUR_MS if atomic_symbol else 6*C.HOUR_MS)
    tapes = {}
    for symbol in legs:
        tapes[symbol] = tuple(data.bars[symbol][t] for t in range(entry-C.HOUR_MS,
            min(due+2*C.HOUR_MS, data.end), C.HOUR_MS) if t in data.bars[symbol])
    for _ in range(len(legs)+1):
        try:
            finals = C._derive_final_events(legs=legs, quantities=quantities,
                symbol_bars=tapes, entry_ms=entry, atomic_one_leg=bool(atomic_symbol))
            break
        except C.MissingLifecycleBar as exc:
            if exc.timestamp_ms != data.end:
                raise
            tapes[exc.symbol] += (source.boundary_open(symbol=exc.symbol, fold=data.name),)
    else:
        raise C.IncompleteAuthority("unresolved exact boundary-open evidence")
    fills = []
    for symbol, leg in sorted(legs.items()):
        prior = data.bars[symbol].get(entry-C.HOUR_MS)
        if prior is None:
            raise C.IncompleteAuthority("entry impact bar is missing")
        fills.append(C.make_fill(timestamp_ms=entry, symbol=symbol, side=leg.side,
            quantity=float(quantities[symbol]), reference_price=float(leg.entry_reference),
            phase="entry", movement_from=None, prior_quote_volume=prior.quote_volume))
    points, funding_marks = [], {}
    for final in finals:
        symbol, leg = final.symbol, legs[final.symbol]
        prior = data.bars[symbol].get(final.timestamp_ms-C.HOUR_MS)
        if prior is None:
            raise C.IncompleteAuthority("required exit impact bar is missing")
        fills.append(C.make_fill(timestamp_ms=final.timestamp_ms, symbol=symbol, side=-leg.side,
            quantity=float(quantities[symbol]), reference_price=final.reference,
            phase=final.phase, movement_from=float(leg.entry_reference),
            prior_quote_volume=prior.quote_volume))
        if not data.funding[symbol]:
            # An empty tape is valid only when a checksum-bound zero-row stream
            # exists; the source reader distinguishes that from absent records.
            if any(gap["symbol"] == symbol and gap["stream"] == "fundingRate" for gap in data.gaps):
                raise C.IncompleteAuthority("accepted exposure lacks exhaustive funding source")
        points.extend(C.FundingPoint(symbol, event.nominal_ms, event.rate)
            for event in data.funding[symbol] if entry <= event.nominal_ms < final.timestamp_ms)
        funding_marks[symbol] = tuple(data.marks[symbol].values())
    points.sort(key=lambda event: (event.nominal_time_ms, event.symbol))
    row = C.close_attempt(attempt_id=identifier, entry_ms=entry,
        close_ms=max(event.timestamp_ms for event in finals), long_symbol=accepted.long.symbol,
        short_symbol=accepted.short.symbol, regime=regime, fills=fills, symbol_bars=tapes,
        accepted_pair=None if atomic_symbol else accepted,
        accepted_atomic_leg=legs[atomic_symbol] if atomic_symbol else None,
        funding_points=points, funding_authority=points, funding_marks=funding_marks,
        exhaustive_source_tape=True, actual_mark_closes=True, atomic_one_leg=bool(atomic_symbol))
    _portfolio_marks(data, (row,))
    return row


def _period_grids(rows, lo, hi):
    start = datetime.fromtimestamp(lo/1000, timezone.utc)
    week = start-timedelta(days=start.weekday(), hours=start.hour, minutes=start.minute,
                           seconds=start.second, microseconds=start.microsecond)
    weeks = {}
    while week.timestamp()*1000 < hi:
        weeks[week.isoformat()] = 0
        week += timedelta(days=7)
    month = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = {}
    while month.timestamp()*1000 < hi:
        months[month.strftime("%Y-%m")] = 0.0
        month = month.replace(year=month.year+1, month=1) if month.month == 12 else month.replace(month=month.month+1)
    for row in rows:
        closed = datetime.fromtimestamp(row.close_ms/1000, timezone.utc)
        key = (closed-timedelta(days=closed.weekday(), hours=closed.hour, minutes=closed.minute,
                               seconds=closed.second, microseconds=closed.microsecond)).isoformat()
        weeks[key] = weeks.get(key, 0)+1
        month_key = closed.strftime("%Y-%m")
        months[month_key] = months.get(month_key, 0.0)+row.verdict_R
    return weeks, months


def _portfolio_marks(data, rows):
    needed = {(timestamp, leg.symbol) for row in rows for leg in row.legs
              for timestamp in range(row.entry_ms, row.close_ms+C.HOUR_MS, C.HOUR_MS)}
    result = []
    for timestamp, symbol in sorted(needed):
        mark = data.marks[symbol].get(timestamp-1)
        if mark is None:
            raise C.IncompleteAuthority("accepted lifecycle has a missing exact causal portfolio mark")
        result.append(C.PortfolioMark(timestamp, symbol, mark.price))
    return tuple(result)


def _pearson(left, right):
    if len(left) < 2 or np.var(left) <= 0 or np.var(right) <= 0:
        return None
    value = float(np.corrcoef(np.asarray(left), np.asarray(right))[0, 1])
    return value if math.isfinite(value) else None


def _economic_slices(rows):
    symbols, sides, regimes = {}, {"long": [], "short": []}, {}
    for row in rows:
        regimes.setdefault(row.regime, []).append(row.verdict_R)
        for leg in row.legs:
            symbols.setdefault(leg.symbol, []).append(leg.net_R)
            sides["long" if leg.position_side == 1 else "short"].append(leg.net_R)
    return {"symbols": {key: {"leg_closes": len(values), "net_R": math.fsum(values)}
                         for key, values in symbols.items()},
            "sides": {key: {"leg_closes": len(values), "net_R": math.fsum(values)}
                       for key, values in sides.items()},
            "regimes": {key: {"paired_closes": len(values), "net_R": math.fsum(values),
                               "mean_R": math.fsum(values)/len(values)}
                        for key, values in regimes.items()}}


def _exposure_report(data, base, counts):
    book_returns, btc_returns = [], []
    btc_missing = set()
    interval_gaps = []
    for prior, current in zip(counts["equities"], counts["equities"][1:]):
        timestamp, equity = current[:2]
        btc = data.bars.get("BTCUSDT", {})
        current_btc, previous_btc = btc.get(timestamp-C.HOUR_MS), btc.get(timestamp-2*C.HOUR_MS)
        first_interval = timestamp == data.start+C.HOUR_MS
        missing = []
        if current_btc is None:
            missing.append(timestamp-C.HOUR_MS)
        if not first_interval and previous_btc is None:
            missing.append(timestamp-2*C.HOUR_MS)
        if missing or prior[1] <= 0:
            btc_missing.update(missing)
            interval_gaps.append({"interval_start_ms": prior[0], "interval_end_ms": timestamp,
                                  "missing_BTC_open_ms": missing,
                                  "nonpositive_book_equity": prior[1] <= 0})
            book_returns.append(None)
            btc_returns.append(None)
        else:
            book_returns.append(equity/prior[1]-1)
            # The first benchmark return uses only the first in-window open
            # and close; no pre-fold BTC close is requested or inferred.
            reference = current_btc.open if first_interval else previous_btc.close
            btc_returns.append(current_btc.close/reference-1)
    alignment_complete = not interval_gaps
    beta = None
    if alignment_complete and len(btc_returns) > 1 and np.var(btc_returns) > 0:
        beta = float(np.cov(np.asarray(book_returns), np.asarray(btc_returns), ddof=0)[0, 1]
                     /np.var(btc_returns))
    clusters = {}
    for row in base.attempts:
        clusters[row.entry_ms] = clusters.get(row.entry_ms, 0)+1
    return {"hourly_observations": len(counts["occupancy"]),
        "pair_hours": sum(sample[1] for sample in counts["occupancy"]),
        "leg_hours": sum(sample[2] for sample in counts["occupancy"]),
        "fraction_hours_exposed": sum(sample[1] > 0 for sample in counts["occupancy"])/len(counts["occupancy"]),
        "max_concurrent_pairs": max((sample[1] for sample in counts["occupancy"]), default=0),
        "max_same_hour_cluster": max(clusters.values(), default=0),
        "mean_long_short_gross_imbalance": math.fsum(sample[5] for sample in counts["equities"])/len(counts["equities"]),
        "max_long_short_gross_imbalance": max((sample[5] for sample in counts["equities"]), default=0),
        "required_canonical_BTC_intervals": len(book_returns),
        "matched_canonical_BTC_intervals": len(book_returns)-len(interval_gaps),
        "correlation_intervals": len(book_returns) if alignment_complete else 0,
        "BTC_alignment_complete": alignment_complete,
        "BTC_interval_gaps": interval_gaps,
        "BTC_authority_gaps": [{"symbol": "BTCUSDT", "stream": "klines",
            "kind": "MISSING_REQUIRED_CANONICAL_BTC_BAR", "open_ms": timestamp}
            for timestamp in sorted(btc_missing)],
        "book_BTC_return_correlation": _pearson(book_returns, btc_returns) if alignment_complete else None,
        "book_BTC_beta": beta,
        "paired_long_short_contribution_correlation": _pearson(
            [row.long_contribution_R for row in base.attempts],
            [row.short_contribution_R for row in base.attempts]),
        "C257_overlap": {"status": "UNAVAILABLE_NO_ACCEPTED_C257_TAPE", "value": None}}


def _stress_equity_curve(base_rows, stress_rows, counts):
    deltas = {}
    for base, stress in zip(base_rows, stress_rows):
        for left, right in zip(base.fills, stress.fills):
            deltas.setdefault(left.timestamp_ms, []).append(
                (right.movement_cash-right.fee_drag-right.slippage_drag)
                -(left.movement_cash-left.fee_drag-left.slippage_drag))
        for left, right in zip(base.funding_events, stress.funding_events):
            deltas.setdefault(left.timestamp_ms, []).append(right.cash-left.cash)
        deltas.setdefault(base.close_ms, []).append(
            stress.conservative_adjustment_cash-base.conservative_adjustment_cash)
    accumulated = 0.0
    curve = []
    for timestamp, equity, cash, gross, remaining, imbalance in counts["equities"]:
        accumulated += math.fsum(deltas.pop(timestamp, ()))
        curve.append((timestamp, equity+accumulated, cash+accumulated))
    if deltas:
        raise C.IncompleteEpisode("stress cashflow lies outside the hourly book ledger")
    return curve


def _pair_book_correlations(data, rows, equity_samples):
    actions, ranks = {}, set()
    for row in rows:
        parts = row.attempt_id.split(":")
        rank = "indexed_pair_"+(parts[2] if len(parts) >= 5 else "single")
        ranks.add(rank)
        for fill in row.fills:
            actions.setdefault(fill.timestamp_ms, []).append((rank, row, "fill", fill))
        for funding in row.funding_events:
            actions.setdefault(funding.timestamp_ms, []).append((rank, row, "funding", funding))
        actions.setdefault(row.close_ms, []).append((rank, row, "adjustment", row.conservative_adjustment_cash))
    cash = {rank: 0.0 for rank in ranks}
    previous = dict(cash)
    series = {rank: [] for rank in ranks}
    positions, book = {}, []
    previous_equity = float(C.PRIMARY_CAPITAL)
    for sample in equity_samples:
        timestamp, equity = sample[:2]
        for rank, row, kind, value in actions.pop(timestamp, ()):
            if kind == "fill":
                cash[rank] += value.movement_cash-value.fee_drag-value.slippage_drag
                key = row.attempt_id, value.symbol
                if value.phase == "entry":
                    side = 1 if value.symbol == row.long_symbol else -1
                    positions[key] = (rank, side, value.quantity, value.reference_price)
                else:
                    positions.pop(key)
            elif kind == "funding":
                cash[rank] += value.cash
            else:
                cash[rank] += value
        profit = dict(cash)
        for (_, symbol), (rank, side, quantity, reference) in positions.items():
            mark = data.marks[symbol].get(timestamp-1)
            if mark is None:
                raise C.IncompleteAuthority("pair correlation has no causal mark")
            profit[rank] += side*quantity*(mark.price-reference)
        if not math.isclose(math.fsum(profit.values()), equity-C.PRIMARY_CAPITAL, abs_tol=1e-8):
            raise C.IncompleteEpisode("pair/book return attribution does not reconcile")
        for rank in ranks:
            series[rank].append((profit[rank]-previous[rank])/C.PRIMARY_CAPITAL)
        previous = profit
        book.append((equity-previous_equity)/C.PRIMARY_CAPITAL)
        previous_equity = equity
    return {"clock": "hourly after all same-open events", "denominator": C.PRIMARY_CAPITAL,
        "pair_return_matrix": {left: {right: _pearson(series[left], series[right])
            for right in sorted(ranks)} for left in sorted(ranks)},
        "pair_book_return_correlation": {rank: _pearson(values, book) for rank, values in series.items()}}


def replay_book(source, data, capital, ledger, *, synthetic=False):
    cash = float(capital)
    positions, actions, attempts, skips, occupancy, equities = {}, {}, [], [], [], []
    protections = {}
    ema = C.EMA200State()
    atr_states = {}
    scheduled = eligible = zero = indexed_skips = 0
    incomplete = False
    incomplete_reasons = set()
    admission_equities = []
    def book_state(timestamp):
        marked = remaining = gross = long_gross = short_gross = 0.0
        for symbol, (_, side, quantity, reference, stop) in positions.items():
            mark = data.marks[symbol].get(timestamp-1)
            if mark is None:
                raise C.IncompleteAuthority("open book lacks a current causal mark")
            marked += side*quantity*(mark.price-reference)
            value = abs(quantity*mark.price)
            gross += value
            if side == 1:
                long_gross += value
            else:
                short_gross += value
            exit_execution = stop*(1-side*C.STRESS_SLIPPAGE_BPS/10000)
            remaining += quantity*(max(0, side*(mark.price-stop))
                + abs(exit_execution)*C.FEE_BPS/10000+abs(stop)*C.STRESS_SLIPPAGE_BPS/10000)
        imbalance = abs(long_gross-short_gross)/gross if gross else 0.0
        return cash+marked, gross, remaining, imbalance
    def apply(event):
        nonlocal cash
        row, kind, value = event
        if kind == "fill":
            cash += value.movement_cash-value.fee_drag-value.slippage_drag
            if value.phase == "entry":
                leg = next(leg for leg in row.legs if leg.symbol == value.symbol)
                # Stops are reconstructed from the accepted entry, not from P&L.
                positions[value.symbol] = (row.attempt_id, leg.position_side, leg.quantity,
                                          value.reference_price, protections[row.attempt_id][value.symbol])
            else:
                if value.symbol not in positions or positions[value.symbol][0] != row.attempt_id:
                    raise C.IncompleteEpisode("book close does not match an occupied leg")
                del positions[value.symbol]
        elif kind == "funding":
            if value.symbol not in positions or positions[value.symbol][0] != row.attempt_id:
                raise C.IncompleteEpisode("book funding is outside exact leg exposure")
            cash += value.cash
        else:
            cash += value
    for timestamp in range(data.start, data.end+C.HOUR_MS, C.HOUR_MS):
        pending_funding = []
        for phase, identity, symbol, event in sorted(actions.pop(timestamp, ())):
            if event[1] == "funding":
                pending_funding.append((identity, symbol, event))
            else:
                apply(event)
        def finish_open():
            for _, _, event in sorted(pending_funding):
                apply(event)
            equity, gross, remaining, imbalance = book_state(timestamp)
            occupancy.append((timestamp, len({position[0] for position in positions.values()}), len(positions)))
            equities.append((timestamp, equity, cash, gross, remaining, imbalance))
        # At this boundary only bars ending one millisecond earlier are complete.
        completed = timestamp-C.HOUR_MS
        btc = data.bars.get("BTCUSDT", {}).get(completed)
        regime = ema.update(btc) if btc is not None else None
        if btc is None:
            ema = C.EMA200State()
        for symbol, index in data.bars.items():
            bar, previous = index.get(completed), index.get(completed-C.HOUR_MS)
            state = atr_states.get(symbol)
            if bar is None or previous is None or state is None or state[0] != completed-C.HOUR_MS:
                atr_states[symbol] = (completed, [], None)
                continue
            tr = max(bar.high-bar.low, abs(bar.high-previous.close), abs(bar.low-previous.close))
            seed, atr = state[1], state[2]
            if atr is None:
                seed = seed+[tr]
                atr = math.fsum(seed)/24 if len(seed) == 24 else None
            else:
                atr = (23*atr+tr)/24
            atr_states[symbol] = (completed, seed, atr)
        if timestamp <= data.start:
            finish_open()
            continue
        scheduled += 1
        if capital == C.PRIMARY_CAPITAL:
            ledger.append(fold=data.name, kind="SCHEDULED", detail=str(timestamp-1))
        if (regime is None or data.name == "development" and timestamp-1 < 1734134400000):
            zero += 1
            if capital == C.PRIMARY_CAPITAL:
                ledger.append(fold=data.name, kind="ZERO_TRADE", detail="regime/filter warmup")
            finish_open()
            continue
        week = max((monday for monday in data.memberships if monday <= timestamp-1), default=None)
        members, filters = data.memberships.get(week, ((), {}))
        feature_tapes = {symbol: tuple(data.bars[symbol][t] for t in range(
            timestamp-25*C.HOUR_MS, timestamp, C.HOUR_MS) if t in data.bars[symbol])
            for symbol in members}
        section = C.form_cross_section(feature_tapes, timestamp, members)
        if section is None or not section.pairs:
            zero += 1
            if capital == C.PRIMARY_CAPITAL:
                ledger.append(fold=data.name, kind="ZERO_TRADE", detail="no fixed robust tails")
            finish_open()
            continue
        eligible += 1
        admitted_before = len(attempts)
        for pair in section.pairs:
            long_symbol, short_symbol = pair.long.symbol, pair.short.symbol
            identifier = f"{data.name}:{timestamp}:{pair.index}:{long_symbol}:{short_symbol}"
            reason = None
            if timestamp+6*C.HOUR_MS > data.end:
                reason = "fixed exit would cross frozen split"
            if long_symbol in positions or short_symbol in positions:
                reason = "occupied indexed member"
            if len({position[0] for position in positions.values()}) >= 3:
                reason = "three-pair cap"
            legs = []
            for symbol, side in ((long_symbol, 1), (short_symbol, -1)):
                bar = data.bars[symbol].get(timestamp)
                atr = atr_states[symbol][2]
                if bar is None or atr is None:
                    reason = "missing exact next-open/causal ATR"
                    break
                exchange, tick = filters[symbol]
                if Decimal(str(bar.open)) % tick:
                    reason = "PRICE_FILTER off-grid raw entry reference"
                    break
                raw = Decimal(str(bar.open))-Decimal(side)*Decimal(2)*Decimal(str(atr))
                rounding = ROUND_FLOOR if side == 1 else ROUND_CEILING
                stop = (raw/tick).to_integral_value(rounding=rounding)*tick
                if stop <= 0:
                    reason = "non-positive frozen stop"
                    break
                legs.append(C.LegSpec(symbol, side, Decimal(str(bar.open)), stop, exchange))
            equity, gross, remaining, imbalance = book_state(timestamp)
            if equity <= 0:
                reason = "non-positive pre-admission equity"
            sized = None
            if reason is None:
                sized = C.size_pair(*legs, book_equity=Decimal(str(equity)),
                                   existing_remaining_stressed_stop_loss=Decimal(str(remaining)))
                if sized is None or float(sized.gross)+gross > 1.5*equity:
                    reason = "exchange/neutrality/equity/gross/risk infeasible"
                elif any(data.bars[leg.symbol][completed].quote_volume <= 0
                    or not C.entry_impact_feasible(float(quantity), float(leg.entry_reference),
                    data.bars[leg.symbol][completed].quote_volume)
                    for leg, quantity in zip(legs, (sized.long_qty, sized.short_qty))):
                    reason = "entry impact cap"
            if reason:
                indexed_skips += 1
                skips.append(identifier+":"+reason)
                if capital == C.PRIMARY_CAPITAL:
                    ledger.append(fold=data.name, kind="INDEXED_SKIP", attempt_id=identifier, detail=reason)
                continue
            accepted = C.AcceptedPair(legs[0], legs[1], sized)
            faults = source._manifest.get("synthetic_atomic_failures", {}) if synthetic else {}
            atomic = faults.get(str(timestamp))
            if atomic is not None and atomic not in (long_symbol, short_symbol):
                atomic = None
            try:
                row = _build_attempt(source, data, identifier=identifier, entry=timestamp,
                                     regime=regime, accepted=accepted, atomic_symbol=atomic)
            except (C.C258Error, OSError, ValueError, KeyError) as exc:
                accepted_legs = [leg for leg in legs if atomic is None or leg.symbol == atomic]
                entry_fills = [C.make_fill(timestamp_ms=timestamp, symbol=leg.symbol, side=leg.side,
                    quantity=float(sized.long_qty if leg.side == 1 else sized.short_qty),
                    reference_price=float(leg.entry_reference), phase="entry", movement_from=None,
                    prior_quote_volume=data.bars[leg.symbol][completed].quote_volume) for leg in accepted_legs]
                unresolved = {"attempt_id": identifier, "entry_ms": timestamp, "regime": regime,
                    "entry_fills": [asdict(fill) for fill in entry_fills],
                    "accepted_legs": [asdict(leg) for leg in accepted_legs], "sizing": asdict(sized),
                    "cash_before_entry": cash, "equity_before_entry": equity, "reason": str(exc)}
                if capital == C.PRIMARY_CAPITAL:
                    ledger.append(fold=data.name, kind="REJECTION", attempt_id=identifier,
                                  detail="INCOMPLETE_ACCEPTED_EXPOSURE: "+str(exc))
                raise ReplayIncomplete(str(exc), capital=capital, rows=attempts,
                                       unresolved=unresolved) from exc
            # Keep accepted protection alongside the immutable evidence without
            # making it a caller input to the accounting primitives.
            protections[row.attempt_id] = {leg.symbol: float(leg.stop_reference) for leg in legs}
            attempts.append(row)
            if atomic is not None:
                incomplete_reasons.add("ATOMIC_ONE_LEG_LOSS")
            if any(fill.impact_incomplete for fill in row.fills):
                incomplete_reasons.add("INCOMPLETE_IMPACT_LIMIT")
            leg_rules = {leg.symbol: leg.filters for leg in legs}
            for fill in row.fills:
                rule, quantity = leg_rules[fill.symbol], Decimal(str(fill.quantity))
                if Decimal(str(fill.reference_price)) % filters[fill.symbol][1]:
                    incomplete_reasons.add("INCOMPLETE_PRICE_FILTER_TICK_EXIT"
                        if fill.phase != "entry" else "INCOMPLETE_PRICE_FILTER_TICK_ENTRY")
                if (quantity < rule.min_qty or quantity > rule.max_qty
                        or quantity % rule.step_size
                        or quantity*Decimal(str(fill.reference_price)) < rule.min_notional):
                    incomplete_reasons.add("INCOMPLETE_EXCHANGE_FILTER_FILL")
            incomplete = bool(incomplete_reasons)
            for fill in row.fills:
                phase = 2 if fill.phase == "entry" else 0
                event = (row, "fill", fill)
                if fill.timestamp_ms == timestamp:
                    apply(event)
                else:
                    actions.setdefault(fill.timestamp_ms, []).append((phase, identifier, fill.symbol, event))
            for funding in row.funding_events:
                event = (row, "funding", funding)
                if funding.timestamp_ms == timestamp:
                    pending_funding.append((identifier, funding.symbol, event))
                else:
                    actions.setdefault(funding.timestamp_ms, []).append((1, identifier, funding.symbol, event))
            if row.conservative_adjustment_cash:
                actions.setdefault(row.close_ms, []).append((4, identifier, "", (row, "adjustment", row.conservative_adjustment_cash)))
            if capital == C.PRIMARY_CAPITAL:
                ledger.append(fold=data.name, kind="BASE_ROW", attempt_id=identifier, detail=row.derivation_identity_sha256)
            admission_equities.append((identifier, timestamp, equity, cash, gross, remaining))
        if len(attempts) == admitted_before:
            zero += 1
            if capital == C.PRIMARY_CAPITAL:
                ledger.append(fold=data.name, kind="ZERO_TRADE", detail="all indexed pairs skipped")
        finish_open()
    if positions or actions:
        raise C.IncompleteEpisode("book is not flat at frozen split end")
    if not math.isclose(cash-capital, math.fsum(row.verdict_net_cash for row in attempts), abs_tol=1e-8):
        raise C.IncompleteEpisode("concurrent book cash does not reconcile with attempt ledger")
    replay = C.ReplayResult(capital, tuple(attempts), tuple(skips), incomplete)
    return replay, dict(scheduled=scheduled, eligible=eligible, zero=zero,
        indexed_skips=indexed_skips, occupancy=occupancy, equities=equities,
        admission_equities=admission_equities, final_cash=cash,
        incomplete_reasons=sorted(incomplete_reasons))


def _fold_report(source, name, ledger, *, synthetic=False):
    data = _load_fold(source, name)
    books, diagnostic_errors = {}, {}
    for capital in (C.PRIMARY_CAPITAL, 1000, 3000, 4000):
        try:
            books[capital] = replay_book(source, data, capital, ledger, synthetic=synthetic)
        except ReplayIncomplete as exc:
            if capital == C.PRIMARY_CAPITAL:
                raise
            diagnostic_errors[str(capital)] = exc.partial
            books[capital] = (C.ReplayResult(capital, exc.rows, (), True), {"final_cash": None})
    base, counts = books[C.PRIMARY_CAPITAL]
    stress_rows = tuple(C.mechanical_stress_attempt(row) for row in base.attempts)
    for row in stress_rows:
        ledger.append(fold=name, kind="STRESS_ROW", attempt_id=row.attempt_id,
                      detail=row.derivation_identity_sha256)
    stress = C.ReplayResult(C.PRIMARY_CAPITAL, stress_rows, base.skips, base.incomplete)
    stress_curve = _stress_equity_curve(base.attempts, stress_rows, counts)
    weeks, months = _period_grids(base.attempts, data.start, data.end)
    marks = _portfolio_marks(data, base.attempts)
    exposure = _exposure_report(data, base, counts)
    report_statistics_defined = exposure["BTC_alignment_complete"] and all(exposure[key] is not None for key in (
        "book_BTC_return_correlation", "book_BTC_beta", "paired_long_short_contribution_correlation"))
    evidence = C.FoldEvidence(name, base, stress, tuple(weeks.values()), tuple(months.values()),
        marks, marks, counts["scheduled"], counts["eligible"], len(base.attempts), counts["zero"],
        counts["indexed_skips"], report_statistics_defined, not base.incomplete, tuple(weeks), tuple(months))
    report = {"name": name, "window": [data.start, data.end], "counts": counts,
        "window_cairo": [datetime.fromtimestamp(timestamp/1000, ZoneInfo("Africa/Cairo")).isoformat()
                         for timestamp in (data.start, data.end)],
        "weeks_including_zero": weeks, "months_including_zero": months,
        "authority_gaps": [*data.gaps, *exposure["BTC_authority_gaps"]],
        "required_report_complete": report_statistics_defined,
        "base_attempts": [asdict(row) for row in base.attempts],
        "diagnostic_incomplete_evidence": diagnostic_errors,
        "stress_attempts": [asdict(row) for row in stress_rows],
        "stress_full_book": {"hourly_equity_cash": stress_curve,
            "final_cash": C.PRIMARY_CAPITAL+math.fsum(row.verdict_net_cash for row in stress_rows),
            "pair_and_book_return_correlations": _pair_book_correlations(data, stress_rows, stress_curve),
            "same_admitted_selection": True},
        "close_attribution": "EXACT_CLOSE_UTC; boundary-only periods disclosed; aggregate denominator uses intersecting UTC periods",
        "exposure_and_correlation": exposure,
        "base_slices": _economic_slices(base.attempts),
        "stress_slices": _economic_slices(stress_rows),
        "pair_and_book_return_correlations": _pair_book_correlations(data, base.attempts, counts["equities"]),
        "capital_simulations": {str(capital): {"closed": len(book.attempts),
            "net_cash": math.fsum(row.verdict_net_cash for row in book.attempts),
            "attempts": [asdict(row) for row in book.attempts],
            "stress_net_cash": math.fsum(C.mechanical_stress_attempt(row).verdict_net_cash for row in book.attempts),
            "skips": list(book.skips), "incomplete": book.incomplete,
            "incomplete_reasons": detail.get("incomplete_reasons", []),
            "final_cash": detail["final_cash"]} for capital, (book, detail) in books.items()}}
    for label, rows in (("base", base.attempts), ("stress", stress_rows)):
        report[label] = C.fold_metrics(rows) if rows else {"closed_attempts": 0, "undefined": True}
        report[label]["fees"] = math.fsum(row.fee_drag for row in rows)
        report[label]["slippage"] = math.fsum(row.slippage_drag for row in rows)
        report[label]["funding"] = math.fsum(row.funding_cash for row in rows)
        report[label]["gross_R"] = math.fsum(row.gross_R for row in rows)
        report[label]["drawdown"] = C.fold_drawdown(evidence, stress=label == "stress") if rows else None
        if rows:
            events = C.derive_drawdown_events(rows, marks)
            _, recovery = C.chronological_drawdown((*events,
                C.DrawdownEvent(data.end, "MARK_END", 0, 0.0, 0.0)))
            report[label]["drawdown"]["recovery_ms"] = recovery
        report[label]["paired_closes"] = len(rows)
        report[label]["leg_closes"] = sum(len(row.legs) for row in rows)
        report[label]["impact_failures"] = sum(fill.impact_incomplete for row in rows for fill in row.fills)
        report[label]["actual_net_cash"] = math.fsum(row.actual_net_cash for row in rows)
        report[label]["verdict_net_cash"] = math.fsum(row.verdict_net_cash for row in rows)
        report[label]["conservative_adjustment_cash"] = math.fsum(row.conservative_adjustment_cash for row in rows)
        drawdown = report[label]["drawdown"]
        report[label]["return_drawdown_ratio"] = (report[label]["net_R"]/drawdown["worse_of_R"]
            if rows and drawdown["worse_of_R"] > 0 else None)
        report[label]["bootstrap"] = C.bootstrap_statistics([row.verdict_R for row in sorted(
            rows, key=lambda item: (item.entry_ms, item.long_symbol, item.short_symbol))]) if rows else None
    return evidence, report


def run_archive_evaluation(pins, store, claims, *, root=C.ROOT, synthetic=False):
    C.load_frozen_contract()
    if not synthetic:
        if pins is not None or root.resolve() != C.ROOT.resolve():
            raise C.EvaluationLocked("production authority comes only from the protected mechanical amendment")
        pins = C.assert_real_evaluation_authorized()
        if np.__version__ != "2.5.3":
            raise C.EvaluationLocked("frozen NumPy 2.5.3 runtime is required before real evaluation")
    source = C258ArchiveSource(pins, store, root=root)
    if synthetic and (root.resolve() == C.ROOT.resolve()
                      or source._manifest.get("synthetic_fixture_only") is not True):
        raise C.EvaluationLocked("synthetic replay requires a separate protected synthetic authority tree")
    if not synthetic and source._manifest.get("synthetic_fixture_only") is True:
        raise C.EvaluationLocked("synthetic source cannot satisfy production authority")
    from tools.research.c258_production_authority import IMPLEMENTATION_PATHS
    initial_code = {path: C._sha256(C.ROOT/path) for path in IMPLEMENTATION_PATHS}
    identity, claim = claim_evaluation(pins, claims, synthetic=synthetic)
    ledger = C.TrialLedger()
    result = {"candidate_id": 258, "synthetic_only": synthetic, "default_off": True,
              "forward_opened": False, "sealed_holdout_opened": False, "folds": [],
              "readiness": {"status": "INCOMPLETE_DEPENDENCY",
                  "C257_overlap": "UNAVAILABLE_NO_ACCEPTED_C257_TAPE",
                  "independent_exact_head_acceptance": "REQUIRED",
                  "paper_activation": "BLOCKED"}}
    try:
        development, report = _fold_report(source, "development", ledger, synthetic=synthetic)
        result["folds"].append(report)
        dev = C.development_gate(development)
        result["development"] = dev
        if not dev["pass"]:
            result["verdict"] = "REJECT_DEVELOPMENT"
        else:
            forwards = []
            result["forward_opened"] = True
            for name in ("wf1", "wf2", "wf3"):
                evidence, report = _fold_report(source, name, ledger, synthetic=synthetic)
                forwards.append(evidence)
                result["folds"].append(report)
            forward = C.forward_gate(forwards)
            result["forward"] = forward
            result["verdict"] = "FORWARD_PASS_PAPER_BLOCKED" if forward["pass"] else "REJECT_FORWARD"
        ledger.append(fold="stage_a", kind="REJECTION" if result["verdict"].startswith("REJECT")
                      else "VERDICT", detail=result["verdict"])
    except (C.C258Error, KeyError, ValueError, OSError) as exc:
        result.update(verdict="INCOMPLETE", technical_blocker=str(exc))
        if isinstance(exc, ReplayIncomplete):
            result["incomplete_accepted_exposure_evidence"] = exc.partial
        ledger.append(fold="stage_a", kind="REJECTION", detail="INCOMPLETE: "+str(exc))
    result["trial_ledger"] = [asdict(event) for event in ledger.events]
    result["trial_ledger_sha256"] = ledger.digest()
    result["evaluation_identity"] = identity
    result["code_blobs"] = {path: C._sha256(C.ROOT/path) for path in IMPLEMENTATION_PATHS}
    if result["code_blobs"] != initial_code:
        result.update(verdict="INCOMPLETE", technical_blocker="executable dependency changed during replay")
    result["economic_pass"] = result["verdict"] == "FORWARD_PASS_PAPER_BLOCKED"
    result["numpy_version"] = np.__version__
    result["code_head"] = C._git(C.ROOT, "rev-parse", "HEAD")
    output = C.immutable_output_identity(pins=pins, source_digest=pins.manifest_v6.content_digest,
                                         result=_finite(result))
    completed_claim = claim.with_suffix(".finished.tmp")
    import os
    with completed_claim.open("w", encoding="utf-8") as handle:
        json.dump({"identity": identity, "state": "FINISHED",
            "output_identity_sha256": output["output_identity_sha256"],
            "verdict": result["verdict"]}, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(completed_claim, claim)
    return output
