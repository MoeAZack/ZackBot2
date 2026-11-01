"""Deterministic reporting primitives for the signed CARRY-01 history study.

The archive reader and the portfolio simulator are deliberately separate.  This
module contains only result math whose inputs are already-causal observations.
It never downloads data, reads an exchange account, or places an order.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

YEAR_DAYS = 365.25
HOUR_MS = 3_600_000
FUNDING_DELAY_MS = 300_000


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


@dataclass(frozen=True)
class HourlyBar:
    """One already-validated hourly bar, usable only after its close."""

    open_time: int
    close_time: int
    close: float
    high: float

    def validate(self) -> None:
        if isinstance(self.open_time, bool) or isinstance(self.close_time, bool):
            raise CarryRunnerError("bar timestamps must be integers")
        if not isinstance(self.open_time, int) or not isinstance(self.close_time, int):
            raise CarryRunnerError("bar timestamps must be integers")
        if self.open_time % HOUR_MS or self.close_time != self.open_time + HOUR_MS - 1:
            raise CarryRunnerError("bar must occupy one canonical aligned hour")
        close = _finite(self.close, "bar close")
        high = _finite(self.high, "bar high")
        if close <= 0 or high < close:
            raise CarryRunnerError("bar prices violate positive OHLC ordering")

    @property
    def available_time(self) -> int:
        self.validate()
        return self.close_time + 1


@dataclass(frozen=True)
class FundingEvent:
    symbol: str
    funding_time: int
    rate: float

    def validate(self) -> None:
        if not self.symbol:
            raise CarryRunnerError("funding symbol is empty")
        if isinstance(self.funding_time, bool) or not isinstance(self.funding_time, int):
            raise CarryRunnerError("funding time must be an integer")
        _finite(self.rate, "funding rate")

    @property
    def available_time(self) -> int:
        self.validate()
        return self.funding_time + FUNDING_DELAY_MS


@dataclass(frozen=True)
class CausalEvent:
    available_time: int
    kind: str
    symbol: str
    source_time: int
    value: float


@dataclass(frozen=True)
class BasketObservation:
    """Synchronous BTC/ETH close used to mark and, if needed, rebalance."""

    open_time: int
    spot_close: Mapping[str, float]
    mark_close: Mapping[str, float]
    mark_high: Mapping[str, float]

    @property
    def available_time(self) -> int:
        return self.open_time + HOUR_MS

    def validate(self) -> None:
        if isinstance(self.open_time, bool) or not isinstance(self.open_time, int):
            raise CarryRunnerError("observation time must be an integer")
        if self.open_time % HOUR_MS:
            raise CarryRunnerError("observation must be hourly aligned")
        symbols = set(self.spot_close)
        if not symbols or set(self.mark_close) != symbols or set(self.mark_high) != symbols:
            raise CarryRunnerError("observation legs must cover identical symbols")
        for symbol in symbols:
            spot = _finite(self.spot_close[symbol], "spot close")
            mark = _finite(self.mark_close[symbol], "mark close")
            high = _finite(self.mark_high[symbol], "mark high")
            if spot <= 0 or mark <= 0 or high < mark:
                raise CarryRunnerError("observation prices violate positive OHLC ordering")


@dataclass(frozen=True)
class FundingCredit:
    """A validated funding event paired to its exact strict-prior mark close."""

    event: FundingEvent
    settlement_mark: float

    def validate(self) -> None:
        self.event.validate()
        if _finite(self.settlement_mark, "funding mark") <= 0:
            raise CarryRunnerError("funding mark must be positive")


@dataclass(frozen=True)
class FoldReplay:
    status: str
    starting_equity: float
    ending_equity: float | None
    equity_curve: tuple[tuple[int, float], ...]
    funding_pnl: float
    price_pnl: float
    trading_cost: float
    funding_events: int
    rebalances: int
    liquidated_symbols: tuple[str, ...] = ()


def required_funding_mark_open(funding_time: int) -> int:
    """Return the exact hour whose close is strictly before nominal settlement."""
    if isinstance(funding_time, bool) or not isinstance(funding_time, int):
        raise CarryRunnerError("funding time must be an integer")
    return ((funding_time - HOUR_MS) // HOUR_MS) * HOUR_MS


def funding_settlement_mark(event: FundingEvent,
                            mark_bars: Mapping[int, HourlyBar]) -> float:
    """Resolve only the signed exact prior mark bar; never substitute an older bar."""
    event.validate()
    required = required_funding_mark_open(event.funding_time)
    try:
        bar = mark_bars[required]
    except KeyError as exc:
        raise CarryRunnerError("funding event is missing its exact prior mark bar") from exc
    bar.validate()
    if bar.open_time != required or bar.close_time >= event.funding_time:
        raise CarryRunnerError("funding mark is not strictly earlier than settlement")
    return bar.close


def causal_event_order(spot_bars: Mapping[str, Sequence[HourlyBar]],
                       mark_bars: Mapping[str, Sequence[HourlyBar]],
                       funding: Sequence[FundingEvent]) -> list[CausalEvent]:
    """Build an availability-time ledger without reading or calculating a return."""
    if set(spot_bars) != set(mark_bars):
        raise CarryRunnerError("spot and mark symbol sets differ")
    events: list[CausalEvent] = []
    for kind, series_by_symbol in (("spot", spot_bars), ("mark", mark_bars)):
        for symbol, series in sorted(series_by_symbol.items()):
            previous = -1
            for bar in series:
                bar.validate()
                if bar.open_time <= previous:
                    raise CarryRunnerError(f"{kind} bars must be unique and chronological")
                previous = bar.open_time
                events.append(CausalEvent(bar.available_time, kind, symbol,
                                          bar.open_time, bar.close))
    for event in funding:
        event.validate()
        if event.symbol not in spot_bars:
            raise CarryRunnerError("funding event names an unknown symbol")
        events.append(CausalEvent(event.available_time, "funding", event.symbol,
                                  event.funding_time, event.rate))
    priority = {"spot": 0, "mark": 1, "funding": 2}
    events.sort(key=lambda item: (item.available_time, priority[item.kind],
                                  item.symbol, item.source_time))
    return events


@dataclass(frozen=True)
class CostModel:
    """Per-fill costs expressed in basis points of each leg's notional."""

    spot_fee_bps: float = 10.0
    perpetual_fee_bps: float = 5.0
    spot_slippage_bps: float = 5.0
    perpetual_slippage_bps: float = 5.0
    multiplier: float = 1.0

    def validate(self) -> None:
        for name in ("spot_fee_bps", "perpetual_fee_bps", "spot_slippage_bps",
                     "perpetual_slippage_bps"):
            if _finite(getattr(self, name), name) < 0:
                raise CarryRunnerError(f"{name} cannot be negative")
        if _finite(self.multiplier, "cost multiplier") <= 0:
            raise CarryRunnerError("cost multiplier must be positive")

    def fill_cost(self, quantity: float, spot: float, mark: float) -> float:
        self.validate()
        quantity = abs(_finite(quantity, "quantity"))
        spot = _finite(spot, "spot price")
        mark = _finite(mark, "mark price")
        if spot <= 0 or mark <= 0:
            raise CarryRunnerError("fill prices must be positive")
        spot_rate = (self.spot_fee_bps + self.spot_slippage_bps) / 10_000
        perp_rate = (self.perpetual_fee_bps + self.perpetual_slippage_bps) / 10_000
        return quantity * (spot * spot_rate + mark * perp_rate) * self.multiplier


@dataclass
class SleeveState:
    """One matched long-spot/short-perpetual sleeve between rebalances."""

    quantity: float
    entry_spot: float
    entry_mark: float
    segment_equity: float
    futures_collateral: float
    funding_pnl: float = 0.0
    price_pnl_realized: float = 0.0
    trading_cost: float = 0.0
    funding_events: int = 0

    @classmethod
    def open(cls, equity: float, spot: float, mark: float, costs: CostModel,
             *, spot_fraction: float = 0.75) -> "SleeveState":
        equity = _finite(equity, "sleeve equity")
        spot = _finite(spot, "spot price")
        mark = _finite(mark, "mark price")
        if equity <= 0 or spot <= 0 or mark <= 0:
            raise CarryRunnerError("opening equity and prices must be positive")
        if not 0 < spot_fraction < 1:
            raise CarryRunnerError("spot fraction must be between zero and one")
        quantity = equity * spot_fraction / spot
        opening_cost = costs.fill_cost(quantity, spot, mark)
        if opening_cost >= equity:
            raise CarryRunnerError("opening cost consumes the sleeve")
        return cls(quantity=quantity, entry_spot=spot, entry_mark=mark,
                   segment_equity=equity - opening_cost,
                   futures_collateral=equity * (1 - spot_fraction) - opening_cost,
                   trading_cost=opening_cost)

    def _prices(self, spot: float, mark: float) -> tuple[float, float]:
        spot = _finite(spot, "spot price")
        mark = _finite(mark, "mark price")
        if spot <= 0 or mark <= 0:
            raise CarryRunnerError("marking prices must be positive")
        return spot, mark

    def spot_pnl(self, spot: float) -> float:
        spot = _finite(spot, "spot price")
        return self.quantity * (spot - self.entry_spot)

    def short_pnl(self, mark: float) -> float:
        mark = _finite(mark, "mark price")
        return self.quantity * (self.entry_mark - mark)

    def equity(self, spot: float, mark: float) -> float:
        spot, mark = self._prices(spot, mark)
        return self.segment_equity + self.spot_pnl(spot) + self.short_pnl(mark) + self.funding_pnl

    def margin_balance(self, mark: float) -> float:
        mark = _finite(mark, "mark price")
        if mark <= 0:
            raise CarryRunnerError("mark price must be positive")
        return self.futures_collateral + self.short_pnl(mark) + self.funding_pnl

    def leverage(self, mark: float) -> float:
        balance = self.margin_balance(mark)
        return math.inf if balance <= 0 else self.quantity * mark / balance

    def margin_headroom(self, mark: float, *, maintenance_rate: float = 0.005) -> float:
        maintenance_rate = _finite(maintenance_rate, "maintenance rate")
        if not 0 <= maintenance_rate < 1:
            raise CarryRunnerError("maintenance rate must be in [0, 1)")
        return self.margin_balance(mark) - self.quantity * mark * maintenance_rate

    def liquidates_at_high(self, high: float, *, maintenance_rate: float = 0.005) -> bool:
        high = _finite(high, "mark high")
        if high <= 0:
            raise CarryRunnerError("mark high must be positive")
        return self.margin_headroom(high, maintenance_rate=maintenance_rate) <= 0

    def credit_funding(self, rate: float, settlement_mark: float, *, received_divisor: float = 1.0,
                       paid_multiplier: float = 1.0) -> float:
        rate = _finite(rate, "funding rate")
        settlement_mark = _finite(settlement_mark, "funding mark")
        received_divisor = _finite(received_divisor, "received funding divisor")
        paid_multiplier = _finite(paid_multiplier, "paid funding multiplier")
        if settlement_mark <= 0 or received_divisor <= 0 or paid_multiplier <= 0:
            raise CarryRunnerError("funding controls and mark must be positive")
        raw = self.quantity * settlement_mark * rate
        amount = raw / received_divisor if raw >= 0 else raw * paid_multiplier
        self.funding_pnl += amount
        self.funding_events += 1
        return amount

    def close(self, spot: float, mark: float, costs: CostModel) -> float:
        """Return equity after closing both legs and accumulate attribution."""
        spot, mark = self._prices(spot, mark)
        price_pnl = self.spot_pnl(spot) + self.short_pnl(mark)
        closing_cost = costs.fill_cost(self.quantity, spot, mark)
        self.price_pnl_realized += price_pnl
        self.trading_cost += closing_cost
        return self.segment_equity + price_pnl + self.funding_pnl - closing_cost


@dataclass
class BasketState:
    """Equal-weight sleeves with atomic whole-portfolio rebalancing."""

    sleeves: dict[str, SleeveState]
    costs: CostModel
    starting_equity: float
    archived_funding_pnl: float = 0.0
    archived_price_pnl: float = 0.0
    archived_trading_cost: float = 0.0
    archived_funding_events: int = 0
    rebalances: int = 0

    @classmethod
    def open(cls, equity: float, prices: dict[str, tuple[float, float]],
             costs: CostModel) -> "BasketState":
        equity = _finite(equity, "basket equity")
        if equity <= 0:
            raise CarryRunnerError("basket equity must be positive")
        if len(prices) < 1 or any(not isinstance(symbol, str) or not symbol for symbol in prices):
            raise CarryRunnerError("basket needs named price sleeves")
        sleeve_equity = equity / len(prices)
        sleeves = {symbol: SleeveState.open(sleeve_equity, spot, mark, costs)
                   for symbol, (spot, mark) in sorted(prices.items())}
        return cls(sleeves=sleeves, costs=costs, starting_equity=equity)

    def equity(self, prices: dict[str, tuple[float, float]]) -> float:
        self._require_prices(prices)
        return math.fsum(self.sleeves[symbol].equity(*prices[symbol])
                         for symbol in self.sleeves)

    def _require_prices(self, prices: dict[str, tuple[float, float]]) -> None:
        if set(prices) != set(self.sleeves):
            raise CarryRunnerError("basket prices must cover every sleeve exactly")

    def rebalances_required(self, prices: dict[str, tuple[float, float]], *,
                            minimum: float = 2.0, maximum: float = 5.0) -> bool:
        self._require_prices(prices)
        minimum = _finite(minimum, "minimum leverage")
        maximum = _finite(maximum, "maximum leverage")
        if not 0 < minimum < maximum:
            raise CarryRunnerError("leverage band is invalid")
        return any(not minimum <= sleeve.leverage(prices[symbol][1]) <= maximum
                   for symbol, sleeve in self.sleeves.items())

    def liquidated_symbols(self, mark_highs: dict[str, float], *,
                           maintenance_rate: float = 0.005) -> list[str]:
        if set(mark_highs) != set(self.sleeves):
            raise CarryRunnerError("mark highs must cover every sleeve exactly")
        return [symbol for symbol, sleeve in self.sleeves.items()
                if sleeve.liquidates_at_high(mark_highs[symbol],
                                             maintenance_rate=maintenance_rate)]

    def credit_funding(self, symbol: str, rate: float, settlement_mark: float, *,
                       received_divisor: float = 1.0, paid_multiplier: float = 1.0) -> float:
        try:
            sleeve = self.sleeves[symbol]
        except KeyError as exc:
            raise CarryRunnerError(f"unknown funding sleeve: {symbol}") from exc
        return sleeve.credit_funding(rate, settlement_mark,
                                     received_divisor=received_divisor,
                                     paid_multiplier=paid_multiplier)

    def rebalance(self, prices: dict[str, tuple[float, float]]) -> float:
        """Close every sleeve, split net equity equally, and reopen all sleeves."""
        self._require_prices(prices)
        closed_equity: dict[str, float] = {}
        for symbol, sleeve in self.sleeves.items():
            closed_equity[symbol] = sleeve.close(*prices[symbol], self.costs)
            self.archived_funding_pnl += sleeve.funding_pnl
            self.archived_price_pnl += sleeve.price_pnl_realized
            self.archived_trading_cost += sleeve.trading_cost
            self.archived_funding_events += sleeve.funding_events
        equity = math.fsum(closed_equity.values())
        if equity <= 0:
            raise CarryRunnerError("rebalance consumes the basket")
        sleeve_equity = equity / len(self.sleeves)
        self.sleeves = {
            symbol: SleeveState.open(sleeve_equity, *prices[symbol], self.costs)
            for symbol in sorted(self.sleeves)
        }
        self.rebalances += 1
        return self.equity(prices)

    @property
    def funding_pnl(self) -> float:
        return self.archived_funding_pnl + math.fsum(
            sleeve.funding_pnl for sleeve in self.sleeves.values())

    @property
    def trading_cost(self) -> float:
        return self.archived_trading_cost + math.fsum(
            sleeve.trading_cost for sleeve in self.sleeves.values())

    @property
    def funding_events(self) -> int:
        return self.archived_funding_events + sum(
            sleeve.funding_events for sleeve in self.sleeves.values())

    @property
    def price_pnl(self) -> float:
        return self.archived_price_pnl + math.fsum(
            sleeve.price_pnl_realized for sleeve in self.sleeves.values())

    def close(self, prices: dict[str, tuple[float, float]]) -> float:
        self._require_prices(prices)
        total = 0.0
        for symbol, sleeve in self.sleeves.items():
            total += sleeve.close(*prices[symbol], self.costs)
        return total


def replay_fold(observations: Sequence[BasketObservation], funding: Sequence[FundingCredit],
                *, starting_equity: float = 10_000.0, costs: CostModel | None = None,
                minimum_leverage: float = 2.0, maximum_leverage: float = 5.0,
                maintenance_rate: float = 0.005, rebalance_latency_hours: int = 0,
                received_funding_divisor: float = 1.0,
                paid_funding_multiplier: float = 1.0) -> FoldReplay:
    """Replay one fresh-start fold without crossing a boundary or inventing data."""
    if len(observations) < 2:
        raise CarryRunnerError("fold needs at least two common observations")
    if rebalance_latency_hours not in (0, 1):
        raise CarryRunnerError("rebalance latency must be zero or one hour")
    previous = -1
    expected_symbols: set[str] | None = None
    for observation in observations:
        observation.validate()
        if observation.open_time <= previous:
            raise CarryRunnerError("observations must be unique and chronological")
        symbols = set(observation.spot_close)
        if expected_symbols is None:
            expected_symbols = symbols
        elif symbols != expected_symbols:
            raise CarryRunnerError("observation symbol set changed inside a fold")
        previous = observation.open_time
    credits = list(funding)
    previous_available = -1
    for credit in credits:
        credit.validate()
        if credit.event.available_time <= previous_available:
            raise CarryRunnerError("funding credits must be unique and chronological")
        if credit.event.symbol not in (expected_symbols or set()):
            raise CarryRunnerError("funding credit names an unknown sleeve")
        previous_available = credit.event.available_time

    costs = costs or CostModel()
    first = observations[0]
    opening_prices = {symbol: (first.spot_close[symbol], first.mark_close[symbol])
                      for symbol in sorted(expected_symbols or ())}
    basket = BasketState.open(starting_equity, opening_prices, costs)
    curve: list[tuple[int, float]] = [(first.available_time, basket.equity(opening_prices))]
    funding_index = 0
    pending_rebalance = False

    for observation in observations[1:]:
        while (funding_index < len(credits)
               and credits[funding_index].event.available_time <= observation.available_time):
            credit = credits[funding_index]
            if credit.event.available_time > first.available_time:
                basket.credit_funding(
                    credit.event.symbol, credit.event.rate, credit.settlement_mark,
                    received_divisor=received_funding_divisor,
                    paid_multiplier=paid_funding_multiplier)
            funding_index += 1
        liquidated = basket.liquidated_symbols(dict(observation.mark_high),
                                               maintenance_rate=maintenance_rate)
        prices = {symbol: (observation.spot_close[symbol], observation.mark_close[symbol])
                  for symbol in sorted(expected_symbols or ())}
        marked_equity = basket.equity(prices)
        curve.append((observation.available_time, max(0.0, marked_equity)))
        if liquidated or marked_equity <= 0:
            return FoldReplay("LIQUIDATED", starting_equity, None, tuple(curve),
                              basket.funding_pnl, basket.price_pnl, basket.trading_cost,
                              basket.funding_events, basket.rebalances,
                              tuple(sorted(liquidated)))
        required = basket.rebalances_required(prices, minimum=minimum_leverage,
                                              maximum=maximum_leverage)
        if pending_rebalance or (required and rebalance_latency_hours == 0):
            rebalanced_equity = basket.rebalance(prices)
            curve[-1] = (observation.available_time, rebalanced_equity)
            pending_rebalance = False
        elif required:
            pending_rebalance = True

    last = observations[-1]
    final_prices = {symbol: (last.spot_close[symbol], last.mark_close[symbol])
                    for symbol in sorted(expected_symbols or ())}
    ending = basket.close(final_prices)
    curve.append((last.available_time, ending))
    return FoldReplay("COMPLETE", starting_equity, ending, tuple(curve),
                      basket.funding_pnl, basket.price_pnl, basket.trading_cost,
                      basket.funding_events, basket.rebalances)


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
