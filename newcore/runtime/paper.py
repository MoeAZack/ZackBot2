"""RT-02 PAPER venue: a deterministic, in-memory NC-03 ``VenuePort`` for ``paper_sim`` runs.  No network, no keys.

It behaves like Binance USD-M in hedge mode for what NEWCORE sends:

* ``submit_market`` fills at once at the injected mark price; a reduce order larger than the position is rejected
  (-2022), like reduce-only;
* ``submit_stop`` rests a reduce-only STOP_MARKET on the algo route; the classic route answers -4120 (Binance moved
  conditional orders to the algo service);
* ``tick()`` triggers resting stops whose mark price crossed (LONG: mark <= stop, SHORT: mark >= stop) and fills them
  at the mark, never for more than the open position;
* ``positions`` / ``open_orders`` / ``fills`` / ``query`` / ``cancel`` read and change this book only.

Every answer uses the frozen ``newcore.ports.venue`` outcome types, so the runtime, NC-04 and REC-02 see exactly what
they would see from the testnet adapter.  Prices and quantities are Decimals; nothing here reads a clock or a random
source except the injected ``clock`` and ``marks``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from newcore.ports import venue as P

ZERO = Decimal('0')


@dataclass(slots=True)
class _Stop:
    ref: P.OrderRef
    exchange_order_id: str
    position_side: str
    qty: Decimal
    stop_price: Decimal
    status: str = 'NEW'
    executed: Decimal = ZERO


class PaperVenue:
    """In-memory hedge-mode book.  Thread-safe: NC-04 calls it from runner threads, the owner calls ``tick``."""

    def __init__(self, *, clock: Callable[[], int], marks: Callable[[str], Decimal]):
        if not callable(clock) or not callable(marks):
            raise ValueError('paper_venue_invalid')
        self._clock, self._marks = clock, marks
        self._lock = threading.Lock()
        self._positions: dict[tuple[str, str], Decimal] = {}
        self._stops: dict[str, _Stop] = {}            # client id -> stop (open or final)
        self._orders: dict[str, P.OrderOutcome] = {}  # client id -> last outcome of a market order
        self._fills: dict[str, list[P.VenueFill]] = {}
        self._next_order = 1000
        self._next_trade = 5000

    # ------------------------------------------------------------------------------------------------ helpers
    def _eid(self) -> str:
        self._next_order += 1
        return str(self._next_order)

    def _fill(self, eid: str, symbol: str, side: str, qty: Decimal, price: Decimal, at: int) -> None:
        self._next_trade += 1
        self._fills.setdefault(eid, []).append(P.VenueFill(
            trade_id=str(self._next_trade), exchange_order_id=eid, symbol=symbol, position_side=side, qty=qty,
            price=price, fee=ZERO, fee_asset='USDT', realized_pnl=ZERO, maker=False, at_ms=at))

    def _mark(self, symbol: str) -> Decimal:
        mark = self._marks(symbol)
        if type(mark) is not Decimal or not mark > 0:
            raise ValueError('mark_invalid')
        return mark

    # ------------------------------------------------------------------------------------------------ orders
    def submit_market(self, order: P.MarketOrder) -> P.OrderOutcome:
        with self._lock:
            at, ref = self._clock(), order.ref
            if ref.client_id in self._orders or ref.client_id in self._stops:
                return P.OrderOutcome(kind=P.OutcomeKind.REJECTED, ref=ref, observed_at_ms=at, error_code=-4116,
                                      detail='duplicate')
            key = (ref.symbol, order.position_side)
            held = self._positions.get(key, ZERO)
            if order.reduce and order.qty > held:
                out = P.OrderOutcome(kind=P.OutcomeKind.REJECTED, ref=ref, observed_at_ms=at, error_code=-2022,
                                     detail='venue_rejected')
                self._orders[ref.client_id] = out
                return out
            price, eid = self._mark(ref.symbol), self._eid()
            self._positions[key] = held - order.qty if order.reduce else held + order.qty
            self._fill(eid, ref.symbol, order.position_side, order.qty, price, at)
            out = P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=ref, observed_at_ms=at, status='FILLED',
                                 exchange_order_id=eid, executed_qty=order.qty, avg_price=price)
            self._orders[ref.client_id] = out
            return out

    def submit_stop(self, order: P.StopOrder) -> P.OrderOutcome:
        with self._lock:
            at, ref = self._clock(), order.ref
            if ref.route != 'algo':
                return P.OrderOutcome(kind=P.OutcomeKind.REJECTED, ref=ref, observed_at_ms=at, error_code=-4120,
                                      detail='algo_route')
            if ref.client_id in self._stops or ref.client_id in self._orders:
                return P.OrderOutcome(kind=P.OutcomeKind.REJECTED, ref=ref, observed_at_ms=at, error_code=-4116,
                                      detail='duplicate')
            stop = _Stop(ref, self._eid(), order.position_side, order.qty, order.stop_price)
            self._stops[ref.client_id] = stop
            return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, ref=ref, observed_at_ms=at, status='NEW',
                                  exchange_order_id=stop.exchange_order_id)

    def cancel(self, ref: P.OrderRef) -> P.OrderOutcome:
        with self._lock:
            at, stop = self._clock(), self._stops.get(ref.client_id)
            if stop is None or stop.status != 'NEW':
                return P.OrderOutcome(kind=P.OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=at, error_code=-2011)
            stop.status = 'CANCELED'
            return self._stop_outcome(stop, at)

    def query(self, ref: P.OrderRef) -> P.OrderOutcome:
        with self._lock:
            at = self._clock()
            stop = self._stops.get(ref.client_id)
            if stop is not None:
                return self._stop_outcome(stop, at)
            known = self._orders.get(ref.client_id)
            if known is None or known.kind is not P.OutcomeKind.FINAL:
                return P.OrderOutcome(kind=P.OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=at, error_code=-2013)
            return known

    @staticmethod
    def _stop_outcome(stop: _Stop, at: int) -> P.OrderOutcome:
        if stop.status == 'NEW':
            return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, ref=stop.ref, observed_at_ms=at, status='NEW',
                                  exchange_order_id=stop.exchange_order_id)
        return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=stop.ref, observed_at_ms=at, status=stop.status,
                              exchange_order_id=stop.exchange_order_id, executed_qty=stop.executed,
                              avg_price=None if stop.executed == 0 else stop.stop_price)

    # ------------------------------------------------------------------------------------------------ market
    def tick(self) -> tuple[str, ...]:
        """Trigger every resting stop whose mark crossed.  Returns the triggered client ids."""
        with self._lock:
            at, fired = self._clock(), []
            for cid, stop in self._stops.items():
                if stop.status != 'NEW':
                    continue
                mark = self._mark(stop.ref.symbol)
                crossed = mark <= stop.stop_price if stop.position_side == 'LONG' else mark >= stop.stop_price
                if not crossed:
                    continue
                key = (stop.ref.symbol, stop.position_side)
                qty = min(stop.qty, self._positions.get(key, ZERO))
                stop.status = 'FILLED' if qty == stop.qty else 'EXPIRED'
                if qty > 0:
                    stop.executed = qty
                    self._positions[key] -= qty
                    self._fill(stop.exchange_order_id, stop.ref.symbol, stop.position_side, qty, mark, at)
                fired.append(cid)
            return tuple(fired)

    # ------------------------------------------------------------------------------------------------ reads
    def positions(self, symbol: str | None = None) -> P.ReadOutcome:
        with self._lock:
            rows = tuple(P.VenuePosition(symbol=s, side=side, qty=qty, entry_price=ZERO)
                         for (s, side), qty in sorted(self._positions.items()) if symbol in (None, s))
            return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=self._clock(), value=rows)

    def open_orders(self, symbol: str | None = None) -> P.ReadOutcome:
        with self._lock:
            rows = tuple(P.VenueOrder(ref=s.ref, exchange_order_id=s.exchange_order_id, position_side=s.position_side,
                                      reduce=True, order_type='STOP_MARKET', status='NEW', qty=s.qty,
                                      close_position=False, stop_price=s.stop_price)
                         for s in self._stops.values() if s.status == 'NEW' and symbol in (None, s.ref.symbol))
            return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=self._clock(), value=rows)

    def fills(self, symbol: str, exchange_order_id: str) -> P.ReadOutcome:
        with self._lock:
            rows = tuple(f for f in self._fills.get(exchange_order_id, ()) if f.symbol == symbol)
            return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=self._clock(), value=rows)


__all__ = ['PaperVenue']
