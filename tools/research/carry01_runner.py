"""Deterministic reporting primitives for the signed CARRY-01 history study.

The archive reader and the portfolio simulator are deliberately separate.  This
module contains only result math whose inputs are already-causal observations.
It never downloads data, reads an exchange account, or places an order.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable, Sequence

YEAR_DAYS = 365.25


class CarryRunnerError(ValueError):
    """The supplied observations cannot support a signed CARRY-01 result."""


@dataclass(frozen=True)
class DailyReturn:
    """One included UTC-day arithmetic return and its matching cash return."""

    day_ordinal: int
    portfolio: float
    cash: float

    @property
    def cash_excess(self) -> float:
        return self.portfolio - self.cash


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise CarryRunnerError(f"{name} cannot be boolean")
    out = float(value)
    if not math.isfinite(out):
        raise CarryRunnerError(f"{name} must be finite")
    return out


def validate_daily_returns(rows: Iterable[DailyReturn]) -> list[DailyReturn]:
    """Require unique chronological days and valid, non-bankrupt return factors."""
    result = list(rows)
    if not result:
        raise CarryRunnerError("daily return series is empty")
    previous: int | None = None
    for row in result:
        if isinstance(row.day_ordinal, bool) or not isinstance(row.day_ordinal, int):
            raise CarryRunnerError("day_ordinal must be an integer")
        if previous is not None and row.day_ordinal <= previous:
            raise CarryRunnerError("daily returns must be unique and chronological")
        portfolio = _finite(row.portfolio, "portfolio return")
        cash = _finite(row.cash, "cash return")
        if portfolio <= -1 or cash <= -1:
            raise CarryRunnerError("daily return cannot lose more than all capital")
        previous = row.day_ordinal
    return result


def compound(returns: Iterable[float]) -> float:
    """Compound arithmetic returns without silently accepting invalid factors."""
    wealth = 1.0
    seen = False
    for value in returns:
        rate = _finite(value, "return")
        if rate <= -1:
            raise CarryRunnerError("return cannot lose more than all capital")
        wealth *= 1 + rate
        seen = True
    if not seen:
        raise CarryRunnerError("cannot compound an empty series")
    return wealth - 1


def annualized_return(rows: Iterable[DailyReturn]) -> float:
    """Apply the signed calendar-span annualization to included portfolio days."""
    data = validate_daily_returns(rows)
    span_days = data[-1].day_ordinal - data[0].day_ordinal + 1
    if span_days < 1:
        raise CarryRunnerError("annualization span must be positive")
    wealth = 1 + compound(row.portfolio for row in data)
    return wealth ** (YEAR_DAYS / span_days) - 1


def cash_excess_return(rows: Iterable[DailyReturn]) -> float:
    """Return the portfolio growth minus matching cash growth on included dates."""
    data = validate_daily_returns(rows)
    return compound(row.portfolio for row in data) - compound(row.cash for row in data)


def maximum_drawdown(equity: Sequence[float]) -> float:
    """Return a non-negative peak-to-trough drawdown fraction."""
    if not equity:
        raise CarryRunnerError("equity series is empty")
    peak = 0.0
    worst = 0.0
    for value in equity:
        current = _finite(value, "equity")
        if current < 0:
            raise CarryRunnerError("equity cannot be negative")
        peak = max(peak, current)
        if peak > 0:
            worst = max(worst, 1 - current / peak)
    return worst


def longest_recovery_days(equity_by_day: Sequence[tuple[int, float]]) -> int:
    """Measure the longest completed or still-open underwater calendar interval."""
    if not equity_by_day:
        raise CarryRunnerError("equity series is empty")
    peak = -math.inf
    underwater_since: int | None = None
    longest = 0
    previous: int | None = None
    for day, raw_equity in equity_by_day:
        if isinstance(day, bool) or not isinstance(day, int):
            raise CarryRunnerError("equity day must be an integer")
        if previous is not None and day <= previous:
            raise CarryRunnerError("equity days must be unique and chronological")
        equity = _finite(raw_equity, "equity")
        if equity < 0:
            raise CarryRunnerError("equity cannot be negative")
        if equity >= peak:
            if underwater_since is not None:
                longest = max(longest, day - underwater_since)
                underwater_since = None
            peak = equity
        elif underwater_since is None:
            underwater_since = previous if previous is not None else day
        previous = day
    if underwater_since is not None and previous is not None:
        longest = max(longest, previous - underwater_since)
    return longest


def circular_block_bootstrap_lcb(rows: Iterable[DailyReturn], *, block_days: int,
                                 resamples: int, seed: int,
                                 lower_percentile: float) -> float:
    """Bootstrap the arithmetic mean daily cash excess with circular blocks."""
    data = validate_daily_returns(rows)
    if block_days < 1 or resamples < 1:
        raise CarryRunnerError("bootstrap block and resample counts must be positive")
    if not 0 <= lower_percentile <= 100:
        raise CarryRunnerError("bootstrap percentile must be between zero and 100")
    excess = [row.cash_excess for row in data]
    count = len(excess)
    generator = random.Random(seed)
    samples: list[float] = []
    blocks = math.ceil(count / block_days)
    for _ in range(resamples):
        draw: list[float] = []
        for _ in range(blocks):
            start = generator.randrange(count)
            draw.extend(excess[(start + offset) % count] for offset in range(block_days))
        samples.append(math.fsum(draw[:count]) / count)
    samples.sort()
    if len(samples) == 1:
        return samples[0]
    position = (len(samples) - 1) * lower_percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return samples[lower]
    weight = position - lower
    return samples[lower] * (1 - weight) + samples[upper] * weight
