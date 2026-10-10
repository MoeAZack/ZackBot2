"""TestnetBarSource: the frozen newcore.ports.BarSource over REST klines on the pinned testnet host.

closed_bars(symbol, tf_ms, *, as_of_ms, limit) -> ReadOutcome[tuple[Bar]]:
- CLOSED candles only. The cutoff is min(as_of_ms, server now) where server now comes from the injected server-aligned
  clock (the OffsetClock): a caller cannot make a forming candle "closed" by passing a future as_of_ms. A bar is closed
  iff close_ms <= cutoff; the forming candle is dropped, never returned.
- close_ms = Binance closeTime + 1 (= open_ms + tf_ms, the exclusive end; the DecisionKey candle_close_ms). Decimal
  prices, integer UTC ms. A kline whose closeTime + 1 != open + tf, or whose open is off the tf grid, is UNKNOWN.
- Gaps are typed, never skipped: if the closed window is not contiguous the answer is UNKNOWN detail 'gap' and
  last_gaps holds BarGap(after_close_ms, next_open_ms, missing) records. Duplicates with different content, or
  out-of-order candles, are UNKNOWN too (a duplicate with identical content across a page seam is merged).
- Bounded paging for warm-up: pages of PAGE_LIMIT walk backwards from the cutoff (endTime) until `limit` closed bars
  are held or the venue has no older history; at most ceil(limit / PAGE_LIMIT) + 1 requests. Any page that is not OK
  makes the whole answer not OK (never a partial history).
"""
from dataclasses import dataclass

from newcore.ports import bars as B
from newcore.ports import venue as P
from newcore.ports.values import PortValueError

from .outcomes import ReadKind as TR

INTERVALS = {60_000: '1m', 180_000: '3m', 300_000: '5m', 900_000: '15m', 1_800_000: '30m', 3_600_000: '1h',
             7_200_000: '2h', 14_400_000: '4h', 21_600_000: '6h', 28_800_000: '8h', 43_200_000: '12h',
             86_400_000: '1d'}
PAGE_LIMIT = 500
WARMUP_EMA200_4H = 400


@dataclass(frozen=True)
class BarGap:
    after_close_ms: int          # the last bar before the hole closes here
    next_open_ms: int            # the next bar opens here
    missing: int                 # whole candles missing


def find_gaps(bars, tf_ms):
    return tuple(BarGap(a.close_ms, b.open_ms, (b.open_ms - a.close_ms) // tf_ms)
                 for a, b in zip(bars, bars[1:]) if b.open_ms != a.close_ms)


class TestnetBarSource:
    __test__ = False

    def __init__(self, transport, server_clock, *, page_limit=PAGE_LIMIT):
        if not callable(server_clock):
            raise PortValueError('server_clock', 'callable() -> int ms (the server-aligned OffsetClock)')
        if type(page_limit) is not int or not 1 <= page_limit <= 1500:
            raise PortValueError('page_limit', 'an int in 1..1500')
        self._t, self._clock, self._page = transport, server_clock, page_limit
        self.last_gaps = ()

    def _unknown(self, now, detail):
        return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=now, detail=detail)

    def closed_bars(self, symbol, tf_ms, *, as_of_ms, limit):
        B.check_request(symbol, tf_ms, as_of_ms, limit)
        if tf_ms not in INTERVALS:
            raise PortValueError('tf_ms', f'no Binance interval for {tf_ms} ms')
        self.last_gaps = ()
        try:
            now = self._clock()
            if type(now) is not int or now <= 0:
                raise ValueError('clock')
        except Exception:                                 # noqa: BLE001 - an unusable clock is an UNKNOWN read
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=1_000_000_000_000, detail='clock')
        cutoff = min(as_of_ms, now)                     # a future as_of can never admit the forming candle
        interval = INTERVALS[tf_ms]
        by_open = {}
        end_ms = cutoff - 1                             # klines whose open <= end_ms
        for _ in range(-(-limit // self._page) + 1):
            out = self._t.klines(symbol, interval, end_ms=end_ms, limit=self._page)
            if out.kind is not TR.OK:
                if out.kind is TR.REJECTED:
                    return P.ReadOutcome(kind=P.ReadKind.REJECTED, observed_at_ms=now,
                                         error_code=out.error.code if isinstance(out.error.code, int) else -1,
                                         detail=out.error.category.value)
                return self._unknown(now, out.unknown_reason or 'unknown')
            page = out.value
            for k in page:
                if k.open_time_ms % tf_ms or k.close_time_ms + 1 != k.open_time_ms + tf_ms:
                    return self._unknown(now, 'misaligned_candle')
                if k.open_time_ms > end_ms:
                    return self._unknown(now, 'candle_after_end_time')
                try:
                    bar = B.Bar(open_ms=k.open_time_ms, close_ms=k.close_time_ms + 1, open=k.open, high=k.high,
                                low=k.low, close=k.close, volume=k.volume)
                except PortValueError:
                    return self._unknown(now, 'unrepresentable')
                prev = by_open.get(bar.open_ms)
                if prev is not None and prev != bar:
                    return self._unknown(now, 'conflicting_duplicate')
                by_open[bar.open_ms] = bar
            closed = [b for b in by_open.values() if b.close_ms <= cutoff]
            if len(closed) >= limit or len(page) < self._page:
                break
            end_ms = min(by_open) - 1
        else:
            if len([b for b in by_open.values() if b.close_ms <= cutoff]) < limit:
                return self._unknown(now, 'paging_bound')
        bars = tuple(sorted((b for b in by_open.values() if b.close_ms <= cutoff), key=lambda b: b.open_ms))[-limit:]
        gaps = find_gaps(bars, tf_ms)
        if gaps:
            self.last_gaps = gaps
            return self._unknown(now, 'gap')
        if not bars or bars[-1].close_ms != cutoff // tf_ms * tf_ms:
            return self._unknown(now, 'stale')        # the newest CLOSED candle must be the one just before cutoff
        try:
            B.check_closed_bars(bars, tf_ms, cutoff)
        except PortValueError:
            return self._unknown(now, 'not_closed_bars')
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=now, value=bars)
