"""BarSource: closed candles only, integer-millisecond timestamps (STEP0_INTERFACE.md section 5).

Separate from VenuePort: replay / backtest read frozen CSVs (sha256-pinned in DATA_MANIFEST), testnet reads venue klines,
and the strategy never sees which. A Bar's close_ms is open_ms + tf_ms, the exclusive end of the candle (= Binance
kline closeTime + 1) and the DecisionKey.candle_close_ms of any decision taken on it. A bar is CLOSED at as_of_ms iff
close_ms <= as_of_ms; the forming candle is never returned (the adapter drops it, check_closed_bars refuses it).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from .values import check_decimal, check_int, check_ms, check_symbol, req
from .venue import ReadOutcome

DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True, kw_only=True)
class Bar:
    open_ms: int
    close_ms: int                # open_ms + tf_ms (exclusive end)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self):
        check_ms(self.open_ms, 'Bar.open_ms')
        check_ms(self.close_ms, 'Bar.close_ms')
        req(self.close_ms > self.open_ms, 'Bar.close_ms', 'after open_ms')
        for name in ('open', 'high', 'low', 'close'):
            check_decimal(getattr(self, name), f'Bar.{name}', positive=True)
        check_decimal(self.volume, 'Bar.volume', nonneg=True)
        req(self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close), 'Bar', 'OHLC order')


def check_tf(tf_ms):
    check_int(tf_ms, 'tf_ms', 60_000, DAY_MS)
    req(DAY_MS % tf_ms == 0, 'tf_ms', 'divides a day')


def check_closed_bars(bars, tf_ms: int, as_of_ms: int) -> tuple[Bar, ...]:
    """A BarSource answer is: Bars, oldest first, aligned to tf_ms, contiguous, every one closed at as_of_ms."""
    check_tf(tf_ms)
    check_ms(as_of_ms, 'as_of_ms')
    req(type(bars) is tuple and all(isinstance(b, Bar) for b in bars), 'bars', 'a tuple of Bar')
    for i, b in enumerate(bars):
        req(b.open_ms % tf_ms == 0 and b.close_ms == b.open_ms + tf_ms, f'bars[{i}]', f'not a {tf_ms} ms candle')
        req(i == 0 or b.open_ms == bars[i - 1].close_ms, f'bars[{i}]', 'gap or disorder')
        req(b.close_ms <= as_of_ms, f'bars[{i}]', f'forming at {as_of_ms}: closes at {b.close_ms}')
    return bars


@runtime_checkable
class BarSource(Protocol):
    def closed_bars(self, symbol: str, tf_ms: int, *, as_of_ms: int, limit: int) -> ReadOutcome:
        """value: the last `limit` closed bars at as_of_ms (fewer only when history is shorter), passing
        check_closed_bars. UNKNOWN when they cannot be read; a gapped or forming answer is never OK."""
        ...


def check_request(symbol, tf_ms, as_of_ms, limit):
    """Argument check every BarSource runs before reading."""
    check_symbol(symbol, 'symbol')
    check_tf(tf_ms)
    check_ms(as_of_ms, 'as_of_ms')
    check_int(limit, 'limit', 1, 1500)
